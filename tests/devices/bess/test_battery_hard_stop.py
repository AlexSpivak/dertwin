"""
Hard SOC stop: no tapering, land exactly on the limit, then exactly zero.

Also covers the efficiency convention (a configured round_trip_eff is actually
realised) and optional self-discharge.
"""

import math

import pytest

from dertwin.devices.bess.battery import BatteryLimits, BatteryModel
from dertwin.devices.bess.bess import BESSModel
from dertwin.devices.bess.inverter import InverterModel


# eta = 0.9 each way, so the arithmetic below is exact in decimal
RTE = 0.81
ETA = 0.9


def make_battery(**kwargs) -> BatteryModel:
    defaults = dict(
        capacity_kwh=100.0,
        initial_soc=50.0,
        round_trip_eff=RTE,
        max_charge_kw=1000.0,
        max_discharge_kw=1000.0,
        soc_min_percent=10.0,
        soc_max_percent=95.0,
    )
    defaults.update(kwargs)
    return BatteryModel(**defaults)


# ------------------------------------------------------------
# Landing exactly on the limit
# ------------------------------------------------------------

def test_charge_lands_exactly_on_soc_max():
    """The crossing step is clamped so SOC lands on the ceiling, not past it."""
    battery = make_battery(initial_soc=90.0)

    applied = battery.step(-1000.0, 3600.0)

    # 5 kWh of room, stored at eta → 5/eta kW of AC for one hour
    assert applied == pytest.approx(-5.0 / ETA, rel=1e-12)
    assert battery.soc == pytest.approx(95.0, abs=1e-9)
    assert battery.energy_kwh == pytest.approx(95.0, abs=1e-9)


def test_discharge_lands_exactly_on_soc_min():
    battery = make_battery(initial_soc=15.0)

    applied = battery.step(1000.0, 3600.0)

    # 5 kWh available above the floor, delivered at eta
    assert applied == pytest.approx(5.0 * ETA, rel=1e-12)
    assert battery.soc == pytest.approx(10.0, abs=1e-9)


def test_power_is_exactly_zero_once_on_soc_max():
    battery = make_battery(initial_soc=90.0)
    battery.step(-1000.0, 3600.0)

    charged_before = battery.charge_energy_total_kwh
    applied = battery.step(-1000.0, 3600.0)

    assert applied == 0.0
    assert battery.soc == pytest.approx(95.0, abs=1e-9)
    assert battery.charge_energy_total_kwh == charged_before


def test_power_is_exactly_zero_once_on_soc_min():
    battery = make_battery(initial_soc=15.0)
    battery.step(1000.0, 3600.0)

    discharged_before = battery.discharge_energy_total_kwh
    applied = battery.step(1000.0, 3600.0)

    assert applied == 0.0
    assert battery.soc == pytest.approx(10.0, abs=1e-9)
    assert battery.discharge_energy_total_kwh == discharged_before


def test_soc_never_crosses_limit_over_many_small_steps():
    battery = make_battery(initial_soc=94.0, max_charge_kw=50.0)

    for _ in range(600):
        battery.step(-50.0, 1.0)
        assert battery.soc <= 95.0 + 1e-9

    assert battery.soc == pytest.approx(95.0, abs=1e-9)


# ------------------------------------------------------------
# No tapering anywhere near the limits
# ------------------------------------------------------------

def test_full_charge_power_right_up_to_the_limit():
    """
    One second from the ceiling, full rating is still available.

    The legacy taper would have allowed 10 * (95 - 94.99)/10 = 0.01 kW here.
    """
    battery = make_battery(
        initial_soc=94.99,
        max_charge_kw=10.0,
        limits=BatteryLimits(soc_upper_limit_1=85.0, soc_upper_limit_2=95.0),
    )

    applied = battery.step(-10.0, 1.0)

    assert applied == pytest.approx(-10.0, rel=1e-12)


def test_full_discharge_power_right_down_to_the_limit():
    battery = make_battery(initial_soc=10.01, max_discharge_kw=10.0)

    applied = battery.step(10.0, 1.0)

    assert applied == pytest.approx(10.0, rel=1e-12)


def test_power_limits_gate_is_binary_not_tapered():
    battery = make_battery(initial_soc=94.99, max_charge_kw=10.0)
    min_kw, _ = battery.get_power_limits()
    assert min_kw == pytest.approx(-10.0, rel=1e-12)

    battery.energy_kwh = battery.energy_for_soc(95.0)
    min_kw, _ = battery.get_power_limits()
    assert min_kw == 0.0


