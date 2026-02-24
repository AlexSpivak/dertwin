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

    # Ramp rate = 10 kW/s
    # After 1s → 10
    # After 2s → 20
    # After 10 more seconds → capped at 100
    assert p1 == 10
    assert p2 == 20
    assert p3 == 100


def test_power_clamped_to_limits():
    inverter = InverterModel(
        max_charge_kw=50,
        max_discharge_kw=50,
        ramp_rate_kw_per_s=1000,
    )

    inverter.target_power = 100
    power = inverter.step(1.0)

    # Even though ramp allows instant change,
    # output must respect discharge limit
    assert power == 50

def test_reactive_and_apparent_power():
    # Inverter with high ramp rate so target is reached immediately
    inverter = InverterModel(
        max_charge_kw=100,
        max_discharge_kw=100,
        ramp_rate_kw_per_s=1000,
    )

    inverter.target_power = 10
    inverter.step(1.0)

    # Reactive power (model defines fixed proportion of active power)
    q = inverter.reactive_power()

    # Apparent power magnitude from AC power triangle:
    # S² = P² + Q²
    s = inverter.apparent_power()

    # Model defines 10% reactive component
    assert q == pytest.approx(1.0, rel=1e-6)

    # Apparent power: S = sqrt(P² + Q²)
    assert s == pytest.approx((10**2 + 1**2) ** 0.5, rel=1e-6)