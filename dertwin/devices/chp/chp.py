from dertwin.devices.chp.engine import EngineModel, UnitState
from dertwin.telemetry.chp import CHPTelemetry


class CHPModel:
    """
    Combined Heat and Power unit composition.

    Composes:
    - EngineModel — state machine + thermal physics
    - Electrical power output (% of rated, ramp-limited)
    - Heat power output (derived from electrical via heat_to_power_ratio)

    The controller sets `target_power_percent`; CHPModel applies ramp limiting
    and produces electrical_power_kw / heat_power_kw based on engine state.
    """

    def __init__(
        self,
        engine: EngineModel,
        rated_kw: float = 4000.0,
        heat_to_power_ratio: float = 1.0,
        ramp_rate_percent_per_s: float = 5.0,
        min_load_percent: float = 30.0,
        max_load_percent: float = 110.0,
    ):
        self.engine = engine
        self.rated_kw = rated_kw
        self.heat_to_power_ratio = heat_to_power_ratio
        self.ramp_rate_percent_per_s = ramp_rate_percent_per_s
        self.min_load_percent = min_load_percent
        self.max_load_percent = max_load_percent

        # Power state
        self.target_power_percent: float = 0.0
        self.actual_power_percent: float = 0.0
        self.permitted_power_percent: float = 100.0

    # =========================================================
    # Command interface (called by controller)
    # =========================================================

    def set_power_setpoint_percent(self, percent: float):
        """
        Set electrical power setpoint as a percentage of rated.
        Clamped to [min_load_percent, max_load_percent].
        Below min_load_percent is interpreted as "stay at minimum" rather than
        zero, because real CHPs cannot run below their minimum load while
        synchronized — they would trip.
        """
        if percent <= 0.0:
            self.target_power_percent = 0.0
            return
        percent = max(self.min_load_percent, min(self.max_load_percent, percent))
        self.target_power_percent = percent

    # =========================================================
    # Properties
    # =========================================================

    @property
    def electrical_power_kw(self) -> float:
        return (self.actual_power_percent / 100.0) * self.rated_kw

    @property
    def heat_power_kw(self) -> float:
        return self.electrical_power_kw * self.heat_to_power_ratio

    @property
    def load_factor(self) -> float:
        """Current load as fraction of rated power (0.0–1.1)."""
        return self.actual_power_percent / 100.0

    # =========================================================
    # Simulation Step
    # =========================================================

    def step(self, dt: float) -> CHPTelemetry:
        # 1. Compute commanded power based on engine state
        if self.engine.is_dispatchable:
            # Limit setpoint to permitted_power (derating from temperature etc.)
            effective_target = min(self.target_power_percent, self.permitted_power_percent)
        elif self.engine.is_synchronized:
            # Synchronized but not yet running — power must be zero
            effective_target = 0.0
        else:
            effective_target = 0.0

        # 2. Apply ramp limit
        delta = effective_target - self.actual_power_percent
        max_delta = self.ramp_rate_percent_per_s * dt
        if abs(delta) > max_delta:
            delta = max_delta if delta > 0 else -max_delta
        self.actual_power_percent += delta
        self.actual_power_percent = max(0.0, self.actual_power_percent)

        # Step the engine with the current load factor
        self.engine.step(dt, load_factor=self.load_factor)

        # Emit telemetry
        return self._build_telemetry()

    # =========================================================
    # Telemetry
    # =========================================================

    def _build_telemetry(self) -> CHPTelemetry:
        eng = self.engine
        ops_hours_int = int(eng.operating_hours)
        starts = eng.start_counter

        return CHPTelemetry(
            # State
            unit_state=int(eng.state),

            # Power
            actual_power_percent=self.actual_power_percent,
            actual_power_kw=self.electrical_power_kw,
            permitted_power_percent=self.permitted_power_percent,
            heat_power_kw=self.heat_power_kw,

            # Engine
            engine_speed_rpm=eng.engine_speed_rpm,
            throttle_position=eng.throttle_position_percent,

            # Temperatures
            coolant_outlet_temp=eng.coolant_outlet_c,
            coolant_inlet_temp=eng.coolant_inlet_c,
            exhaust_temp_after_catalyst=eng.exhaust_temp_c,
            oil_temperature=eng.oil_temperature_c,
            intake_air_temp=eng.intake_air_temp_c,

            # Pressures
            oil_pressure=eng.oil_pressure_bar,
            charge_pressure=eng.charge_pressure_bar,

            # Counters (split MWM convention)
            operating_hours=ops_hours_int % 10000,
            operating_hours_10000=ops_hours_int // 10000,
            start_counter=starts % 10000,
            start_counter_10000=starts // 10000,

            # Discrete flags
            engine_running=eng.is_running,
            circuit_breaker_closed=eng.circuit_breaker_closed,
            collective_fault=eng.fault_code != 0,
            collective_warning=eng.warning_code != 0,
            auto_mode=True,
            e_stop_request=False,
            power_supply_failure=False,
            ignition_on=eng.state in (UnitState.STARTING, UnitState.WARMUP,
                                      UnitState.IDLE, UnitState.SYNCHRONIZING,
                                      UnitState.RUNNING),
            starter_on=eng.state == UnitState.STARTING,
            prelube_pump_on=eng.state in (UnitState.STARTING, UnitState.FORCED_PRELUBE),
            preheat_on=eng.state == UnitState.WARMUP,
        )