import math
import pytest
from dertwin.devices.pv.inverter import PVInverterModel


def test_clipping_to_rated_power():
    inverter = PVInverterModel(rated_ac_power_w=5000)
    inverter.step(dc_input_w=10000, dt=1.0)
    assert inverter.active_power_w <= 5000


def test_curtailment_limits_output():
    inverter = PVInverterModel(rated_ac_power_w=5000)
    inverter.active_power_rate = 50  # %
    inverter.step(dc_input_w=5000, dt=1.0)
    assert inverter.active_power_w <= 2500


def test_reactive_power_from_pf():
    inverter = PVInverterModel(rated_ac_power_w=5000)
    inverter.power_factor_setpoint = 0.9
    inverter.step(dc_input_w=4000, dt=1.0)
    assert abs(inverter.reactive_power_var) > 0


def test_grid_voltage_fault():
    inverter = PVInverterModel(rated_ac_power_w=5000)
    inverter.grid_voltage = 100  # under-voltage
    inverter.step(dc_input_w=4000, dt=1.0)
    assert inverter.active_power_w == 0
    assert inverter.fault_code != 0


def test_thermal_rise_under_load():
    inverter = PVInverterModel(rated_ac_power_w=5000)
    initial_temp = inverter.temperature_c

    for _ in range(100):
        inverter.step(dc_input_w=4000, dt=1.0)

    assert inverter.temperature_c > initial_temp


def test_no_power_at_zero_dc_input():
    inverter = PVInverterModel(rated_ac_power_w=5000)
    inverter.step(dc_input_w=0, dt=1.0)
    assert inverter.active_power_w == pytest.approx(0.0, abs=1e-6)


def test_dc_ac_efficiency_applied():
    inverter = PVInverterModel(rated_ac_power_w=10000, efficiency=0.97)
    inverter.step(dc_input_w=4000, dt=1.0)
    # AC output should be DC * efficiency (if below rated and no curtailment)
    assert inverter.active_power_w == pytest.approx(4000 * 0.97, rel=1e-4)


def test_grid_frequency_fault():
    inverter = PVInverterModel(rated_ac_power_w=5000)
    inverter.grid_frequency = 60.0  # out of bounds (>53)
    inverter.step(dc_input_w=4000, dt=1.0)
    assert inverter.active_power_w == 0.0
    assert inverter.fault_code != 0


def test_grid_recovery_after_fault():
    """After grid returns to normal, inverter should produce power again."""
    inverter = PVInverterModel(rated_ac_power_w=5000)

    inverter.grid_voltage = 100  # trigger fault
    inverter.step(dc_input_w=4000, dt=1.0)
    assert inverter.active_power_w == 0.0

    inverter.grid_voltage = 230  # restore normal
    inverter.step(dc_input_w=4000, dt=1.0)
    assert inverter.active_power_w > 0.0
    assert inverter.fault_code == 0


def test_unity_pf_produces_zero_reactive():
    inverter = PVInverterModel(rated_ac_power_w=5000)
    inverter.power_factor_setpoint = 1.0
    inverter.step(dc_input_w=4000, dt=1.0)
    assert inverter.reactive_power_var == pytest.approx(0.0, abs=1e-6)


def test_apparent_power_consistent_with_components():
    inverter = PVInverterModel(rated_ac_power_w=5000)
    inverter.power_factor_setpoint = 0.9
    inverter.step(dc_input_w=4000, dt=1.0)

    expected = math.hypot(inverter.active_power_w, inverter.reactive_power_var)
    assert inverter.apparent_power() == pytest.approx(expected, rel=1e-6)


def test_full_curtailment_stops_output():
    inverter = PVInverterModel(rated_ac_power_w=5000)
    inverter.active_power_rate = 0.0
    inverter.step(dc_input_w=5000, dt=1.0)
    assert inverter.active_power_w == pytest.approx(0.0, abs=1e-6)


def test_temperature_capped_at_max():
    """Inverter temperature must not exceed 85°C regardless of load."""
    inverter = PVInverterModel(rated_ac_power_w=5000, efficiency=0.5)  # high loss

    for _ in range(10000):
        inverter.step(dc_input_w=5000, dt=1.0)

    assert inverter.temperature_c <= 85.0