import math
from dataclasses import dataclass, field
from enum import IntEnum


class UnitState(IntEnum):
    """
    CHP unit state machine.
    Values match MWM TEM Evolution register 30279.
    """
    FAULT = 0
    READY = 1
    STARTING = 2
    IDLE = 3
    SYNCHRONIZING = 4
    RUNNING = 5
    STOPPING = 6
    START_BLOCKED = 7
    RESTART = 8
    RESERVE = 9
    OIL_CHANGE = 10
    AUX_TEST = 11
    IGNITION_TEST = 12
    GOVERNOR_TEST = 13
    SC_SHUTDOWN = 14
    ISLAND_START = 15
    BLACK_START = 16
    FORCED_PRELUBE = 17
    WARMUP = 18
    CONTROL_TEST = 19


@dataclass
class StartupTimings:
    """
    Time in seconds for each startup phase transition.
    Defaults reflect typical TCG-class gas engine behavior.
    """
    starting_to_warmup_s: float = 10.0      # Cranking + ignition
    warmup_to_idle_s: float = 60.0          # Warmup until coolant reaches operating temp
    idle_to_sync_s: float = 30.0            # Idle stabilization
    sync_to_running_s: float = 15.0         # Grid sync + breaker close
    stopping_to_ready_s: float = 20.0       # Cooldown + breaker open


