import math
import pytest
from dertwin.devices.bess.inverter import InverterModel


def test_ramp_rate_limits_change():
    inverter = InverterModel(
        max_charge_kw=100,
        max_discharge_kw=100,
        ramp_rate_kw_per_s=10,
    )

    inverter.target_power = 100

    p1 = inverter.step(1.0)
    p2 = inverter.step(1.0)
    p3 = inverter.step(10.0)

    assert p1 == 10
    assert p2 == 20
    assert p3 == 100


def test_power_clamped_to_discharge_limit():
    inverter = InverterModel(
        max_charge_kw=50,
        max_discharge_kw=50,
        ramp_rate_kw_per_s=1000,
    )
    inverter.target_power = 100
    power = inverter.step(1.0)
    assert power == 50


def test_power_clamped_to_charge_limit():
    inverter = InverterModel(
        max_charge_kw=30,
        max_discharge_kw=50,
        ramp_rate_kw_per_s=1000,
    )
    inverter.target_power = -100  # request more charge than allowed
    power = inverter.step(1.0)
    assert power == pytest.approx(-30.0, rel=1e-6)


def test_reactive_and_apparent_power():
    inverter = InverterModel(
        max_charge_kw=100,
        max_discharge_kw=100,
        ramp_rate_kw_per_s=1000,
    )
    inverter.target_power = 10
    inverter.step(1.0)

    q = inverter.reactive_power()
    s = inverter.apparent_power()

    assert q == pytest.approx(1.0, rel=1e-6)
    assert s == pytest.approx(math.hypot(10.0, 1.0), rel=1e-6)


def test_zero_target_power_stays_at_zero():
    inverter = InverterModel(
        max_charge_kw=50,
        max_discharge_kw=50,
        ramp_rate_kw_per_s=10,
    )
    inverter.target_power = 0.0
    power = inverter.step(1.0)
    assert power == pytest.approx(0.0, abs=1e-9)


def test_ramp_down_from_positive():
    """Power should ramp down toward zero when target is reduced."""
    inverter = InverterModel(
        max_charge_kw=100,
        max_discharge_kw=100,
        ramp_rate_kw_per_s=10,
    )
    inverter.target_power = 100
    for _ in range(10):
        inverter.step(1.0)

    assert inverter.current_power == pytest.approx(100.0, rel=1e-6)

    inverter.target_power = 0.0
    p1 = inverter.step(1.0)
    p2 = inverter.step(1.0)

    assert p1 < 100.0
    assert p2 < p1


def test_target_power_setter_clamps_to_discharge_limit():
    inverter = InverterModel(
        max_charge_kw=50,
        max_discharge_kw=50,
        ramp_rate_kw_per_s=10,
    )
    inverter.target_power = 999
    assert inverter.target_power == pytest.approx(50.0, rel=1e-6)


def test_target_power_setter_clamps_to_charge_limit():
    inverter = InverterModel(
        max_charge_kw=30,
        max_discharge_kw=50,
        ramp_rate_kw_per_s=10,
    )
    inverter.target_power = -999
    assert inverter.target_power == pytest.approx(-30.0, rel=1e-6)


def test_set_grid_frequency():
    inverter = InverterModel(max_charge_kw=50, max_discharge_kw=50, ramp_rate_kw_per_s=10)
    inverter.set_grid_frequency(60.0)
    assert inverter.grid_frequency == 60.0


def test_set_grid_voltage():
    inverter = InverterModel(max_charge_kw=50, max_discharge_kw=50, ramp_rate_kw_per_s=10)
    inverter.set_grid_voltage(480.0)
    assert inverter.grid_voltage_ll == 480.0


def test_reactive_power_zero_at_zero_active():
    inverter = InverterModel(max_charge_kw=50, max_discharge_kw=50, ramp_rate_kw_per_s=1000)
    inverter.target_power = 0.0
    inverter.step(1.0)
    assert inverter.reactive_power() == pytest.approx(0.0, abs=1e-9)