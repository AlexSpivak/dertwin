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


def test_soft_charge_derating():
    limits = BatteryLimits(
        soc_upper_limit_1=85.0,
        soc_upper_limit_2=90.0,
    )
    battery = BatteryModel(
        capacity_kwh=100,
        initial_soc=87.5,
        limits=limits,
        max_charge_kw=10,
    )

    applied = battery.step(-10, 1.0)

    expected_factor = (90.0 - 87.5) / (90.0 - 85.0)
    expected_power = -10 * expected_factor  # negative = charge

    assert applied == pytest.approx(expected_power, rel=1e-6)


def test_full_discharge_capability_above_lower_limit_1():
    """Above lower_limit_1 the battery should allow full discharge power."""
    limits = BatteryLimits(soc_lower_limit_1=25.0, soc_lower_limit_2=20.0)
    battery = BatteryModel(
        capacity_kwh=100,
        initial_soc=50.0,
        limits=limits,
        max_discharge_kw=10,
    )

    applied = battery.step(10, 1.0)
    assert applied == pytest.approx(10.0, rel=1e-6)


def test_full_charge_capability_below_upper_limit_1():
    """Below upper_limit_1 the battery should allow full charge power."""
    limits = BatteryLimits(soc_upper_limit_1=85.0, soc_upper_limit_2=90.0)
    battery = BatteryModel(
        capacity_kwh=100,
        initial_soc=50.0,
        limits=limits,
        max_charge_kw=10,
    )

    applied = battery.step(-10, 1.0)
    assert applied == pytest.approx(-10.0, rel=1e-6)


def test_temperature_derating_cold():
    """Cold battery (≤0°C) should derate to 50% capability."""
    battery = BatteryModel(
        capacity_kwh=100,
        initial_soc=50,
        max_discharge_kw=10,
    )
    battery.temperature_c = 0.0

    _, max_kw = battery.get_power_limits()
    assert max_kw == pytest.approx(10.0 * 0.5, rel=1e-6)


def test_temperature_derating_hot():
    """Hot battery (>50°C) should derate."""
    battery = BatteryModel(
        capacity_kwh=100,
        initial_soc=50,
        max_discharge_kw=10,
    )
    battery.temperature_c = 55.0

    _, max_kw = battery.get_power_limits()
    assert max_kw < 10.0


def test_temperature_derating_extreme_heat_shuts_down():
    """Above 60°C the battery should be fully blocked."""
    battery = BatteryModel(
        capacity_kwh=100,
        initial_soc=50,
        max_discharge_kw=10,
    )
    battery.temperature_c = 65.0

    _, max_kw = battery.get_power_limits()
    assert max_kw == pytest.approx(0.0, abs=1e-9)


def test_soc_and_temperature_limits_combine():
    """Both SOC derating and temperature derating should apply simultaneously."""
    limits = BatteryLimits(soc_lower_limit_1=25.0, soc_lower_limit_2=20.0)
    battery = BatteryModel(
        capacity_kwh=100,
        initial_soc=22.5,
        limits=limits,
        max_discharge_kw=10,
    )
    battery.temperature_c = 55.0  # also in temperature derating zone

    _, max_kw = battery.get_power_limits()

    soc_scale = (22.5 - 20.0) / (25.0 - 20.0)
    temp_scale = 0.8  # 50-60°C band
    expected = 10.0 * min(soc_scale, temp_scale)

    assert max_kw == pytest.approx(expected, rel=1e-6)


def test_get_power_limits_returns_negative_min():
    """min_kw (max charge) must be negative."""
    battery = BatteryModel(capacity_kwh=100, initial_soc=50, max_charge_kw=20)
    min_kw, _ = battery.get_power_limits()
    assert min_kw < 0.0


def test_get_power_limits_returns_positive_max():
    """max_kw (max discharge) must be positive."""
    battery = BatteryModel(capacity_kwh=100, initial_soc=50, max_discharge_kw=20)
    _, max_kw = battery.get_power_limits()
    assert max_kw > 0.0