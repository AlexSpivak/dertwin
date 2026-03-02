import pytest
from dertwin.devices.bess.battery import BatteryModel, BatteryLimits


def test_hard_discharge_cutoff():
    limits = BatteryLimits(soc_lower_limit_2=20.0)
    battery = BatteryModel(100, initial_soc=20.0, limits=limits)

    applied = battery.step(10, 3600)

    assert applied == 0.0
    assert battery.soc == 20.0


def test_hard_charge_cutoff():
    limits = BatteryLimits(soc_upper_limit_2=90.0)
    battery = BatteryModel(100, initial_soc=90.0, limits=limits)

    applied = battery.step(-10, 3600)

    assert applied == 0.0
    assert battery.soc == 90.0


def test_soft_discharge_derating():
    limits = BatteryLimits(
        soc_lower_limit_1=25.0,
        soc_lower_limit_2=20.0,
    )

    battery = BatteryModel(
        capacity_kwh=100,
        initial_soc=22.5,
        limits=limits,
        max_discharge_kw=10,
    )

    applied = battery.step(10, 1.0)

    expected_factor = (22.5 - 20.0) / (25.0 - 20.0)
    expected_power = 10 * expected_factor

    assert applied == pytest.approx(expected_power, rel=1e-6)