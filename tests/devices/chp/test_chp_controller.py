import pytest

from dertwin.devices.chp.engine import EngineModel, StartupTimings, UnitState
from dertwin.devices.chp.chp import CHPModel
from dertwin.devices.chp.controller import CHPController, ACKNOWLEDGMENT_MAGIC


# ============================================================
# FIXTURES
# ============================================================

def make_controller(
    rated_kw: float = 4000.0,
    ramp_rate: float = 100.0,
) -> CHPController:
    engine = EngineModel(
        timings=StartupTimings(
            starting_to_warmup_s=1.0,
            warmup_to_idle_s=2.0,
            idle_to_sync_s=1.0,
            sync_to_running_s=1.0,
            stopping_to_ready_s=1.0,
        ),
    )
    chp = CHPModel(
        engine=engine,
        rated_kw=rated_kw,
        ramp_rate_percent_per_s=ramp_rate,
    )
    return CHPController(chp)


def run_until_state(controller: CHPController, target_state: UnitState, dt: float = 0.1, max_steps: int = 1000):
    for _ in range(max_steps):
        controller.step(dt)
        if controller.chp.engine.state == target_state:
            return
    raise AssertionError(
        f"CHP never reached {target_state.name}, stuck in {controller.chp.engine.state.name}"
    )


# ============================================================
# START / STOP COMMAND
# ============================================================

class TestStartStop:

    def test_start_command_triggers_startup(self):
        controller = make_controller()
        controller.apply_commands({"start_stop": 1})
        controller.step(0.1)
        assert controller.chp.engine.state == UnitState.STARTING

    def test_stop_command_during_running_triggers_stop(self):
        controller = make_controller()
        controller.apply_commands({"start_stop": 1})
        run_until_state(controller, UnitState.RUNNING)

        controller.apply_commands({"start_stop": 0})
        controller.step(0.1)
        assert controller.chp.engine.state == UnitState.STOPPING

    def test_full_lifecycle_via_commands(self):
        controller = make_controller()

        # Start
        controller.apply_commands({"start_stop": 1})
        run_until_state(controller, UnitState.RUNNING)

        # Stop
        controller.apply_commands({"start_stop": 0})
        run_until_state(controller, UnitState.READY)
        assert controller.chp.engine.state == UnitState.READY


# ============================================================
# POWER SETPOINT
# ============================================================

class TestPowerSetpoint:

    def test_setpoint_within_range_applied(self):
        controller = make_controller()
        applied = controller.apply_commands({"power_setpoint_percent": 60.0})
        assert applied.get("power_setpoint_percent") == 60.0
        assert controller.chp.target_power_percent == 60.0

    def test_setpoint_above_110_clamped(self):
        controller = make_controller()
        controller.apply_commands({"power_setpoint_percent": 150.0})
        assert controller.chp.target_power_percent == 110.0

    def test_setpoint_negative_clamped_to_zero(self):
        controller = make_controller()
        controller.apply_commands({"power_setpoint_percent": -10.0})
        assert controller.chp.target_power_percent == 0.0

    def test_setpoint_dispatched_when_running(self):
        controller = make_controller(ramp_rate=100.0)
        controller.apply_commands({"start_stop": 1})
        run_until_state(controller, UnitState.RUNNING)

        controller.apply_commands({"power_setpoint_percent": 75.0})

        for _ in range(50):
            controller.step(0.1)

        assert controller.chp.actual_power_percent == pytest.approx(75.0, abs=1.0)


# ============================================================
# CHANGE DETECTION
# ============================================================

class TestChangeDetection:

    def test_same_setpoint_twice_only_applies_once(self):
        controller = make_controller()
        applied1 = controller.apply_commands({"power_setpoint_percent": 50.0})
        applied2 = controller.apply_commands({"power_setpoint_percent": 50.0})

        assert applied1 == {"power_setpoint_percent": 50.0}
        assert applied2 == {}

    def test_different_setpoint_triggers_apply(self):
        controller = make_controller()
        controller.apply_commands({"power_setpoint_percent": 50.0})
        applied = controller.apply_commands({"power_setpoint_percent": 70.0})

        assert applied == {"power_setpoint_percent": 70.0}
        assert controller.chp.target_power_percent == 70.0

    def test_start_stop_change_doesnt_reset_setpoint(self):
        """
        Critical: applying start_stop=1 must NOT re-trigger the power_setpoint
        side effect (and vice versa). This is the bug we hit in PVController.
        """
        controller = make_controller()
        controller.apply_commands({"power_setpoint_percent": 60.0})
        applied = controller.apply_commands({
            "power_setpoint_percent": 60.0,
            "start_stop": 1,
        })

        # Only start_stop should be in applied
        assert "power_setpoint_percent" not in applied
        assert applied.get("start_stop") == 1


# ============================================================
# REMOTE ACKNOWLEDGMENT
# ============================================================

class TestRemoteAcknowledgment:

    def test_magic_value_clears_fault(self):
        controller = make_controller()
        controller.chp.engine.raise_fault(1001)
        assert controller.chp.engine.state == UnitState.FAULT

        controller.apply_commands({"remote_acknowledgment": ACKNOWLEDGMENT_MAGIC})

        assert controller.chp.engine.state == UnitState.READY
        assert controller.chp.engine.fault_code == 0

    def test_wrong_value_does_not_clear_fault(self):
        controller = make_controller()
        controller.chp.engine.raise_fault(1001)

        controller.apply_commands({"remote_acknowledgment": 1})  # not the magic value

        assert controller.chp.engine.state == UnitState.FAULT
        assert controller.chp.engine.fault_code == 1001

    def test_acknowledgment_always_runs_even_if_repeated(self):
        """Remote acknowledgment is stateless — must fire on every write."""
        controller = make_controller()
        controller.chp.engine.raise_fault(1001)
        controller.apply_commands({"remote_acknowledgment": ACKNOWLEDGMENT_MAGIC})

        # New fault arises
        controller.chp.engine.raise_fault(2001)
        assert controller.chp.engine.state == UnitState.FAULT

        # Same ack value clears it again
        controller.apply_commands({"remote_acknowledgment": ACKNOWLEDGMENT_MAGIC})
        assert controller.chp.engine.state == UnitState.READY


# ============================================================
# INIT APPLIED COMMANDS
# ============================================================

class TestInitAppliedCommands:

    def test_init_stores_baseline(self):
        controller = make_controller()
        controller.init_applied_commands({"power_setpoint_percent": 80.0, "start_stop": 1})

        # Subsequent apply_commands with same values should be no-ops
        applied = controller.apply_commands({"power_setpoint_percent": 80.0, "start_stop": 1})
        assert applied == {}

    def test_init_with_none_safe(self):
        controller = make_controller()
        controller.init_applied_commands(None)
        # Should not raise
        applied = controller.apply_commands({"power_setpoint_percent": 50.0})
        assert "power_setpoint_percent" in applied


# ============================================================
# UNKNOWN COMMANDS
# ============================================================

class TestUnknownCommands:

    def test_unknown_command_silently_ignored(self):
        controller = make_controller()
        applied = controller.apply_commands({"unknown_command": 42})
        assert applied == {}

    def test_mixed_known_and_unknown_only_applies_known(self):
        controller = make_controller()
        applied = controller.apply_commands({
            "power_setpoint_percent": 50.0,
            "garbage": 999,
        })
        assert applied == {"power_setpoint_percent": 50.0}