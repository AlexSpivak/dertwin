import pytest

from dertwin.devices.chp.engine import StartupTimings, UnitState
from dertwin.devices.chp.simulator import CHPSimulator
from dertwin.devices.chp.controller import ACKNOWLEDGMENT_MAGIC


# Fast-startup timings for tests — total startup ~6 seconds instead of ~2 minutes
FAST_TIMINGS = StartupTimings(
    starting_to_warmup_s=1.0,
    warmup_to_idle_s=2.0,
    idle_to_sync_s=1.0,
    sync_to_running_s=1.0,
    stopping_to_ready_s=1.0,
)


def run_until_running(chp: CHPSimulator, dt: float = 0.1, max_steps: int = 1000):
    for _ in range(max_steps):
        chp.update(dt)
        if chp.is_running:
            return
    raise AssertionError(f"CHP never reached RUNNING, stuck in {chp.state.name}")


# =========================================================
# Initial State
# =========================================================

def test_initial_state_reasonable():
    chp = CHPSimulator(rated_kw=4000.0)
    assert chp.rated_kw == 4000.0
    assert chp.electrical_power_kw == 0.0
    assert chp.heat_power_kw == 0.0
    assert chp.state == UnitState.READY
    assert not chp.is_running
    assert chp.operating_hours == 0.0
    assert chp.start_counter == 0


def test_initial_fault_code_zero():
    chp = CHPSimulator()
    assert chp.fault_code == 0


def test_configurable_rated_kw():
    chp = CHPSimulator(rated_kw=2500.0)
    assert chp.rated_kw == 2500.0


# =========================================================
# Start / Stop Lifecycle
# =========================================================

def test_start_command_initiates_startup_sequence():
    chp = CHPSimulator(startup_timings=FAST_TIMINGS)
    chp.apply_commands({"start_stop": 1})
    chp.update(dt=0.1)
    assert chp.state == UnitState.STARTING
    assert chp.start_counter == 1


def test_full_startup_reaches_running():
    chp = CHPSimulator(startup_timings=FAST_TIMINGS)
    chp.apply_commands({"start_stop": 1})
    run_until_running(chp)
    assert chp.is_running
    assert chp.state == UnitState.RUNNING


def test_stop_command_returns_to_ready():
    chp = CHPSimulator(startup_timings=FAST_TIMINGS)
    chp.apply_commands({"start_stop": 1})
    run_until_running(chp)

    chp.apply_commands({"start_stop": 0})
    for _ in range(50):
        chp.update(dt=0.1)
    assert chp.state == UnitState.READY


def test_full_lifecycle_via_commands():
    """Start → dispatch → stop, the way an EMS would drive it."""
    chp = CHPSimulator(
        rated_kw=4000.0,
        ramp_rate_percent_per_s=100.0,
        startup_timings=FAST_TIMINGS,
    )

    chp.apply_commands({"start_stop": 1})
    run_until_running(chp)

    chp.apply_commands({"power_setpoint_percent": 75.0})
    for _ in range(50):
        chp.update(dt=0.1)
    assert chp.actual_power_percent == pytest.approx(75.0, abs=1.0)
    assert chp.electrical_power_kw == pytest.approx(3000.0, abs=50.0)

    chp.apply_commands({"start_stop": 0})
    for _ in range(50):
        chp.update(dt=0.1)
    assert chp.state == UnitState.READY
    assert chp.actual_power_percent == pytest.approx(0.0, abs=1.0)


# =========================================================
# Power Dispatch
# =========================================================

def test_power_dispatched_only_when_running():
    chp = CHPSimulator(startup_timings=FAST_TIMINGS)
    chp.apply_commands({"power_setpoint_percent": 75.0})

    # Engine still READY — no dispatch
    for _ in range(20):
        chp.update(dt=0.1)
    assert chp.electrical_power_kw == 0.0
    assert chp.heat_power_kw == 0.0


def test_setpoint_clamped_to_max():
    chp = CHPSimulator(
        rated_kw=4000.0,
        ramp_rate_percent_per_s=100.0,
        startup_timings=FAST_TIMINGS,
        max_load_percent=110.0,
    )
    chp.apply_commands({"start_stop": 1})
    run_until_running(chp)

    # Raise permitted_power above 100% so overload is reachable
    chp.chp.permitted_power_percent = 110.0
    chp.apply_commands({"power_setpoint_percent": 150.0})
    for _ in range(50):
        chp.update(dt=0.1)
    assert chp.actual_power_percent == pytest.approx(110.0, abs=1.0)


