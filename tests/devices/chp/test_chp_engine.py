import pytest

from dertwin.devices.chp.engine import EngineModel, StartupTimings, UnitState


# ============================================================
# FIXTURES
# ============================================================

def make_engine(ambient_c: float = 20.0, fast_timings: bool = True) -> EngineModel:
    """Create an engine with optionally accelerated startup timings for tests."""
    if fast_timings:
        timings = StartupTimings(
            starting_to_warmup_s=1.0,
            warmup_to_idle_s=2.0,
            idle_to_sync_s=1.0,
            sync_to_running_s=1.0,
            stopping_to_ready_s=1.0,
        )
    else:
        timings = None
    return EngineModel(ambient_temp_c=ambient_c, timings=timings)


def run_until_state(engine: EngineModel, target_state: UnitState, dt: float = 0.1, max_steps: int = 1000):
    """Step engine until it reaches target_state. Raises if it never does."""
    for _ in range(max_steps):
        engine.step(dt)
        if engine.state == target_state:
            return
    raise AssertionError(
        f"Engine never reached {target_state.name}, stuck in {engine.state.name}"
    )


# ============================================================
# INITIAL STATE
# ============================================================

class TestInitialState:

    def test_engine_starts_in_ready(self):
        engine = make_engine()
        assert engine.state == UnitState.READY

    def test_engine_speed_initially_zero(self):
        engine = make_engine()
        assert engine.engine_speed_rpm == 0.0

    def test_coolant_at_ambient_initially(self):
        engine = make_engine(ambient_c=15.0)
        assert engine.coolant_outlet_c == 15.0
        assert engine.coolant_inlet_c == 15.0

    def test_no_fault_initially(self):
        engine = make_engine()
        assert engine.fault_code == 0
        assert engine.warning_code == 0

    def test_counters_zero_initially(self):
        engine = make_engine()
        assert engine.operating_hours == 0.0
        assert engine.start_counter == 0


# ============================================================
# STATE MACHINE TRANSITIONS
# ============================================================

class TestStateMachine:

    def test_request_start_from_ready_transitions_to_starting(self):
        engine = make_engine()
        engine.request_start()
        engine.step(0.01)
        assert engine.state == UnitState.STARTING

    def test_start_counter_increments_on_start(self):
        engine = make_engine()
        assert engine.start_counter == 0
        engine.request_start()
        engine.step(0.01)
        assert engine.start_counter == 1

    def test_full_startup_sequence_reaches_running(self):
        engine = make_engine()
        engine.request_start()
        run_until_state(engine, UnitState.RUNNING)
        assert engine.state == UnitState.RUNNING

    def test_startup_sequence_passes_through_all_phases(self):
        engine = make_engine()
        engine.request_start()

        seen_states = set()
        for _ in range(1000):
            engine.step(0.1)
            seen_states.add(engine.state)
            if engine.state == UnitState.RUNNING:
                break

        # Must visit each intermediate state
        assert UnitState.STARTING in seen_states
        assert UnitState.WARMUP in seen_states
        assert UnitState.IDLE in seen_states
        assert UnitState.SYNCHRONIZING in seen_states
        assert UnitState.RUNNING in seen_states

    def test_request_stop_from_running_transitions_to_stopping(self):
        engine = make_engine()
        engine.request_start()
        run_until_state(engine, UnitState.RUNNING)

        engine.request_stop()
        engine.step(0.01)
        assert engine.state == UnitState.STOPPING

    def test_stopping_transitions_to_ready(self):
        engine = make_engine()
        engine.request_start()
        run_until_state(engine, UnitState.RUNNING)
        engine.request_stop()
        run_until_state(engine, UnitState.READY)
        assert engine.state == UnitState.READY

    def test_stop_during_warmup_allowed(self):
        engine = make_engine()
        engine.request_start()
        run_until_state(engine, UnitState.WARMUP)

        engine.request_stop()
        engine.step(0.01)
        assert engine.state == UnitState.STOPPING

    def test_request_start_from_running_is_noop(self):
        engine = make_engine()
        engine.request_start()
        run_until_state(engine, UnitState.RUNNING)

        starts_before = engine.start_counter
        engine.request_start()
        engine.step(0.01)
        assert engine.state == UnitState.RUNNING
        assert engine.start_counter == starts_before

    def test_request_stop_from_ready_is_noop(self):
        engine = make_engine()
        engine.request_stop()
        engine.step(0.01)
        assert engine.state == UnitState.READY


# ============================================================
# FAULT HANDLING
# ============================================================

class TestFaultHandling:

    def test_raise_fault_transitions_to_fault_state(self):
        engine = make_engine()
        engine.raise_fault(1001)
        assert engine.state == UnitState.FAULT
        assert engine.fault_code == 1001

    def test_raise_fault_stops_engine_immediately(self):
        engine = make_engine()
        engine.request_start()
        run_until_state(engine, UnitState.RUNNING)

        engine.raise_fault(2001)
        assert engine.engine_speed_rpm == 0.0
        assert engine.throttle_position_percent == 0.0

    def test_request_start_blocked_in_fault_state(self):
        engine = make_engine()
        engine.raise_fault(1001)

        engine.request_start()
        engine.step(0.01)
        assert engine.state == UnitState.FAULT

    def test_acknowledge_fault_returns_to_ready(self):
        engine = make_engine()
        engine.raise_fault(1001)
        engine.acknowledge_fault()
        assert engine.state == UnitState.READY
        assert engine.fault_code == 0

    def test_can_start_after_fault_acknowledgment(self):
        engine = make_engine()
        engine.raise_fault(1001)
        engine.acknowledge_fault()
        engine.request_start()
        run_until_state(engine, UnitState.RUNNING)
        assert engine.state == UnitState.RUNNING


