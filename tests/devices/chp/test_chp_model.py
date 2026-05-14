import pytest

from dertwin.devices.chp.engine import EngineModel, StartupTimings, UnitState
from dertwin.devices.chp.chp import CHPModel


# ============================================================
# FIXTURES
# ============================================================

def make_chp(
    rated_kw: float = 4000.0,
    heat_to_power_ratio: float = 1.0,
    ramp_rate: float = 100.0,
    min_load: float = 30.0,
    max_load: float = 110.0,
) -> CHPModel:
    """Make a CHP with fast startup timings for testing."""
    engine = EngineModel(
        timings=StartupTimings(
            starting_to_warmup_s=1.0,
            warmup_to_idle_s=2.0,
            idle_to_sync_s=1.0,
            sync_to_running_s=1.0,
            stopping_to_ready_s=1.0,
        ),
    )
    return CHPModel(
        engine=engine,
        rated_kw=rated_kw,
        heat_to_power_ratio=heat_to_power_ratio,
        ramp_rate_percent_per_s=ramp_rate,
        min_load_percent=min_load,
        max_load_percent=max_load,
    )


def run_until_state(chp: CHPModel, target_state: UnitState, dt: float = 0.1, max_steps: int = 1000):
    for _ in range(max_steps):
        chp.step(dt)
        if chp.engine.state == target_state:
            return
    raise AssertionError(
        f"CHP never reached {target_state.name}, stuck in {chp.engine.state.name}"
    )


# ============================================================
# INITIAL STATE
# ============================================================

class TestInitialState:

    def test_zero_power_initially(self):
        chp = make_chp()
        assert chp.actual_power_percent == 0.0
        assert chp.target_power_percent == 0.0

    def test_permitted_power_at_full_initially(self):
        chp = make_chp()
        assert chp.permitted_power_percent == 100.0

    def test_electrical_and_heat_power_zero_initially(self):
        chp = make_chp()
        assert chp.electrical_power_kw == 0.0
        assert chp.heat_power_kw == 0.0


# ============================================================
# SETPOINT CLAMPING
# ============================================================

class TestSetpointClamping:

    def test_setpoint_within_range_accepted(self):
        chp = make_chp(min_load=30.0, max_load=110.0)
        chp.set_power_setpoint_percent(50.0)
        assert chp.target_power_percent == 50.0

    def test_setpoint_above_max_clamped(self):
        chp = make_chp(max_load=110.0)
        chp.set_power_setpoint_percent(150.0)
        assert chp.target_power_percent == 110.0

    def test_setpoint_below_min_clamped_to_min(self):
        """Real CHPs trip below min load — setpoint between 0 and min should
        be interpreted as 'stay at minimum', not 'shut down'."""
        chp = make_chp(min_load=30.0)
        chp.set_power_setpoint_percent(10.0)
        assert chp.target_power_percent == 30.0

    def test_zero_setpoint_sets_target_to_zero(self):
        """Setpoint = 0 explicitly means 'no dispatch' (distinct from below-min)."""
        chp = make_chp()
        chp.set_power_setpoint_percent(50.0)
        chp.set_power_setpoint_percent(0.0)
        assert chp.target_power_percent == 0.0

    def test_negative_setpoint_treated_as_zero(self):
        chp = make_chp()
        chp.set_power_setpoint_percent(-10.0)
        assert chp.target_power_percent == 0.0


# ============================================================
# POWER DISPATCH GATED BY ENGINE STATE
# ============================================================

class TestPowerDispatchGating:

    def test_setpoint_ignored_when_not_running(self):
        chp = make_chp()
        chp.set_power_setpoint_percent(75.0)

        # Engine still in READY — should not produce power
        for _ in range(50):
            chp.step(0.1)

        assert chp.actual_power_percent == 0.0
        assert chp.electrical_power_kw == 0.0

    def test_setpoint_applied_after_engine_running(self):
        chp = make_chp(ramp_rate=100.0)
        chp.engine.request_start()
        run_until_state(chp, UnitState.RUNNING)

        chp.set_power_setpoint_percent(80.0)

        for _ in range(50):
            chp.step(0.1)

        assert chp.actual_power_percent == pytest.approx(80.0, abs=1.0)

    def test_zero_power_during_synchronizing(self):
        chp = make_chp()
        chp.set_power_setpoint_percent(50.0)
        chp.engine.request_start()
        run_until_state(chp, UnitState.SYNCHRONIZING)

        # During sync, power must be zero — breaker not closed yet
        assert chp.actual_power_percent == 0.0


# ============================================================
# RAMP RATE LIMITING
# ============================================================

class TestRampRate:

    def test_ramp_rate_limits_power_change(self):
        chp = make_chp(ramp_rate=5.0)  # 5% per second
        chp.engine.request_start()
        run_until_state(chp, UnitState.RUNNING)

        chp.set_power_setpoint_percent(100.0)

        # After 1 second, power should be ~5%
        for _ in range(10):
            chp.step(0.1)

        assert chp.actual_power_percent == pytest.approx(5.0, abs=0.5)

    def test_ramp_reaches_target_eventually(self):
        chp = make_chp(ramp_rate=10.0)
        chp.engine.request_start()
        run_until_state(chp, UnitState.RUNNING)

        chp.set_power_setpoint_percent(80.0)

        # 10 seconds at 10%/s — should reach 80%
        for _ in range(150):
            chp.step(0.1)

        assert chp.actual_power_percent == pytest.approx(80.0, abs=1.0)