def test_setpoint_below_min_clamped_to_min():
    """Real CHPs cannot run below minimum load — clamped, not zero."""
    chp = CHPSimulator(
        ramp_rate_percent_per_s=100.0,
        startup_timings=FAST_TIMINGS,
        min_load_percent=30.0,
    )
    chp.apply_commands({"start_stop": 1})
    run_until_running(chp)

    chp.apply_commands({"power_setpoint_percent": 10.0})
    for _ in range(50):
        chp.update(dt=0.1)
    assert chp.actual_power_percent == pytest.approx(30.0, abs=1.0)


def test_zero_setpoint_explicitly_means_no_dispatch():
    chp = CHPSimulator(
        ramp_rate_percent_per_s=100.0,
        startup_timings=FAST_TIMINGS,
    )
    chp.apply_commands({"start_stop": 1})
    run_until_running(chp)

    chp.apply_commands({"power_setpoint_percent": 50.0})
    for _ in range(50):
        chp.update(dt=0.1)
    assert chp.actual_power_percent == pytest.approx(50.0, abs=1.0)

    chp.apply_commands({"power_setpoint_percent": 0.0})
    for _ in range(50):
        chp.update(dt=0.1)
    assert chp.actual_power_percent == pytest.approx(0.0, abs=1.0)


# =========================================================
# Heat Output
# =========================================================

def test_heat_power_scales_with_ratio():
    chp = CHPSimulator(
        rated_kw=4000.0,
        heat_to_power_ratio=1.2,
        ramp_rate_percent_per_s=100.0,
        startup_timings=FAST_TIMINGS,
    )
    chp.apply_commands({"start_stop": 1})
    run_until_running(chp)

    chp.apply_commands({"power_setpoint_percent": 50.0})
    for _ in range(50):
        chp.update(dt=0.1)

    assert chp.electrical_power_kw == pytest.approx(2000.0, abs=50.0)
    assert chp.heat_power_kw == pytest.approx(2400.0, abs=50.0)


def test_heat_zero_when_idle():
    chp = CHPSimulator(heat_to_power_ratio=1.0, startup_timings=FAST_TIMINGS)
    chp.update(dt=0.1)
    assert chp.heat_power_kw == 0.0


# =========================================================
# Ramp Rate
# =========================================================

def test_ramp_rate_limits_power_change():
    chp = CHPSimulator(
        ramp_rate_percent_per_s=5.0,
        startup_timings=FAST_TIMINGS,
    )
    chp.apply_commands({"start_stop": 1})
    run_until_running(chp)

    chp.apply_commands({"power_setpoint_percent": 100.0})
    for _ in range(10):  # 1 second at 5%/s
        chp.update(dt=0.1)
    assert chp.actual_power_percent == pytest.approx(5.0, abs=0.5)


# =========================================================
# Fault Handling
# =========================================================

def test_fault_code_setter():
    chp = CHPSimulator()
    chp.fault_code = 1001
    assert chp.fault_code == 1001
    assert chp.state == UnitState.FAULT


def test_remote_acknowledgment_clears_fault():
    chp = CHPSimulator()
    chp.fault_code = 1001
    assert chp.state == UnitState.FAULT

    chp.apply_commands({"remote_acknowledgment": ACKNOWLEDGMENT_MAGIC})
    assert chp.state == UnitState.READY
    assert chp.fault_code == 0


def test_wrong_acknowledgment_value_does_not_clear_fault():
    chp = CHPSimulator()
    chp.fault_code = 1001

    chp.apply_commands({"remote_acknowledgment": 1})
    assert chp.fault_code == 1001
    assert chp.state == UnitState.FAULT


def test_fault_blocks_power_dispatch():
    chp = CHPSimulator(
        ramp_rate_percent_per_s=100.0,
        startup_timings=FAST_TIMINGS,
    )
    chp.apply_commands({"start_stop": 1})
    run_until_running(chp)

    chp.apply_commands({"power_setpoint_percent": 80.0})
    for _ in range(50):
        chp.update(dt=0.1)
    assert chp.actual_power_percent > 0.0

    chp.fault_code = 1001
    for _ in range(50):
        chp.update(dt=0.1)
    assert chp.actual_power_percent == 0.0


