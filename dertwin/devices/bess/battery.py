import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)


LIMIT_MODE_HARD = "hard"
LIMIT_MODE_TAPER = "taper"
VALID_LIMIT_MODES = (LIMIT_MODE_HARD, LIMIT_MODE_TAPER)


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
        self.temperature_c = ambient_temp_c
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
        Simple realistic temperature derating curve.
        """

        t = self.temperature_c

        if t <= 0:
            return 0.5
        elif t <= 10:
            return 0.8
        elif t <= 40:
            return 1.0
        elif t <= 50:
            return 0.8
        elif t <= 60:
            return 0.5
        else:
            return 0.0

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

    def update_temperature(self, power_kw: float, dt: float):
        voc = self.open_circuit_voltage()
        current = (power_kw * 1000.0 / voc) if voc else 0.0
        joule = current * current * self.internal_resistance * dt
        Tdiff = max(0.0, self.temperature_c - self.ambient_temp_c)
        cooling = self.thermal_conductance_w_per_k * Tdiff * dt

        delta_T = (joule - cooling) / self.thermal_capacity_j_per_k
        self.temperature_c += delta_T
        self.temperature_c = max(self.ambient_temp_c, min(80.0, self.temperature_c))

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
        self.temperature_c = float(state.get("temperature_c", self.ambient_temp_c))