# ============================================================
# DERATING (permitted_power < setpoint)
# ============================================================

class TestDerating:

    def test_permitted_power_limits_actual_power(self):
        chp = make_chp(ramp_rate=100.0)
        chp.engine.request_start()
        run_until_state(chp, UnitState.RUNNING)

        chp.set_power_setpoint_percent(100.0)
        chp.permitted_power_percent = 70.0

        for _ in range(50):
            chp.step(0.1)

        assert chp.actual_power_percent == pytest.approx(70.0, abs=1.0)

    def test_actual_recovers_when_derating_lifts(self):
        chp = make_chp(ramp_rate=100.0)
        chp.engine.request_start()
        run_until_state(chp, UnitState.RUNNING)

        chp.set_power_setpoint_percent(100.0)
        chp.permitted_power_percent = 60.0

        for _ in range(50):
            chp.step(0.1)
        assert chp.actual_power_percent == pytest.approx(60.0, abs=1.0)

        chp.permitted_power_percent = 100.0
        for _ in range(50):
            chp.step(0.1)
        assert chp.actual_power_percent == pytest.approx(100.0, abs=1.0)


# ============================================================
# ELECTRICAL AND HEAT POWER
# ============================================================

class TestPowerOutputs:

    def test_electrical_power_scales_with_rated_kw(self):
        chp = make_chp(rated_kw=2000.0, ramp_rate=100.0)
        chp.engine.request_start()
        run_until_state(chp, UnitState.RUNNING)

        chp.set_power_setpoint_percent(50.0)
        for _ in range(50):
            chp.step(0.1)

        assert chp.electrical_power_kw == pytest.approx(1000.0, abs=20.0)

    def test_heat_power_uses_ratio(self):
        chp = make_chp(rated_kw=4000.0, heat_to_power_ratio=1.2, ramp_rate=100.0)
        chp.engine.request_start()
        run_until_state(chp, UnitState.RUNNING)

        chp.set_power_setpoint_percent(50.0)
        for _ in range(50):
            chp.step(0.1)

        expected_electrical = 2000.0
        expected_heat = expected_electrical * 1.2

        assert chp.electrical_power_kw == pytest.approx(expected_electrical, abs=50.0)
        assert chp.heat_power_kw == pytest.approx(expected_heat, abs=50.0)

    def test_heat_zero_when_not_running(self):
        chp = make_chp(heat_to_power_ratio=1.0)
        chp.set_power_setpoint_percent(80.0)

        for _ in range(50):
            chp.step(0.1)

        assert chp.heat_power_kw == 0.0

    def test_zero_heat_ratio_means_electrical_only(self):
        chp = make_chp(heat_to_power_ratio=0.0, ramp_rate=100.0)
        chp.engine.request_start()
        run_until_state(chp, UnitState.RUNNING)

        chp.set_power_setpoint_percent(80.0)
        for _ in range(50):
            chp.step(0.1)

        assert chp.electrical_power_kw > 0.0
        assert chp.heat_power_kw == 0.0


# ============================================================
# TELEMETRY OUTPUT
# ============================================================

class TestTelemetry:

    def test_telemetry_reflects_running_state(self):
        chp = make_chp(ramp_rate=100.0)
        chp.engine.request_start()
        run_until_state(chp, UnitState.RUNNING)

        chp.set_power_setpoint_percent(60.0)
        for _ in range(50):
            telemetry = chp.step(0.1)

        assert telemetry.unit_state == int(UnitState.RUNNING)
        assert telemetry.engine_running is True
        assert telemetry.circuit_breaker_closed is True
        assert telemetry.actual_power_percent == pytest.approx(60.0, abs=1.0)

    def test_telemetry_reflects_ready_state(self):
        chp = make_chp()
        telemetry = chp.step(0.1)

        assert telemetry.unit_state == int(UnitState.READY)
        assert telemetry.engine_running is False
        assert telemetry.circuit_breaker_closed is False
        assert telemetry.actual_power_kw == 0.0
        assert telemetry.heat_power_kw == 0.0

    def test_telemetry_reflects_fault_state(self):
        chp = make_chp()
        chp.engine.raise_fault(1001)
        telemetry = chp.step(0.1)

        assert telemetry.unit_state == int(UnitState.FAULT)
        assert telemetry.collective_fault is True
        assert telemetry.actual_power_kw == 0.0

    def test_telemetry_operating_hours_split(self):
        """MWM convention: operating_hours mod 10000 + operating_hours_10000 high part."""
        chp = make_chp()
        chp.engine.operating_hours = 12345.0
        telemetry = chp.step(0.1)

        assert telemetry.operating_hours == 2345  # 12345 % 10000
        assert telemetry.operating_hours_10000 == 1  # 12345 // 10000

    def test_telemetry_start_counter_split(self):
        chp = make_chp()
        chp.engine.start_counter = 25_678
        telemetry = chp.step(0.1)

        assert telemetry.start_counter == 5_678
        assert telemetry.start_counter_10000 == 2