def test_can_restart_after_fault_acknowledgment():
    chp = CHPSimulator(
        ramp_rate_percent_per_s=100.0,
        startup_timings=FAST_TIMINGS,
    )
    chp.apply_commands({"start_stop": 1})
    run_until_running(chp)

    chp.fault_code = 1001
    chp.apply_commands({"remote_acknowledgment": ACKNOWLEDGMENT_MAGIC})

    # Reset start_stop so the next "1" is seen as a change by the deduplicator
    chp.apply_commands({"start_stop": 0})
    chp.apply_commands({"start_stop": 1})
    run_until_running(chp)
    assert chp.is_running


# =========================================================
# Operating Statistics
# =========================================================

def test_operating_hours_accumulate_when_running():
    chp = CHPSimulator(startup_timings=FAST_TIMINGS)
    chp.apply_commands({"start_stop": 1})
    run_until_running(chp)

    # Run for an hour of sim time
    for _ in range(3600):
        chp.update(dt=1.0)

    assert chp.operating_hours == pytest.approx(1.0, abs=0.01)


def test_start_counter_increments():
    chp = CHPSimulator(startup_timings=FAST_TIMINGS)
    assert chp.start_counter == 0

    chp.apply_commands({"start_stop": 1})
    chp.update(dt=0.1)
    assert chp.start_counter == 1

    # Stop and restart
    chp.apply_commands({"start_stop": 0})
    for _ in range(50):
        chp.update(dt=0.1)

    chp.apply_commands({"start_stop": 1})
    chp.update(dt=0.1)
    assert chp.start_counter == 2


# =========================================================
# Telemetry
# =========================================================

def test_telemetry_reflects_state():
    chp = CHPSimulator(
        rated_kw=4000.0,
        ramp_rate_percent_per_s=100.0,
        startup_timings=FAST_TIMINGS,
    )

    chp.update(dt=0.1)
    telemetry = chp.get_telemetry()
    assert telemetry.unit_state == int(UnitState.READY)
    assert telemetry.engine_running is False
    assert telemetry.actual_power_kw == 0.0

    chp.apply_commands({"start_stop": 1})
    run_until_running(chp)
    chp.apply_commands({"power_setpoint_percent": 60.0})
    for _ in range(50):
        chp.update(dt=0.1)

    telemetry = chp.get_telemetry()
    assert telemetry.unit_state == int(UnitState.RUNNING)
    assert telemetry.engine_running is True
    assert telemetry.circuit_breaker_closed is True
    assert telemetry.actual_power_kw == pytest.approx(2400.0, abs=50.0)


def test_telemetry_operating_hours_split():
    """Verify MWM split convention exposed in telemetry."""
    chp = CHPSimulator(startup_timings=FAST_TIMINGS)
    chp.engine.operating_hours = 12345.0
    chp.update(dt=0.1)

    telemetry = chp.get_telemetry()
    assert telemetry.operating_hours == 2345
    assert telemetry.operating_hours_10000 == 1


# =========================================================
# Command Idempotency
# =========================================================

def test_apply_commands_is_idempotent():
    chp = CHPSimulator(startup_timings=FAST_TIMINGS)
    commands = {"power_setpoint_percent": 60.0}

    applied1 = chp.apply_commands(commands)
    applied2 = chp.apply_commands(commands)

    assert applied1 == {"power_setpoint_percent": 60.0}
    assert applied2 == {}


def test_apply_commands_returns_applied_subset():
    chp = CHPSimulator(startup_timings=FAST_TIMINGS)
    result = chp.apply_commands({
        "power_setpoint_percent": 60.0,
        "unknown_register": 99,
    })
    assert "power_setpoint_percent" in result
    assert "unknown_register" not in result


def test_start_stop_change_does_not_reapply_setpoint():
    """Regression for the bug we hit in PVController — one command changing
    must not re-fire the side effect of another command in the same dict."""
    chp = CHPSimulator(startup_timings=FAST_TIMINGS)
    chp.apply_commands({"power_setpoint_percent": 60.0})

    applied = chp.apply_commands({
        "power_setpoint_percent": 60.0,
        "start_stop": 1,
    })
    assert "power_setpoint_percent" not in applied
    assert applied.get("start_stop") == 1