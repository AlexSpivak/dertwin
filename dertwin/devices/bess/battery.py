from dataclasses import dataclass


@dataclass
class BatteryLimits:
    soc_min: float = 5.0
    soc_max: float = 95.0
    soc_recovery_min: float = 7.0
    soc_recovery_max: float = 93.0


class BatteryModel:
    """
    Deterministic energy-based battery model.
    No vendor logic.
    No protocol logic.
    Pure physics + limits.
    """

    def __init__(
        self,
        capacity_kwh: float,
        initial_soc: float = 50.0,
        charge_efficiency: float = 0.98,
        discharge_efficiency: float = 0.98,
        limits: BatteryLimits | None = None,
    ):
        self.capacity_kwh = capacity_kwh
        self.energy_kwh = capacity_kwh * (initial_soc / 100.0)

        self.charge_efficiency = charge_efficiency
        self.discharge_efficiency = discharge_efficiency

        self.limits = limits or BatteryLimits()

        self._charge_blocked = False
        self._discharge_blocked = False

    # ---------------------------------------------------------
    # Public properties
    # ---------------------------------------------------------

    @property
    def soc(self) -> float:
        return 100.0 * self.energy_kwh / self.capacity_kwh

    @property
    def is_charge_allowed(self) -> bool:
        return not self._charge_blocked

    @property
    def is_discharge_allowed(self) -> bool:
        return not self._discharge_blocked

    # ---------------------------------------------------------
    # Core physics step
    # ---------------------------------------------------------

    def step(self, power_kw: float, dt_seconds: float) -> float:
        """
        power_kw:
            + → charging
            - → discharging

        Returns actual applied power (after limits).
        """

        dt_hours = dt_seconds / 3600.0
        applied_power = power_kw

        # -------------------------------------------------
        # CHARGING
        # -------------------------------------------------
        if power_kw > 0 and not self._charge_blocked:

            # Max energy allowed until upper limit
            max_energy = (
                    self.limits.soc_max / 100.0 * self.capacity_kwh
                    - self.energy_kwh
            )

            requested_energy = power_kw * dt_hours * self.charge_efficiency

            if requested_energy > max_energy:
                requested_energy = max_energy

            self.energy_kwh += requested_energy

            if dt_hours > 0:
                applied_power = requested_energy / (
                        dt_hours * self.charge_efficiency
                )

        # -------------------------------------------------
        # DISCHARGING
        # -------------------------------------------------
        elif power_kw < 0 and not self._discharge_blocked:

            min_energy = (
                    self.limits.soc_min / 100.0 * self.capacity_kwh
            )

            max_discharge = self.energy_kwh - min_energy

            requested_energy = power_kw * dt_hours / self.discharge_efficiency

            if abs(requested_energy) > max_discharge:
                requested_energy = -max_discharge

            self.energy_kwh += requested_energy

            if dt_hours > 0:
                applied_power = requested_energy * self.discharge_efficiency / dt_hours

        else:
            applied_power = 0.0

        # Physical clamp
        self.energy_kwh = max(0.0, min(self.capacity_kwh, self.energy_kwh))

        # Update hysteresis after energy change
        self._update_limits()

        return applied_power

    # ---------------------------------------------------------
    # Limit logic with hysteresis
    # ---------------------------------------------------------

    def _update_limits(self):

        soc = self.soc

        # Upper limit
        if soc >= self.limits.soc_max:
            self._charge_blocked = True
        elif soc <= self.limits.soc_recovery_max:
            self._charge_blocked = False

        # Lower limit
        if soc <= self.limits.soc_min:
            self._discharge_blocked = True
        elif soc >= self.limits.soc_recovery_min:
            self._discharge_blocked = False