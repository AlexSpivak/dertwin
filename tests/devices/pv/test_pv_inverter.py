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

    inverter.grid_voltage = 100  # under-voltage fault
    inverter.step(dc_input_w=4000, dt=1.0)

    assert inverter.active_power_w == 0
    assert inverter.fault_code != 0


def test_thermal_rise_under_load():
    inverter = PVInverterModel(rated_ac_power_w=5000)

    initial_temp = inverter.temperature_c

    for _ in range(100):
        inverter.step(dc_input_w=4000, dt=1.0)

    assert inverter.temperature_c > initial_temp