# ------------------------------------------------------------
# Setpoint survives the clamp; dispatch resumes on its own
# ------------------------------------------------------------

def make_bess(**kwargs) -> BESSModel:
    battery = make_battery(**kwargs)
    inverter = InverterModel(
        max_charge_kw=battery.max_charge_kw,
        max_discharge_kw=battery.max_discharge_kw,
        ramp_rate_kw_per_s=10_000.0,
    )
    return BESSModel(battery, inverter)


def test_setpoint_is_kept_while_actual_power_is_zero():
    bess = make_bess(initial_soc=95.0, max_charge_kw=20.0)
    bess.set_power_command(-20.0)

    telemetry = bess.step(1.0)

    assert telemetry.active_power == 0.0
    assert bess.inverter.target_power == -20.0


def test_dispatch_resumes_once_headroom_returns():
    bess = make_bess(initial_soc=95.0, max_charge_kw=20.0)
    bess.set_power_command(-20.0)

    assert bess.step(1.0).active_power == 0.0

    # Something else drained the pack; the setpoint never changed.
    bess.battery.energy_kwh = bess.battery.energy_for_soc(50.0)

    assert bess.step(1.0).active_power == pytest.approx(-20.0, rel=1e-12)


def test_reactive_and_apparent_power_follow_clamped_power():
    bess = make_bess(initial_soc=95.0, max_charge_kw=20.0)
    bess.set_power_command(-20.0)

    telemetry = bess.step(1.0)

    assert telemetry.active_power == 0.0
    assert telemetry.reactive_power == pytest.approx(0.0, abs=1e-12)
    assert telemetry.apparent_power == pytest.approx(0.0, abs=1e-12)


def test_apparent_power_consistent_with_clamped_active_power():
    bess = make_bess(initial_soc=90.0, max_charge_kw=20.0)
    bess.set_power_command(-20.0)

    telemetry = bess.step(3600.0)

    assert telemetry.active_power == pytest.approx(-5.0 / ETA, rel=1e-12)
    assert telemetry.apparent_power == pytest.approx(
        math.hypot(telemetry.active_power, telemetry.reactive_power), rel=1e-12
    )


def test_service_current_is_amps_not_kilowatts():
    bess = make_bess(initial_soc=50.0, max_discharge_kw=20.0)
    bess.set_power_command(20.0)

    telemetry = bess.step(1.0)
    voc = bess.battery.open_circuit_voltage()

    assert telemetry.service_current == pytest.approx(
        telemetry.active_power * 1000.0 / voc, rel=1e-12
    )
    assert telemetry.service_current != pytest.approx(telemetry.active_power)


# ------------------------------------------------------------
# Efficiency is actually realised
# ------------------------------------------------------------

def test_round_trip_efficiency_is_realised():
    battery = make_battery(
        initial_soc=50.0, soc_min_percent=0.0, soc_max_percent=100.0
    )

    start = battery.energy_kwh
    battery.step(-10.0, 3600.0)  # 10 kWh AC in
    stored = battery.energy_kwh - start
    assert stored == pytest.approx(10.0 * ETA, rel=1e-12)

    # Taking those 9 kWh back out of the pack yields 8.1 kWh AC = 0.81 * 10
    before = battery.energy_kwh
    applied = battery.step(stored * ETA, 3600.0)
    assert applied == pytest.approx(RTE * 10.0, rel=1e-12)
    assert before - battery.energy_kwh == pytest.approx(stored, rel=1e-12)


def test_round_trip_does_not_return_to_the_same_soc():
    """The pre-0.2 model multiplied on both legs, so RTE was silently 1.0."""
    battery = make_battery(
        initial_soc=50.0, soc_min_percent=0.0, soc_max_percent=100.0
    )

    battery.step(-10.0, 3600.0)
    battery.step(10.0, 3600.0)

    assert battery.soc < 50.0
    # 10 kWh in stores 9; 10 kWh out costs 11.111 → net -2.111 kWh
    assert battery.soc == pytest.approx(50.0 + 10.0 * ETA - 10.0 / ETA, rel=1e-9)


def test_discharge_counter_is_battery_side():
    battery = make_battery(
        initial_soc=50.0, soc_min_percent=0.0, soc_max_percent=100.0
    )

    battery.step(10.0, 3600.0)

    assert battery.discharge_energy_total_kwh == pytest.approx(10.0 / ETA, rel=1e-12)