class EngineModel:
    """
    Gas engine model for CHP simulation.

    Responsibilities:
    - State machine (READY → STARTING → WARMUP → IDLE → SYNC → RUNNING)
    - Engine speed dynamics (ramps to nominal during startup)
    - Coolant temperature (rises during startup, stabilizes during running)
    - Oil/exhaust/intake temperatures (derived from coolant + load)
    - Operating hours and start counter accumulation
    - Fault state tracking

    Does NOT compute electrical power output — that's CHPModel's job.
    """

    NOMINAL_SPEED_RPM = 1500.0     # 50 Hz synchronous (4-pole), real engines run here
    NOMINAL_COOLANT_C = 85.0       # Typical CHP coolant operating temperature
    NOMINAL_OIL_C = 90.0
    NOMINAL_INTAKE_C = 25.0
    NOMINAL_EXHAUST_C = 450.0      # After catalyst, real units run 420–480 °C
    NOMINAL_OIL_PRESSURE_BAR = 4.5
    NOMINAL_CHARGE_PRESSURE_BAR = 2.0

    def __init__(
        self,
        ambient_temp_c: float = 20.0,
        timings: StartupTimings | None = None,
    ):
        # State
        self.state: UnitState = UnitState.READY
        self._state_elapsed_s: float = 0.0

        # Fault tracking
        self.fault_code: int = 0
        self.warning_code: int = 0

        # Engine dynamics
        self.engine_speed_rpm: float = 0.0
        self.throttle_position_percent: float = 0.0

        # Temperatures
        self.ambient_temp_c = ambient_temp_c
        self.coolant_outlet_c: float = ambient_temp_c
        self.coolant_inlet_c: float = ambient_temp_c
        self.oil_temperature_c: float = ambient_temp_c
        self.exhaust_temp_c: float = ambient_temp_c
        self.intake_air_temp_c: float = ambient_temp_c

        # Pressures
        self.oil_pressure_bar: float = 0.0
        self.charge_pressure_bar: float = 0.0

        # Counters
        self.operating_hours: float = 0.0
        self.start_counter: int = 0

        # External
        self.timings = timings or StartupTimings()

        # Internal
        self._start_requested = False
        self._stop_requested = False

    # =========================================================
    # Command interface
    # =========================================================

    def request_start(self):
        """Request engine startup. No-op if already running or fault."""
        if self.state == UnitState.FAULT:
            return
        if self.state in (UnitState.READY, UnitState.STOPPING):
            self._start_requested = True
            self._stop_requested = False

    def request_stop(self):
        """Request engine stop."""
        if self.state in (
            UnitState.RUNNING, UnitState.SYNCHRONIZING,
            UnitState.IDLE, UnitState.WARMUP, UnitState.STARTING,
        ):
            self._stop_requested = True
            self._start_requested = False

    def acknowledge_fault(self):
        """Clear fault state and return to READY if conditions allow."""
        if self.state == UnitState.FAULT:
            self.fault_code = 0
            self.warning_code = 0
            self.state = UnitState.READY
            self._state_elapsed_s = 0.0

    def raise_fault(self, code: int):
        """Trigger a fault — transitions to FAULT state."""
        self.fault_code = code
        self.state = UnitState.FAULT
        self._state_elapsed_s = 0.0
        self.engine_speed_rpm = 0.0
        self.throttle_position_percent = 0.0

    # =========================================================
    # Properties
    # =========================================================

    @property
    def is_running(self) -> bool:
        return self.state == UnitState.RUNNING

    @property
    def is_dispatchable(self) -> bool:
        """True if the engine can accept a power setpoint."""
        return self.state == UnitState.RUNNING and self.fault_code == 0

    @property
    def is_synchronized(self) -> bool:
        return self.state in (UnitState.SYNCHRONIZING, UnitState.RUNNING)

    @property
    def circuit_breaker_closed(self) -> bool:
        return self.state == UnitState.RUNNING

    # =========================================================
    # Simulation Step
    # =========================================================

    def step(self, dt: float, load_factor: float = 0.0):
        """
        Advance engine state by dt seconds.

        Args:
            load_factor: 0.0–1.0, current electrical load as fraction of rated.
                         Used by thermal model to set steady-state coolant temperature.
        """
        self._state_elapsed_s += dt
        self._advance_state_machine()
        self._update_dynamics(dt, load_factor)
        self._accumulate_counters(dt)

    # =========================================================
    # State Machine
    # =========================================================

    def _advance_state_machine(self):
        t = self.timings

        # Fault is sticky — only acknowledge_fault() clears it
        if self.state == UnitState.FAULT:
            return

        if self.state == UnitState.READY:
            if self._start_requested:
                self._transition_to(UnitState.STARTING)
                self.start_counter += 1
                self._start_requested = False

        elif self.state == UnitState.STARTING:
            if self._state_elapsed_s >= t.starting_to_warmup_s:
                self._transition_to(UnitState.WARMUP)

        elif self.state == UnitState.WARMUP:
            if self._state_elapsed_s >= t.warmup_to_idle_s:
                self._transition_to(UnitState.IDLE)
            elif self._stop_requested:
                self._transition_to(UnitState.STOPPING)

        elif self.state == UnitState.IDLE:
            if self._state_elapsed_s >= t.idle_to_sync_s:
                self._transition_to(UnitState.SYNCHRONIZING)
            elif self._stop_requested:
                self._transition_to(UnitState.STOPPING)

        elif self.state == UnitState.SYNCHRONIZING:
            if self._state_elapsed_s >= t.sync_to_running_s:
                self._transition_to(UnitState.RUNNING)
            elif self._stop_requested:
                self._transition_to(UnitState.STOPPING)

        elif self.state == UnitState.RUNNING:
            if self._stop_requested:
                self._transition_to(UnitState.STOPPING)

        elif self.state == UnitState.STOPPING:
            if self._state_elapsed_s >= t.stopping_to_ready_s:
                self._transition_to(UnitState.READY)
                self._stop_requested = False

    def _transition_to(self, new_state: UnitState):
        self.state = new_state
        self._state_elapsed_s = 0.0

    # =========================================================
    # Engine Dynamics — speed, temperatures, pressures
    # =========================================================

    def _update_dynamics(self, dt: float, load_factor: float):
        target_speed = self._target_speed_for_state()
        target_coolant = self._target_coolant_for_state(load_factor)
        target_oil_temp = self._target_oil_temp_for_state(load_factor)
        target_exhaust = self._target_exhaust_for_state(load_factor)
        target_oil_pressure = self._target_oil_pressure_for_state()
        target_charge_pressure = self._target_charge_pressure_for_state(load_factor)
        target_throttle = self._target_throttle_for_state(load_factor)

        # First-order lag toward each target. Time constants tuned for realistic feel.
        self.engine_speed_rpm = _lag(self.engine_speed_rpm, target_speed, dt, tau=5.0)
        self.coolant_outlet_c = _lag(self.coolant_outlet_c, target_coolant, dt, tau=60.0)
        self.coolant_inlet_c = _lag(self.coolant_inlet_c, target_coolant - 8.0, dt, tau=60.0)
        self.oil_temperature_c = _lag(self.oil_temperature_c, target_oil_temp, dt, tau=90.0)
        self.exhaust_temp_c = _lag(self.exhaust_temp_c, target_exhaust, dt, tau=15.0)
        self.oil_pressure_bar = _lag(self.oil_pressure_bar, target_oil_pressure, dt, tau=2.0)
        self.charge_pressure_bar = _lag(self.charge_pressure_bar, target_charge_pressure, dt, tau=5.0)
        self.throttle_position_percent = _lag(self.throttle_position_percent, target_throttle, dt, tau=3.0)

        # Intake air follows ambient with small thermal lag
        self.intake_air_temp_c = _lag(self.intake_air_temp_c, self.ambient_temp_c + 5.0, dt, tau=120.0)

    def _target_speed_for_state(self) -> float:
        if self.state in (UnitState.READY, UnitState.STOPPING, UnitState.FAULT):
            return 0.0
        if self.state == UnitState.STARTING:
            # Cranking — ramps from 0 to nominal during this phase
            progress = self._state_elapsed_s / max(self.timings.starting_to_warmup_s, 0.01)
            return self.NOMINAL_SPEED_RPM * min(1.0, progress)
        return self.NOMINAL_SPEED_RPM

    def _target_coolant_for_state(self, load_factor: float) -> float:
        if self.state in (UnitState.READY, UnitState.FAULT):
            return self.ambient_temp_c
        if self.state == UnitState.STOPPING:
            # Cooling down toward ambient
            return self.ambient_temp_c + 20.0
        # Warmer when running under load
        return self.NOMINAL_COOLANT_C + load_factor * 5.0

    def _target_oil_temp_for_state(self, load_factor: float) -> float:
        if self.state in (UnitState.READY, UnitState.FAULT):
            return self.ambient_temp_c
        if self.state == UnitState.STOPPING:
            return self.ambient_temp_c + 15.0
        return self.NOMINAL_OIL_C + load_factor * 5.0

    def _target_exhaust_for_state(self, load_factor: float) -> float:
        if self.state in (UnitState.READY, UnitState.FAULT, UnitState.STOPPING):
            return self.ambient_temp_c
        if not self.is_running:
            return 200.0  # Idle/sync exhaust temperature
        # Exhaust temp scales strongly with load
        return self.NOMINAL_EXHAUST_C * (0.5 + 0.5 * load_factor)

    def _target_oil_pressure_for_state(self) -> float:
        if self.state in (UnitState.READY, UnitState.FAULT):
            return 0.0
        return self.NOMINAL_OIL_PRESSURE_BAR

    def _target_charge_pressure_for_state(self, load_factor: float) -> float:
        if not self.is_running:
            return 0.0
        return self.NOMINAL_CHARGE_PRESSURE_BAR * load_factor

    def _target_throttle_for_state(self, load_factor: float) -> float:
        if not self.is_running:
            return 0.0
        return 100.0 * load_factor

    # =========================================================
    # Counters
    # =========================================================

    def _accumulate_counters(self, dt: float):
        if self.is_running:
            self.operating_hours += dt / 3600.0

    # =========================================================
    # External inputs
    # =========================================================

    def set_ambient_temperature(self, temp_c: float):
        self.ambient_temp_c = float(temp_c)


def _lag(current: float, target: float, dt: float, tau: float) -> float:
    """First-order lag: current moves toward target with time constant tau."""
    if tau <= 0:
        return target
    alpha = 1.0 - math.exp(-dt / tau)
    return current + alpha * (target - current)