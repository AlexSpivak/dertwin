import pytest
from dertwin.devices.bess.inverter import InverterModel


def test_ramp_rate_limits_change():
    inverter = InverterModel(max_charge_kw=100, max_discharge_kw=100, ramp_rate_kw_per_s=10)

    inverter.set_target_power(100)

    p1 = inverter.step(1)
    p2 = inverter.step(1)
    p3 = inverter.step(10)

    # with ramp rate 10kW per second each step will increase power to +10
    # until it reach 100 in 10s and won't go over that level
    assert p1 == 10
    assert p2 == 20
    assert p3 == 100


def test_power_clamped_to_limits():
    inverter = InverterModel(max_charge_kw=50, max_discharge_kw=50, ramp_rate_kw_per_s=1000)

    inverter.set_target_power(100)
    power = inverter.step(1)
    # even if setpoint is 100 and ramp rate allows to reach power in one 1
    # applied power can't go over charging limits of which set to 50
    assert power == 50


import pytest


def test_reactive_and_apparent_power():
    # Inverter with high ramp rate so target is reached immediately
    inverter = InverterModel(
        max_charge_kw=100,
        max_discharge_kw=100,
        ramp_rate_kw_per_s=1000,
    )

    # Set active (real) power to 10 kW
    inverter.set_target_power(10)
    inverter.step(1)

    # Reactive power (model defines fixed proportion of active power)
    q = inverter.reactive_power()

    # Apparent power magnitude from AC power triangle:
    # S² = P² + Q²
    s = inverter.apparent_power()

    # Expect 10% reactive component → Q = 1 kVAr
    assert q == 1.0

    # Apparent power must satisfy S = sqrt(P² + Q²)
    assert pytest.approx(s, rel=1e-6) == (10**2 + 1**2) ** 0.5