# ============================================================
# DYNAMICS — speed, temperatures, pressures
# ============================================================

class TestDynamics:

    def test_engine_speed_reaches_nominal_when_running(self):
        engine = make_engine()
        engine.request_start()
        run_until_state(engine, UnitState.RUNNING)

        # Allow speed to settle (tau=5s, need ~5 tau = 25s for <1% error)
        for _ in range(500):
            engine.step(0.1)

        assert engine.engine_speed_rpm == pytest.approx(EngineModel.NOMINAL_SPEED_RPM, rel=0.05)

    def test_coolant_warms_up_during_operation(self):
        engine = make_engine(ambient_c=20.0)
        initial_coolant = engine.coolant_outlet_c

        engine.request_start()
        run_until_state(engine, UnitState.RUNNING)

        # Allow thermal lag to settle
        for _ in range(2000):
            engine.step(0.1, load_factor=0.5)

        assert engine.coolant_outlet_c > initial_coolant
        assert engine.coolant_outlet_c == pytest.approx(EngineModel.NOMINAL_COOLANT_C, abs=5.0)

    def test_coolant_cools_after_stop(self):
        engine = make_engine(ambient_c=20.0)
        engine.request_start()
        run_until_state(engine, UnitState.RUNNING)

        # Heat up
        for _ in range(2000):
            engine.step(0.1, load_factor=0.8)
        hot_coolant = engine.coolant_outlet_c

        engine.request_stop()
        run_until_state(engine, UnitState.READY)

        # Continue cooling
        for _ in range(5000):
            engine.step(0.1)

        assert engine.coolant_outlet_c < hot_coolant

    def test_exhaust_temp_scales_with_load(self):
        engine = make_engine()
        engine.request_start()
        run_until_state(engine, UnitState.RUNNING)

        # Low load
        for _ in range(500):
            engine.step(0.1, load_factor=0.1)
        low_load_exhaust = engine.exhaust_temp_c

        # High load
        for _ in range(500):
            engine.step(0.1, load_factor=1.0)
        high_load_exhaust = engine.exhaust_temp_c

        assert high_load_exhaust > low_load_exhaust

    def test_oil_pressure_reaches_nominal_when_running(self):
        engine = make_engine()
        engine.request_start()
        run_until_state(engine, UnitState.RUNNING)

        for _ in range(100):
            engine.step(0.1)

        assert engine.oil_pressure_bar == pytest.approx(
            EngineModel.NOMINAL_OIL_PRESSURE_BAR, rel=0.1
        )

    def test_throttle_follows_load_factor(self):
        engine = make_engine()
        engine.request_start()
        run_until_state(engine, UnitState.RUNNING)

        for _ in range(200):
            engine.step(0.1, load_factor=0.75)

        assert engine.throttle_position_percent == pytest.approx(75.0, abs=2.0)


# ============================================================
# OPERATING HOURS
# ============================================================

class TestOperatingHours:

    def test_hours_only_accumulate_when_running(self):
        engine = make_engine()

        # Not running — hours stay zero
        for _ in range(100):
            engine.step(1.0)
        assert engine.operating_hours == 0.0

        # Now run for an hour of sim time
        engine.request_start()
        run_until_state(engine, UnitState.RUNNING)
        for _ in range(3600):
            engine.step(1.0)

        assert engine.operating_hours == pytest.approx(1.0, abs=0.01)

    def test_hours_pause_during_stop(self):
        engine = make_engine()
        engine.request_start()
        run_until_state(engine, UnitState.RUNNING)

        # Run for 30 minutes
        for _ in range(1800):
            engine.step(1.0)
        hours_after_run = engine.operating_hours

        engine.request_stop()
        run_until_state(engine, UnitState.READY)

        # Sit idle for another 30 minutes
        for _ in range(1800):
            engine.step(1.0)

        # Hours should not have advanced significantly (only by the brief stopping phase)
        assert engine.operating_hours == pytest.approx(hours_after_run, abs=0.01)


# ============================================================
# PROPERTIES
# ============================================================

class TestProperties:

    def test_is_running_only_in_running_state(self):
        engine = make_engine()
        assert not engine.is_running

        engine.request_start()
        run_until_state(engine, UnitState.RUNNING)
        assert engine.is_running

        engine.request_stop()
        run_until_state(engine, UnitState.READY)
        assert not engine.is_running

    def test_is_dispatchable_requires_running_and_no_fault(self):
        engine = make_engine()
        engine.request_start()
        run_until_state(engine, UnitState.RUNNING)
        assert engine.is_dispatchable

        engine.raise_fault(1001)
        assert not engine.is_dispatchable

    def test_circuit_breaker_closed_only_when_running(self):
        engine = make_engine()
        assert not engine.circuit_breaker_closed

        engine.request_start()
        run_until_state(engine, UnitState.SYNCHRONIZING)
        assert not engine.circuit_breaker_closed  # not yet

        run_until_state(engine, UnitState.RUNNING)
        assert engine.circuit_breaker_closed


# ============================================================
# AMBIENT TEMPERATURE TRACKING
# ============================================================

class TestAmbientTracking:

    def test_set_ambient_temperature_updates_target(self):
        engine = make_engine(ambient_c=20.0)
        engine.set_ambient_temperature(35.0)

        # Cycle through some idle time so intake air follows
        for _ in range(10000):
            engine.step(1.0)

        # Intake air should track ambient + a small offset
        assert engine.intake_air_temp_c == pytest.approx(35.0 + 5.0, abs=2.0)