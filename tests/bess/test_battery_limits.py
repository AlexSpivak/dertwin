import pytest
from dertwin.devices.bess.battery import BatteryModel, BatteryLimits


def test_hard_discharge_cutoff():
    limits = BatteryLimits(soc_lower_limit_2=20.0)
    battery = BatteryModel(100, initial_soc=20.0, limits=limits)

    # attempt to discharge 10kW for 1 hour (limit 10% won't allow it)
    applied = battery.step(10, 3600)

    assert applied == 0.0
    assert battery.soc == 20.0


def test_hard_charge_cutoff():
    limits = BatteryLimits(soc_upper_limit_2=90.0)
    battery = BatteryModel(100, initial_soc=90.0, limits=limits)

    # attempt to charge 10kW for 1 hour (limit 90% won't allow it)
    applied = battery.step(-10, 3600)

    assert applied == 0.0
    assert battery.soc == 90.0

def test_soft_discharge_derating_converges_to_lower_limit():
    limits = BatteryLimits(
        soc_lower_limit_1=25.0,
        soc_lower_limit_2=20.0,
    )

    battery = BatteryModel(
        capacity_kwh=100,
        initial_soc=22.5,
        limits=limits,
    )

    dt = 1.0  # 1 second resolution
    first_applied = battery.step(10, dt)

    # ---- Initial derating check ----
    expected_factor = (22.5 - 20.0) / (25.0 - 20.0)
    assert pytest.approx(first_applied, rel=1e-6) == 10 * expected_factor

    # ---- Run long enough to approach lower bound ----
    for _ in range(3600 * 3):  # 3 hours at 1-second resolution
        battery.step(10, dt)

    # ---- SOC should never cross hard limit ----
    assert battery.soc >= limits.soc_lower_limit_2

    # ---- And should converge close to it ----
    assert pytest.approx(battery.soc, rel=1e-3) == limits.soc_lower_limit_2