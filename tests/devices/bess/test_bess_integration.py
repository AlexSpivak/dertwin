import math
import pytest
from dertwin.devices.bess.battery import BatteryModel
from dertwin.devices.bess.inverter import InverterModel
from dertwin.devices.bess.bess import BESSModel
from dertwin.telemetry.bess import BESSTelemetry


def make_bess(
    capacity_kwh=100.0,
    initial_soc=50.0,
    max_charge_kw=50.0,
    max_discharge_kw=50.0,
    ramp_rate_kw_per_s=1000.0,  # fast ramp so tests focus on physics not ramping
):
    battery = BatteryModel(
        capacity_kwh=capacity_kwh,
        initial_soc=initial_soc,
        max_charge_kw=max_charge_kw,
        max_discharge_kw=max_discharge_kw,
    )
    inverter = InverterModel(max_charge_kw, max_discharge_kw, ramp_rate_kw_per_s)
    return BESSModel(battery, inverter)


# ------------------------------------------------------------
# SOC integration correctness
# ------------------------------------------------------------

def test_bess_discharge_reduces_soc_correctly():
    bess = make_bess(initial_soc=50)
    bess.set_power_command(10)
    result: BESSTelemetry = bess.step(3600)

    expected_energy = 10 * bess.battery.discharge_eff
    expected_soc = 50 - (expected_energy / bess.battery.capacity_kwh) * 100

    assert pytest.approx(result.system_soc, rel=1e-6) == expected_soc


def test_bess_charge_increases_soc_correctly():
    bess = make_bess(initial_soc=50)
    bess.set_power_command(-10)
    result: BESSTelemetry = bess.step(3600)

    expected_energy = 10 * bess.battery.charge_eff
    expected_soc = 50 + (expected_energy / bess.battery.capacity_kwh) * 100

    assert pytest.approx(result.system_soc, rel=1e-6) == expected_soc


# ------------------------------------------------------------
# Power limits enforced end-to-end
# ------------------------------------------------------------

def test_bess_respects_max_discharge_power():
    bess = make_bess(initial_soc=50, max_discharge_kw=50)
    bess.set_power_command(100)
    result: BESSTelemetry = bess.step(1.0)
    assert result.active_power <= 50.0


def test_bess_respects_max_charge_power():
    bess = make_bess(initial_soc=50, max_charge_kw=50)
    bess.set_power_command(-100)
    result: BESSTelemetry = bess.step(1.0)
    assert result.active_power >= -50.0


# ------------------------------------------------------------
# SOC hard limits
# ------------------------------------------------------------

def test_bess_cannot_discharge_below_zero():
    bess = make_bess(initial_soc=1)
    bess.set_power_command(50)
    result: BESSTelemetry = bess.step(3600)
    assert result.system_soc >= 0.0


def test_bess_cannot_charge_above_100():
    bess = make_bess(initial_soc=99)
    bess.set_power_command(-50)
    result: BESSTelemetry = bess.step(3600)
    assert result.system_soc <= 100.0


# ------------------------------------------------------------
# Zero command
# ------------------------------------------------------------

def test_bess_zero_command_keeps_soc_constant():
    bess = make_bess(initial_soc=50)
    bess.set_power_command(0)
    result: BESSTelemetry = bess.step(3600)
    assert pytest.approx(result.system_soc, rel=1e-9) == 50.0


# ------------------------------------------------------------
# Ramp behavior
# ------------------------------------------------------------

def test_bess_power_moves_toward_target():
    bess = make_bess(ramp_rate_kw_per_s=5.0)
    bess.set_power_command(50)

    result1: BESSTelemetry = bess.step(1.0)
    result2: BESSTelemetry = bess.step(1.0)

    assert abs(result2.active_power) >= abs(result1.active_power)


# ------------------------------------------------------------
# Available power telemetry
# ------------------------------------------------------------

def test_available_discharge_reduces_as_soc_drops():
    """As SOC falls into derating zone, available_discharging_power must decrease."""
    bess = make_bess(
        capacity_kwh=1.0,  # tiny to hit derating zone fast
        initial_soc=50.0,
        max_discharge_kw=10,
        ramp_rate_kw_per_s=1000,
    )
    bess.set_power_command(10)

    prev_available = bess.step(1.0).available_discharging_power

    for _ in range(500):
        telemetry = bess.step(1.0)

    assert telemetry.available_discharging_power <= prev_available


def test_available_charge_reduces_as_soc_rises():
    """As SOC rises into upper derating zone, available_charging_power must decrease."""
    bess = make_bess(
        capacity_kwh=1.0,
        initial_soc=50.0,
        max_charge_kw=10,
        ramp_rate_kw_per_s=1000,
    )
    bess.set_power_command(-10)

    prev_available = bess.step(1.0).available_charging_power

    for _ in range(500):
        telemetry = bess.step(1.0)

    assert telemetry.available_charging_power <= prev_available


def test_available_power_caps_at_inverter_rating():
    """Available power must never exceed inverter max rating."""
    bess = make_bess(initial_soc=50, max_charge_kw=20, max_discharge_kw=30)
    telemetry = bess.step(1.0)

    assert telemetry.available_charging_power <= 20.0
    assert telemetry.available_discharging_power <= 30.0


# ------------------------------------------------------------
# Telemetry completeness and physical consistency
# ------------------------------------------------------------

def test_bess_telemetry_contains_expected_keys():
    bess = make_bess()
    result = bess.step(1.0).to_dict()

    required = [
        "service_voltage",
        "service_current",
        "system_soc",
        "battery_temperature",
        "active_power",
        "reactive_power",
        "apparent_power",
        "grid_frequency",
        "available_charging_power",
        "available_discharging_power",
        "total_charge_energy",
        "total_discharge_energy",
        "charge_and_discharge_cycles",
        "system_soh",
    ]

    for key in required:
        assert key in result, f"Missing telemetry key: {key}"


def test_apparent_power_consistency():
    bess = make_bess()
    bess.set_power_command(20)
    result: BESSTelemetry = bess.step(1.0)

    expected_s = math.hypot(result.active_power, result.reactive_power)
    assert pytest.approx(result.apparent_power, rel=1e-6) == expected_s


def test_power_factor_valid_range():
    bess = make_bess()
    bess.set_power_command(20)
    result: BESSTelemetry = bess.step(1.0)

    if result.apparent_power > 0:
        pf = abs(result.active_power) / result.apparent_power
        assert 0.0 <= pf <= 1.0


def test_soc_derating_propagates_to_active_power():
    """
    When SOC is in derating zone, actual active_power must be
    less than the requested command — the limit must propagate
    end-to-end from battery through inverter to telemetry.
    """
    from dertwin.devices.bess.battery import BatteryLimits

    limits = BatteryLimits(soc_lower_limit_1=25.0, soc_lower_limit_2=20.0)
    battery = BatteryModel(
        capacity_kwh=100,
        initial_soc=22.5,
        limits=limits,
        max_discharge_kw=10,
    )
    inverter = InverterModel(10, 10, 1000)
    bess = BESSModel(battery, inverter)

    bess.set_power_command(10.0)
    result = bess.step(1.0)

    assert result.active_power < 10.0


def test_grid_voltage_telemetry_matches_all_phases():
    """All three grid voltage phases should be identical (balanced assumption)."""
    bess = make_bess()
    result: BESSTelemetry = bess.step(1.0)

    assert result.grid_voltage_ab == result.grid_voltage_bc
    assert result.grid_voltage_bc == result.grid_voltage_ca