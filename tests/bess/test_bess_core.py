import pytest

from dertwin.devices.bess.battery import BatteryModel
from dertwin.devices.bess.bess import BESSModel
from dertwin.devices.bess.inverter import InverterModel


def test_soc_increases_when_charging():
    battery = BatteryModel(capacity_kwh=100, initial_soc=50)
    inverter = InverterModel(
        max_charge_kw=50,
        max_discharge_kw=50,
        ramp_rate_kw_per_s=1000,  # instant ramp
    )

    bess = BESSModel(battery, inverter)

    bess.set_power_command(10)  # charge 10 kW

    dt = 3600  # 1 hour
    result = bess.step(dt)

    assert result["soc"] > 50
    assert pytest.approx(result["soc"], rel=1e-3) == 59.8  # 10 kWh * 0.98 eff

def test_soc_decreases_when_discharging():
    battery = BatteryModel(capacity_kwh=100, initial_soc=50)
    inverter = InverterModel(
        max_charge_kw=50,
        max_discharge_kw=50,
        ramp_rate_kw_per_s=1000,
    )

    bess = BESSModel(battery, inverter)

    bess.set_power_command(-10)

    dt = 3600
    result = bess.step(dt)
    assert pytest.approx(result["soc"], rel=1e-3) == 39.8  # 10 kWh * 0.98 eff

def test_upper_limit_blocks_charge():
    battery = BatteryModel(
        capacity_kwh=100,
        initial_soc=94.9,
    )

    inverter = InverterModel(
        max_charge_kw=50,
        max_discharge_kw=50,
        ramp_rate_kw_per_s=1000,
    )

    bess = BESSModel(battery, inverter)

    bess.set_power_command(50)

    dt = 3600
    result = bess.step(dt)

    # Should clamp at max limit (95 default)
    assert result["soc"] <= 95.0
    assert battery.is_charge_allowed is False

def test_hysteresis_recovery():
    battery = BatteryModel(
        capacity_kwh=100,
        initial_soc=95.0,
    )

    inverter = InverterModel(
        max_charge_kw=50,
        max_discharge_kw=50,
        ramp_rate_kw_per_s=1000,
    )

    bess = BESSModel(battery, inverter)

    # First step should block charging
    bess.set_power_command(10)
    bess.step(10)

    assert battery.is_charge_allowed is False

    # Now discharge below recovery threshold
    bess.set_power_command(-20)
    bess.step(3600)

    assert battery.is_charge_allowed is True

def test_inverter_ramp_rate_limits_power_change():
    inverter = InverterModel(
        max_charge_kw=100,
        max_discharge_kw=100,
        ramp_rate_kw_per_s=10,
    )

    inverter.set_target_power(100)

    power = inverter.step(1)  # 1 second

    # Should only ramp 10 kW in 1s
    assert power == 10

    # Additional 10kW after another 1s
    power = inverter.step(1)
    assert power == 20