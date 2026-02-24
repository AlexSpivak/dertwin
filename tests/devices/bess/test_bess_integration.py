import pytest
from dertwin.devices.bess.battery import BatteryModel
from dertwin.devices.bess.inverter import InverterModel
from dertwin.devices.bess.bess import BESSModel


def test_bess_step_updates_soc_from_inverter():
    battery = BatteryModel(100, initial_soc=50)
    inverter = InverterModel(50, 50, 1000)
    bess = BESSModel(battery, inverter)

    bess.set_power_command(10)

    result = bess.step(3600)

    expected_delta = 10 * battery.discharge_eff
    expected_soc = 50 - (expected_delta / 100) * 100

    assert pytest.approx(result["system_soc"], rel=1e-6) == expected_soc


def test_bess_telemetry_contains_expected_keys():
    battery = BatteryModel(100, initial_soc=50)
    inverter = InverterModel(50, 50, 1000)
    bess = BESSModel(battery, inverter)

    result = bess.step(1)

    required = [
        "service_voltage",
        "service_current",
        "system_soc",
        "battery_temperature",
        "active_power",
        "reactive_power",
        "apparent_power",
        "grid_frequency",
    ]

    for key in required:
        assert key in result