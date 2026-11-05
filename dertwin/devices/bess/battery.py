import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


LIMIT_MODE_HARD = "hard"
LIMIT_MODE_TAPER = "taper"
VALID_LIMIT_MODES = (LIMIT_MODE_HARD, LIMIT_MODE_TAPER)

# Temperature capability ladder, as (upper bound °C, factor) in increasing
# order of temperature: the first band whose bound the cell temperature does
# not exceed wins. These are the values the curve has always had.
DEFAULT_DERATE_BANDS = (
    (0.0, 0.5),
    (10.0, 0.8),
    (40.0, 1.0),
    (50.0, 0.8),
    (60.0, 0.5),
)
DEFAULT_DERATE_ABOVE_FACTOR = 0.0

# Suggested setpoints for a managed container, wide enough to sit inside the
# full-capability band of the default ladder with margin at both ends.
DEFAULT_HEAT_SETPOINT_C = 15.0
DEFAULT_COOL_SETPOINT_C = 30.0

# Hard ceiling on cell temperature, managed or not.
MAX_CELL_TEMP_C = 80.0


@dataclass(frozen=True)
class TemperatureDerate:
    """
    Cell-temperature capability curve.

    `bands` is an increasing ladder of (upper bound °C, factor): the factor of
    the first band whose bound the temperature does not exceed. `above_factor`
    applies above the last bound. The defaults are the pre-0.2.2 hardcoded
    curve, so leaving this alone changes nothing:

        ≤ 0 °C → 0.5,  ≤ 10 → 0.8,  ≤ 40 → 1.0,  ≤ 50 → 0.8,
        ≤ 60 → 0.5,  above → 0.0

    The factor scales both charge and discharge capability.
    """

    bands: tuple[tuple[float, float], ...] = DEFAULT_DERATE_BANDS
    above_factor: float = DEFAULT_DERATE_ABOVE_FACTOR

    def __post_init__(self):
        if not self.bands:
            raise ValueError("thermal.derate.bands needs at least one band")

        last_bound = None
        for bound, factor in self.bands:
            if last_bound is not None and bound <= last_bound:
                raise ValueError(
                    "thermal.derate.bands bounds must increase strictly, got "
                    f"{bound} after {last_bound}"
                )
            if not 0.0 <= factor <= 1.0:
                raise ValueError(
                    f"thermal.derate band factor must be in [0, 1], got {factor}"
                )
            last_bound = bound

        if not 0.0 <= self.above_factor <= 1.0:
            raise ValueError(
                "thermal.derate.above_factor must be in [0, 1], got "
                f"{self.above_factor}"
            )

    @classmethod
    def from_config(cls, config: dict | None) -> "TemperatureDerate":
        cfg = dict(config or {})

        bands = cfg.get("bands")
        if bands is None:
            bands_tuple = DEFAULT_DERATE_BANDS
        else:
            bands_tuple = tuple(
                (float(bound), float(factor)) for bound, factor in bands
            )

        return cls(
            bands=bands_tuple,
            above_factor=float(
                cfg.get("above_factor", DEFAULT_DERATE_ABOVE_FACTOR)
            ),
        )

    def factor_at(self, temperature_c: float) -> float:
        for bound, factor in self.bands:
            if temperature_c <= bound:
                return factor
        return self.above_factor


