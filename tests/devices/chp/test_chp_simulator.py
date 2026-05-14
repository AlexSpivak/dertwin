import pytest

from dertwin.devices.chp.engine import StartupTimings, UnitState
from dertwin.devices.chp.simulator import CHPSimulator
from dertwin.devices.chp.controller import ACKNOWLEDGMENT_MAGIC
from dertwin.devices.external.ambient_temperature import AmbientTemperatureModel
from dertwin.telemetry.chp import CHPTelemetry


# ============================================================
# FIXTURES
# ============================================================

def make_simulator(**overrides) -> CHPSimulator:
    defaults = dict(
        rated_kw=4000.0,
        heat_to_power_ratio=1.0,
        ramp_rate_percent_per_s=100.0,
        startup_timings=StartupTimings(
            starting_to_warmup_s=1.0,
            warmup_to_idle_s=2.0,
            idle_to_sync_s=1.0,
            sync_to_running_s=1.0,
            stopping_to_ready_s=1.0,
        ),
    )
    defaults.update(overrides)
    return CHPSimulator(**defaults)


def run_until_state(sim: CHPSimulator, target_state: UnitState, dt: float = 0.1, max_steps: int = 1000):
    for _ in range(max_steps):
        sim.update(dt)
        if sim.engine.state == target_state:
            return
    raise AssertionError(
        f"CHP never reached {target_state.name}, stuck in {sim.engine.state.name}"
    )


# ============================================================
# INITIALIZATION
# ============================================================

class TestInit:

    def test_rated_kw_propagates(self):
        sim = make_simulator(rated_kw=2500.0)
        assert sim.rated_kw == 2500.0
        assert sim.chp.rated_kw == 2500.0

    def test_heat_ratio_propagates(self):
        sim = make_simulator(heat_to_power_ratio=1.3)
        assert sim.chp.heat_to_power_ratio == 1.3

    def test_initial_state_is_ready(self):
        sim = make_simulator()
        assert sim.state == UnitState.READY
        assert not sim.is_running

    def test_external_models_optional(self):
        # Should construct without any external models
        sim = make_simulator()
        assert sim.ambient_temp_model is None
        assert sim.grid_frequency_model is None
        assert sim.grid_voltage_model is None


# ============================================================
# COMPATIBILITY PROPERTIES
# ============================================================

class TestProperties:

    def test_is_running_property(self):
        sim = make_simulator()
        assert not sim.is_running

        sim.apply_commands({"start_stop": 1})
        run_until_state(sim, UnitState.RUNNING)
        assert sim.is_running

    def test_electrical_and_heat_power_zero_initially(self):
        sim = make_simulator()
        assert sim.electrical_power_kw == 0.0
        assert sim.heat_power_kw == 0.0

    def test_electrical_power_at_full_load(self):
        sim = make_simulator(rated_kw=4000.0)
        sim.apply_commands({"start_stop": 1})
        run_until_state(sim, UnitState.RUNNING)

        sim.apply_commands({"power_setpoint_percent": 100.0})
        for _ in range(50):
            sim.update(0.1)

        assert sim.electrical_power_kw == pytest.approx(4000.0, abs=50.0)

    def test_heat_power_uses_configured_ratio(self):
        sim = make_simulator(rated_kw=4000.0, heat_to_power_ratio=1.2)
        sim.apply_commands({"start_stop": 1})
        run_until_state(sim, UnitState.RUNNING)

        sim.apply_commands({"power_setpoint_percent": 50.0})
        for _ in range(50):
            sim.update(0.1)

        assert sim.heat_power_kw == pytest.approx(2400.0, abs=50.0)

    def test_fault_code_setter_raises_fault(self):
        sim = make_simulator()
        sim.fault_code = 1001
        assert sim.state == UnitState.FAULT
        assert sim.fault_code == 1001

    def test_fault_code_setter_to_zero_clears_fault(self):
        sim = make_simulator()
        sim.fault_code = 1001
        sim.fault_code = 0
        assert sim.state == UnitState.READY
        assert sim.fault_code == 0


# ============================================================
# COMMAND APPLICATION VIA SIMULATOR API
# ============================================================

class TestCommands:

    def test_start_via_apply_commands(self):
        sim = make_simulator()
        sim.apply_commands({"start_stop": 1})
        run_until_state(sim, UnitState.RUNNING)
        assert sim.is_running

    def test_setpoint_via_apply_commands(self):
        sim = make_simulator(ramp_rate_percent_per_s=100.0)
        sim.apply_commands({"start_stop": 1})
        run_until_state(sim, UnitState.RUNNING)

        sim.apply_commands({"power_setpoint_percent": 80.0})
        for _ in range(50):
            sim.update(0.1)

        assert sim.actual_power_percent == pytest.approx(80.0, abs=1.0)

    def test_fault_recovery_via_acknowledgment(self):
        sim = make_simulator()
        sim.fault_code = 1001
        assert sim.state == UnitState.FAULT

        sim.apply_commands({"remote_acknowledgment": ACKNOWLEDGMENT_MAGIC})
        assert sim.state == UnitState.READY

    def test_full_dispatch_lifecycle(self):
        sim = make_simulator(ramp_rate_percent_per_s=100.0)

        # Start
        sim.apply_commands({"start_stop": 1})
        run_until_state(sim, UnitState.RUNNING)

        # Dispatch
        sim.apply_commands({"power_setpoint_percent": 60.0})
        for _ in range(50):
            sim.update(0.1)
        assert sim.actual_power_percent == pytest.approx(60.0, abs=1.0)

        # Re-dispatch
        sim.apply_commands({"power_setpoint_percent": 80.0})
        for _ in range(50):
            sim.update(0.1)
        assert sim.actual_power_percent == pytest.approx(80.0, abs=1.0)

        # Stop
        sim.apply_commands({"start_stop": 0})
        run_until_state(sim, UnitState.READY)
        assert sim.actual_power_percent == pytest.approx(0.0, abs=1.0)


# ============================================================
# TELEMETRY
# ============================================================

class TestTelemetry:

    def test_get_telemetry_returns_chp_telemetry(self):
        sim = make_simulator()
        sim.update(0.1)
        telemetry = sim.get_telemetry()
        assert isinstance(telemetry, CHPTelemetry)

    def test_telemetry_state_tracks_engine(self):
        sim = make_simulator()
        sim.update(0.1)
        assert sim.get_telemetry().unit_state == int(UnitState.READY)

        sim.apply_commands({"start_stop": 1})
        run_until_state(sim, UnitState.RUNNING)
        assert sim.get_telemetry().unit_state == int(UnitState.RUNNING)

    def test_telemetry_includes_power_in_kw(self):
        sim = make_simulator(rated_kw=2000.0, ramp_rate_percent_per_s=100.0)
        sim.apply_commands({"start_stop": 1})
        run_until_state(sim, UnitState.RUNNING)

        sim.apply_commands({"power_setpoint_percent": 50.0})
        for _ in range(50):
            sim.update(0.1)

        telemetry = sim.get_telemetry()
        assert telemetry.actual_power_kw == pytest.approx(1000.0, abs=20.0)
        assert telemetry.actual_power_percent == pytest.approx(50.0, abs=1.0)


# ============================================================
# EXTERNAL MODEL INTEGRATION
# ============================================================

class TestExternalModels:

    def test_ambient_temperature_model_propagates(self):
        ambient = AmbientTemperatureModel(mean_temp_c=30.0, amplitude_c=0.0)
        sim = make_simulator(ambient_temp_model=ambient)

        sim.update(0.1)
        assert sim.engine.ambient_temp_c == pytest.approx(30.0, abs=0.1)