def test_separate_efficiencies_override_round_trip():
    battery = make_battery(charge_efficiency=0.8, discharge_efficiency=0.95)

    assert battery.charge_eff == 0.8
    assert battery.discharge_eff == 0.95


def test_invalid_efficiency_raises():
    with pytest.raises(ValueError, match="charge_efficiency"):
        make_battery(charge_efficiency=1.5)

    with pytest.raises(ValueError, match="discharge_efficiency"):
        make_battery(discharge_efficiency=0.0)


# ------------------------------------------------------------
# Self-discharge
# ------------------------------------------------------------

def test_self_discharge_drains_configured_rate_per_day():
    battery = make_battery(
        initial_soc=50.0, soc_min_percent=0.0, self_discharge_percent_per_day=2.0
    )

    battery.step(0.0, 86400.0)  # one idle day

    assert battery.soc == pytest.approx(48.0, rel=1e-9)


def test_self_discharge_off_by_default():
    battery = make_battery(initial_soc=50.0)
    battery.step(0.0, 86400.0)
    assert battery.soc == pytest.approx(50.0, rel=1e-12)


def test_self_discharge_floors_at_soc_min():
    battery = make_battery(
        initial_soc=10.0, soc_min_percent=10.0, self_discharge_percent_per_day=50.0
    )

    battery.step(0.0, 86400.0)

    assert battery.soc == pytest.approx(10.0, abs=1e-9)


# ------------------------------------------------------------
# Hard window plumbing
# ------------------------------------------------------------

def test_hard_limits_default_to_legacy_limit_2_thresholds():
    battery = BatteryModel(
        capacity_kwh=100.0,
        limits=BatteryLimits(soc_lower_limit_2=22.0, soc_upper_limit_2=88.0),
    )

    assert battery.soc_min_percent == 22.0
    assert battery.soc_max_percent == 88.0


def test_ems_write_to_limit_2_moves_the_hard_floor_live():
    battery = BatteryModel(
        capacity_kwh=100.0, initial_soc=35.0, max_discharge_kw=20.0
    )

    assert battery.step(20.0, 1.0) == pytest.approx(20.0, rel=1e-12)

    # Same register the Modbus soc_lower_limit_2 command writes
    battery.limits.soc_lower_limit_2 = 40.0

    assert battery.step(20.0, 1.0) == 0.0


def test_explicit_soc_window_overrides_limits():
    battery = BatteryModel(
        capacity_kwh=100.0,
        soc_min_percent=5.0,
        soc_max_percent=99.0,
        limits=BatteryLimits(soc_lower_limit_2=22.0, soc_upper_limit_2=88.0),
    )

    assert battery.soc_min_percent == 5.0
    assert battery.soc_max_percent == 99.0


def test_pack_booted_above_ceiling_is_not_yanked_down():
    battery = make_battery(initial_soc=99.0, soc_max_percent=95.0)

    applied = battery.step(-1000.0, 3600.0)

    assert applied == 0.0
    assert battery.soc == pytest.approx(99.0, rel=1e-12)


def test_invalid_limit_mode_raises():
    with pytest.raises(ValueError, match="limit_mode"):
        make_battery(limit_mode="soft")


def test_inverted_soc_window_raises():
    with pytest.raises(ValueError, match="soc_min_percent"):
        make_battery(soc_min_percent=90.0, soc_max_percent=20.0)


# ------------------------------------------------------------
# Legacy taper mode still reproduces the old numbers
# ------------------------------------------------------------

def test_taper_mode_reproduces_legacy_charge_derating():
    battery = BatteryModel(
        capacity_kwh=100.0,
        initial_soc=87.5,
        max_charge_kw=10.0,
        limits=BatteryLimits(soc_upper_limit_1=85.0, soc_upper_limit_2=90.0),
        limit_mode="taper",
    )

    applied = battery.step(-10.0, 1.0)

    assert applied == pytest.approx(-5.0, rel=1e-6)


def test_taper_mode_reproduces_legacy_discharge_derating():
    battery = BatteryModel(
        capacity_kwh=100.0,
        initial_soc=22.5,
        max_discharge_kw=10.0,
        limits=BatteryLimits(soc_lower_limit_1=25.0, soc_lower_limit_2=20.0),
        limit_mode="taper",
    )

    applied = battery.step(10.0, 1.0)

    assert applied == pytest.approx(5.0, rel=1e-6)