@dataclass(frozen=True)
class BatteryThermalConfig:
    """
    Cell thermal management, plus the capability curve it defends.

    With `managed=False` — the default — nothing here changes behaviour: the
    pack follows its own first-order thermal dynamics and the capability curve
    is the one it has always had.

    With `managed=True` the pack sits in a container whose HVAC holds the cell
    temperature inside [`heat_setpoint_c`, `cool_setpoint_c`]. Inside the band
    the cell relaxes toward ambient with time constant `time_constant_s`; it is
    never allowed to leave the band, so a site whose ambient walks through the
    cold derate keeps full capability. `aux_power_kw` is the container's own
    draw while heating or cooling is active, and reaches the meter as site
    load.
    """

    managed: bool = False
    heat_setpoint_c: float = DEFAULT_HEAT_SETPOINT_C
    cool_setpoint_c: float = DEFAULT_COOL_SETPOINT_C
    aux_power_kw: float = 0.0

    # None means "the pack's own thermal time constant", C_th / G, which is
    # 2500 s with the auto-scaled defaults whatever the pack size.
    time_constant_s: float | None = None

    derate: TemperatureDerate = field(default_factory=TemperatureDerate)

    def __post_init__(self):
        if self.heat_setpoint_c > self.cool_setpoint_c:
            raise ValueError(
                f"thermal.heat_setpoint_c ({self.heat_setpoint_c}) must not "
                f"exceed thermal.cool_setpoint_c ({self.cool_setpoint_c})"
            )
        if self.cool_setpoint_c > MAX_CELL_TEMP_C:
            raise ValueError(
                f"thermal.cool_setpoint_c ({self.cool_setpoint_c}) must not "
                f"exceed the {MAX_CELL_TEMP_C} °C cell limit"
            )
        if self.aux_power_kw < 0.0:
            raise ValueError(
                f"thermal.aux_power_kw must be non-negative, got {self.aux_power_kw}"
            )
        if self.time_constant_s is not None and self.time_constant_s <= 0.0:
            raise ValueError(
                "thermal.time_constant_s must be positive, got "
                f"{self.time_constant_s}"
            )

    @classmethod
    def from_config(cls, config: dict | None) -> "BatteryThermalConfig":
        """Build from the `thermal` block of a BESS asset config."""

        cfg = dict(config or {})

        return cls(
            managed=bool(cfg.get("managed", False)),
            heat_setpoint_c=float(
                cfg.get("heat_setpoint_c", DEFAULT_HEAT_SETPOINT_C)
            ),
            cool_setpoint_c=float(
                cfg.get("cool_setpoint_c", DEFAULT_COOL_SETPOINT_C)
            ),
            aux_power_kw=float(cfg.get("aux_power_kw", 0.0)),
            time_constant_s=(
                float(cfg["time_constant_s"])
                if cfg.get("time_constant_s") is not None
                else None
            ),
            derate=TemperatureDerate.from_config(cfg.get("derate")),
        )


@dataclass
class BatteryLimits:
    """
    SOC limit thresholds (percent).

    Under limit_mode="taper" (legacy) all four define derating zones:

        lower_limit_2 → hard discharge cutoff
        lower_limit_1 → full discharge capability restored
        upper_limit_1 → charge derating starts
        upper_limit_2 → hard charge cutoff

    Under limit_mode="hard" (default) only lower_limit_2 and upper_limit_2
    matter: they are the hard floor and ceiling whenever soc_min_percent /
    soc_max_percent are not set explicitly. The _1 thresholds are accepted and
    ignored so existing configs and EMS write sequences keep working.
    """
    soc_lower_limit_1: float = 25.0
    soc_lower_limit_2: float = 20.0
    soc_upper_limit_1: float = 85.0
    soc_upper_limit_2: float = 90.0


class BatteryModel:
    """
    Deterministic energy-based battery model.

    Responsibilities:
    - Energy integration
    - SOC tracking
    - Capability limits (SOC + temperature)
    - Thermal dynamics
    - Cycle tracking

    Does NOT enforce inverter ramp limits (handled by inverter model).

    SOC limits
    ----------
    limit_mode="hard" (default): no tapering anywhere. Power is clamped so SOC
    lands exactly on the limit in the step that would otherwise cross it, and
    is exactly 0 from then on while the setpoint keeps pushing that way. The
    clamped value is what step() returns, so reported active power, energy
    counters, the meter and the site balance all follow actual power.

    limit_mode="taper": the pre-0.2 behaviour, a linear charge/discharge
    derating ramp between the _1 and _2 thresholds.

    Efficiency
    ----------
    Charging stores less than arrives at AC, discharging drains more than
    reaches AC, so a configured round_trip_eff is actually realised:

        charge:    dE = +P_ac · eta_c · dt
        discharge: dE = -P_ac / eta_d · dt

    Energy counters are battery-side (what moved in and out of the pack), which
    is what the cycle counter and SOH are defined against.

    Thermal
    -------
    One lumped node: Joule heat in, exchange with ambient out, over C_th, with
    time constant tau = C_th / G. `thermal.derate` turns the resulting cell
    temperature into a capability factor on both directions.

    With `thermal.managed` the pack sits in a container that holds the cell
    inside [heat_setpoint_c, cool_setpoint_c], so a cold site keeps full
    capability, and `thermal.aux_power_kw` is published as `hvac_power_kw` for
    the site balance to pick up as load. Unmanaged — the default — the cell
    sits at ambient whenever it is idle, exactly as before.
    """

    # Auto-scaling reference: thermal mass and cooling scale linearly with
    # capacity_kwh against a 100 kWh "1×" battery; internal resistance scales
    # inversely (bigger packs have more parallel strings → lower pack-level R).
    # Calibrated so steady-state ΔT at 1C is ~0.5 K regardless of pack size.
    _AUTO_SCALE_REF_KWH = 100.0
    _AUTO_SCALE_R_OHM = 0.005
    _AUTO_SCALE_C_TH_J_PER_K = 500_000.0
    _AUTO_SCALE_G_TH_W_PER_K = 200.0

    # Energy tolerance, relative to pack size, used to decide "already at the
    # limit" and to snap away float dust after an integration step.
    _EPS_REL = 1e-9

    def __init__(
        self,
        capacity_kwh: float,
        initial_soc: float = 50.0,
        round_trip_eff: float = 0.92,
        internal_resistance: float | None = None,
        ambient_temp_c: float = 20.0,
        limits: BatteryLimits | None = None,
        max_charge_kw: float | None = None,
        max_discharge_kw: float | None = None,
        thermal_capacity_j_per_k: float | None = None,
        thermal_conductance_w_per_k: float | None = None,
        limit_mode: str = LIMIT_MODE_HARD,
        soc_min_percent: float | None = None,
        soc_max_percent: float | None = None,
        charge_efficiency: float | None = None,
        discharge_efficiency: float | None = None,
        self_discharge_percent_per_day: float = 0.0,
        thermal: BatteryThermalConfig | None = None,
    ):
        self.capacity_kwh = capacity_kwh
        self.energy_kwh = capacity_kwh * initial_soc / 100.0

        if limit_mode not in VALID_LIMIT_MODES:
            raise ValueError(
                f"Invalid limit_mode '{limit_mode}'. "
                f"Must be one of {VALID_LIMIT_MODES}."
            )
        self.limit_mode = limit_mode

        # Efficiency split. Explicit per-direction values win over the
        # round-trip shorthand.
        self.round_trip_eff = round_trip_eff
        self.charge_eff = (
            charge_efficiency
            if charge_efficiency is not None
            else math.sqrt(round_trip_eff)
        )
        self.discharge_eff = (
            discharge_efficiency
            if discharge_efficiency is not None
            else math.sqrt(round_trip_eff)
        )

        for name, eff in (
            ("charge_efficiency", self.charge_eff),
            ("discharge_efficiency", self.discharge_eff),
        ):
            if not 0.0 < eff <= 1.0:
                raise ValueError(f"{name} must be in (0, 1], got {eff}")

        if self_discharge_percent_per_day < 0.0:
            raise ValueError("self_discharge_percent_per_day must be non-negative")
        self.self_discharge_percent_per_day = self_discharge_percent_per_day

        # Limits
        self.limits = limits or BatteryLimits()

        # Hard SOC window. None means "follow limits.soc_*_limit_2", so an EMS
        # write to those registers moves the hard limit live.
        if (
            soc_min_percent is not None
            and soc_max_percent is not None
            and soc_min_percent >= soc_max_percent
        ):
            raise ValueError(
                f"soc_min_percent ({soc_min_percent}) must be below "
                f"soc_max_percent ({soc_max_percent})"
            )
        self._soc_min_percent = soc_min_percent
        self._soc_max_percent = soc_max_percent

        # Auto-scale electrical + thermal parameters with pack size when not
        # explicitly provided.
        scale = capacity_kwh / self._AUTO_SCALE_REF_KWH if capacity_kwh > 0 else 1.0

        # Electrical
        self.internal_resistance = (
            internal_resistance
            if internal_resistance is not None
            else self._AUTO_SCALE_R_OHM / scale
        )

        # Capability limits (absolute)
        self.max_charge_kw = max_charge_kw if max_charge_kw is not None else capacity_kwh
        self.max_discharge_kw = max_discharge_kw if max_discharge_kw is not None else capacity_kwh

        # Thermal
        self.ambient_temp_c = ambient_temp_c
        self.thermal_capacity_j_per_k = (
            thermal_capacity_j_per_k
            if thermal_capacity_j_per_k is not None
            else self._AUTO_SCALE_C_TH_J_PER_K * scale
        )
        self.thermal_conductance_w_per_k = (
            thermal_conductance_w_per_k
            if thermal_conductance_w_per_k is not None
            else self._AUTO_SCALE_G_TH_W_PER_K * scale
        )

        self.thermal = thermal or BatteryThermalConfig()

        # A managed pack boots inside its band: an unmanaged one has nowhere to
        # start but ambient, a managed one is already being held.
        self.temperature_c = self.hold_in_band(ambient_temp_c)

        # Container HVAC draw, in kW, for whatever the step just did. Zero
        # unless the container is managed and is actually heating or cooling.
        self.hvac_power_kw = 0.0

        # Lifetime tracking
        self.charge_energy_total_kwh = 0.0
        self.discharge_energy_total_kwh = 0.0
        self.cycles = 0.0
        self.soh = 100.0

    # -------------------------------------------------
    # Core properties
    # -------------------------------------------------

    @property
    def soc(self) -> float:
        return 100.0 * self.energy_kwh / self.capacity_kwh

    @property
    def soc_min_percent(self) -> float:
        """Hard discharge floor — explicit value, else limits.soc_lower_limit_2."""
        if self._soc_min_percent is not None:
            return self._soc_min_percent
        return self.limits.soc_lower_limit_2

    @soc_min_percent.setter
    def soc_min_percent(self, value: float) -> None:
        self._soc_min_percent = float(value)

    @property
    def soc_max_percent(self) -> float:
        """Hard charge ceiling — explicit value, else limits.soc_upper_limit_2."""
        if self._soc_max_percent is not None:
            return self._soc_max_percent
        return self.limits.soc_upper_limit_2

    @soc_max_percent.setter
    def soc_max_percent(self, value: float) -> None:
        self._soc_max_percent = float(value)

    def energy_for_soc(self, soc_percent: float) -> float:
        return self.capacity_kwh * soc_percent / 100.0

    def _eps(self) -> float:
        return self._EPS_REL * max(self.capacity_kwh, 1.0)

    # -------------------------------------------------
    # Capability limits
    # -------------------------------------------------

    def get_power_limits(self) -> tuple[float, float]:
        """
        Returns allowed DC power range (min_kw, max_kw)

        min_kw = max allowed charge (negative)
        max_kw = max allowed discharge (positive)

        Under limit_mode="hard" the SOC contribution is a binary gate: full
        rating while there is headroom, zero once the pack sits on the limit.
        Under limit_mode="taper" it is the legacy linear derating ramp.
        """

        if self.limit_mode == LIMIT_MODE_HARD:
            soc_scale_charge = self._soc_charge_gate()
            soc_scale_discharge = self._soc_discharge_gate()
        else:
            soc_scale_charge = self._soc_charge_scale()
            soc_scale_discharge = self._soc_discharge_scale()

        temp_scale = self._temperature_scale()

        discharge_scale = min(soc_scale_discharge, temp_scale)
        charge_scale = min(soc_scale_charge, temp_scale)

        max_discharge = self.max_discharge_kw * discharge_scale
        max_charge = self.max_charge_kw * charge_scale

        return -max_charge, max_discharge

    def apply_capability_limits(self, requested_kw: float) -> float:
        """
        Clamp requested power to physically allowed limits.
        """

        min_kw, max_kw = self.get_power_limits()

        return max(min_kw, min(max_kw, requested_kw))

    # -------------------------------------------------
    # Hard SOC window
    # -------------------------------------------------

    def charge_headroom_kwh(self) -> float:
        """Battery-side energy that still fits below the hard ceiling."""
        return max(0.0, self.energy_for_soc(self.soc_max_percent) - self.energy_kwh)

    def discharge_headroom_kwh(self) -> float:
        """Battery-side energy still available above the hard floor."""
        return max(0.0, self.energy_kwh - self.energy_for_soc(self.soc_min_percent))

    def _soc_charge_gate(self) -> float:
        return 0.0 if self.charge_headroom_kwh() <= self._eps() else 1.0

    def _soc_discharge_gate(self) -> float:
        return 0.0 if self.discharge_headroom_kwh() <= self._eps() else 1.0

    def headroom_limited_power(self, power_kw: float, dt: float) -> float:
        """
        Shrink power so SOC lands exactly on a hard limit, never past it.

        Returns 0.0 when the pack is already sitting on the limit in the
        requested direction — no taper, no trickle.
        """

        dt_h = dt / 3600.0

        if power_kw == 0.0 or dt_h <= 0.0:
            return 0.0

        if power_kw < 0:  # charge
            room = self.charge_headroom_kwh()
            if room <= self._eps():
                return 0.0
            return -min(-power_kw, room / (self.charge_eff * dt_h))

        # discharge
        room = self.discharge_headroom_kwh()
        if room <= self._eps():
            return 0.0
        return min(power_kw, room * self.discharge_eff / dt_h)

    # -------------------------------------------------
    # SOC scaling (legacy taper mode)
    # -------------------------------------------------

    def _soc_discharge_scale(self) -> float:
        soc = self.soc
        limits = self.limits

        if soc <= limits.soc_lower_limit_2:
            return 0.0

        if soc >= limits.soc_lower_limit_1:
            return 1.0

        return (
            (soc - limits.soc_lower_limit_2)
            / (limits.soc_lower_limit_1 - limits.soc_lower_limit_2)
        )

    def _soc_charge_scale(self) -> float:
        soc = self.soc
        limits = self.limits

        if soc >= limits.soc_upper_limit_2:
            return 0.0

        if soc <= limits.soc_upper_limit_1:
            return 1.0

        return (
            (limits.soc_upper_limit_2 - soc)
            / (limits.soc_upper_limit_2 - limits.soc_upper_limit_1)
        )

    # -------------------------------------------------
    # Temperature scaling
    # -------------------------------------------------

    def _temperature_scale(self) -> float:
        """
        Capability factor for the current cell temperature.

        The ladder lives in `thermal.derate`; its defaults are the curve this
        model has always had.
        """

        return self.thermal.derate.factor_at(self.temperature_c)

    # -------------------------------------------------
    # Energy Integration
    # -------------------------------------------------

    def step(self, requested_power_kw: float, dt: float) -> float:
        """
        Apply capability limits, integrate energy, update thermal state.

        Returns actual applied power.
        """

        # Inverter/pack rating and temperature capability.
        power_kw = self.apply_capability_limits(requested_power_kw)

        # Size the step so SOC lands exactly on the limit it is heading for.
        if self.limit_mode == LIMIT_MODE_HARD:
            power_kw = self.headroom_limited_power(power_kw, dt)

        dt_h = dt / 3600.0
        start_energy = self.energy_kwh

        if power_kw > 0:  # discharge — the pack gives up more than reaches AC
            delta_kwh = -(power_kw / self.discharge_eff * dt_h)
            self.discharge_energy_total_kwh += -delta_kwh
        elif power_kw < 0:  # charge — less is stored than arrives at AC
            delta_kwh = -(power_kw * self.charge_eff * dt_h)
            self.charge_energy_total_kwh += delta_kwh
        else:
            delta_kwh = 0.0

        self.energy_kwh = start_energy + delta_kwh
        self._snap_to_limit(start_energy, power_kw)
        self._apply_self_discharge(dt_h)

        self.energy_kwh = max(
            0.0,
            min(self.capacity_kwh, self.energy_kwh),
        )

        self.cycles = (
            self.charge_energy_total_kwh + self.discharge_energy_total_kwh
        ) / (2.0 * self.capacity_kwh)

        # SOH linear decay: 20% capacity loss over 4000 full cycles (0.005% per cycle)
        self.soh = max(0.0, 100.0 - self.cycles * 0.005)

        self.update_temperature(power_kw, dt)

        return power_kw

    def _snap_to_limit(self, start_energy: float, power_kw: float) -> None:
        """
        Remove float dust so SOC lands exactly on the limit it was aimed at.

        Only snaps when the step started on the allowed side of that limit, so
        a pack that boots outside its window is never yanked into it.
        """

        if self.limit_mode != LIMIT_MODE_HARD:
            return

        eps = self._eps()

        if power_kw < 0:
            e_max = self.energy_for_soc(self.soc_max_percent)
            if start_energy <= e_max + eps and self.energy_kwh > e_max - eps:
                self.energy_kwh = e_max
        elif power_kw > 0:
            e_min = self.energy_for_soc(self.soc_min_percent)
            if start_energy >= e_min - eps and self.energy_kwh < e_min + eps:
                self.energy_kwh = e_min

    def _apply_self_discharge(self, dt_h: float) -> None:
        """
        Passive loss, floored at the hard discharge limit — a BMS defends its
        reserve rather than letting idle losses walk through it.
        """

        if self.self_discharge_percent_per_day <= 0.0 or dt_h <= 0.0:
            return

        loss_kwh = (
            self.capacity_kwh
            * self.self_discharge_percent_per_day
            / 100.0
            * dt_h
            / 24.0
        )

        floor = 0.0
        if self.limit_mode == LIMIT_MODE_HARD:
            floor = self.energy_for_soc(self.soc_min_percent)

        # Never raise a pack that already sits below the floor.
        floor = min(floor, self.energy_kwh)

        self.energy_kwh = max(floor, self.energy_kwh - loss_kwh)

    # -------------------------------------------------
    # Thermal
    # -------------------------------------------------

    @property
    def hvac_time_constant_s(self) -> float:
        """
        Time constant of the managed cell's relaxation toward ambient.

        Explicit when configured, otherwise the pack's own thermal time
        constant tau = C_th / G — 2500 s with the auto-scaled defaults, for any
        pack size.
        """

        if self.thermal.time_constant_s is not None:
            return self.thermal.time_constant_s

        if self.thermal_conductance_w_per_k <= 0.0:
            return float("inf")

        return self.thermal_capacity_j_per_k / self.thermal_conductance_w_per_k

    def hold_in_band(self, temperature_c: float) -> float:
        """Clamp into the setpoint band. Identity on an unmanaged pack."""

        if not self.thermal.managed:
            return temperature_c

        return min(
            self.thermal.cool_setpoint_c,
            max(self.thermal.heat_setpoint_c, temperature_c),
        )

    def update_temperature(self, power_kw: float, dt: float):
        voc = self.open_circuit_voltage()
        current = (power_kw * 1000.0 / voc) if voc else 0.0
        joule = current * current * self.internal_resistance * dt

        if self.thermal.managed:
            self._update_temperature_managed(joule, dt)
            return

        Tdiff = max(0.0, self.temperature_c - self.ambient_temp_c)
        cooling = self.thermal_conductance_w_per_k * Tdiff * dt

        delta_T = (joule - cooling) / self.thermal_capacity_j_per_k
        self.temperature_c += delta_T
        self.temperature_c = max(
            self.ambient_temp_c, min(MAX_CELL_TEMP_C, self.temperature_c)
        )
        self.hvac_power_kw = 0.0

    def _update_temperature_managed(self, joule_j: float, dt: float) -> None:
        """
        Cell temperature inside a managed container.

        Two differences from the unmanaged pack, both of them the point of the
        feature:

        - the exchange with ambient is signed, so the cell relaxes toward
          ambient in both directions rather than only cooling toward it. An
          unmanaged cell is floored at ambient and so can never be below it;
          a heated one can, and must be pulled back up when ambient rises.
        - the result cannot leave [heat_setpoint_c, cool_setpoint_c]. The HVAC
          is ideal within that band: it supplies or removes exactly the heat
          needed to hold the nearest edge, and `aux_power_kw` is drawn for as
          long as it is doing so.

        Relaxation uses `hvac_time_constant_s`: the conductance is backed out
        of it as G = C_th / tau, so configuring the time constant configures
        the dynamics directly.
        """

        tau_s = self.hvac_time_constant_s
        conductance = (
            self.thermal_capacity_j_per_k / tau_s if tau_s != float("inf") else 0.0
        )

        exchange = conductance * (self.temperature_c - self.ambient_temp_c) * dt
        free_temp_c = (
            self.temperature_c
            + (joule_j - exchange) / self.thermal_capacity_j_per_k
        )

        held_temp_c = min(
            self.thermal.cool_setpoint_c,
            max(self.thermal.heat_setpoint_c, free_temp_c),
        )

        # The HVAC is active exactly when the band is what stopped the cell
        # from going where its own dynamics were taking it.
        active = abs(held_temp_c - free_temp_c) > 1e-12
        self.hvac_power_kw = self.thermal.aux_power_kw if active else 0.0

        self.temperature_c = min(MAX_CELL_TEMP_C, held_temp_c)

    def open_circuit_voltage(self) -> float:
        base_voltage = 700.0
        soc_factor = 1.0 + 0.1 * math.sin((self.soc / 100.0) * math.pi)
        return base_voltage * soc_factor

    def set_ambient_temperature(self, temp_c: float):
        self.ambient_temp_c = float(temp_c)

    # -------------------------------------------------
    # Persistable state
    # -------------------------------------------------

    def state_snapshot(self) -> dict:
        """Durable fields only — config is not echoed back into the state file."""
        return {
            "capacity_kwh": self.capacity_kwh,
            "energy_kwh": self.energy_kwh,
            "soc_percent": self.soc,
            "charge_energy_total_kwh": self.charge_energy_total_kwh,
            "discharge_energy_total_kwh": self.discharge_energy_total_kwh,
            "cycles": self.cycles,
            "soh": self.soh,
            "temperature_c": self.temperature_c,
        }

    def restore_state(self, state: dict) -> None:
        """
        Apply a previously saved snapshot.

        A capacity change rescales SOC rather than energy, so a repack keeps
        the same fill level instead of the same kWh.
        """

        saved_capacity = float(state.get("capacity_kwh", self.capacity_kwh))

        if saved_capacity > 0 and abs(saved_capacity - self.capacity_kwh) > 1e-9:
            soc = 100.0 * float(state["energy_kwh"]) / saved_capacity
            self.energy_kwh = self.energy_for_soc(soc)
            logger.warning(
                "Battery capacity changed %.3f → %.3f kWh since state was saved; "
                "SOC %.2f%% preserved, energy rescaled to %.3f kWh",
                saved_capacity, self.capacity_kwh, soc, self.energy_kwh,
            )
        else:
            self.energy_kwh = float(state["energy_kwh"])

        self.charge_energy_total_kwh = float(state.get("charge_energy_total_kwh", 0.0))
        self.discharge_energy_total_kwh = float(
            state.get("discharge_energy_total_kwh", 0.0)
        )
        self.cycles = float(state.get("cycles", 0.0))
        self.soh = float(state.get("soh", 100.0))
        # A managed pack is inside its band even on the first step after a
        # restore, so capability telemetry never reports a cold derate the
        # container would not actually allow.
        self.temperature_c = self.hold_in_band(
            float(state.get("temperature_c", self.ambient_temp_c))
        )
