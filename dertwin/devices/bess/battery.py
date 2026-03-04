import math
from dataclasses import dataclass


@dataclass
class BatteryLimits:
    """
    SOC derating zones (percent)

    lower_limit_2 → hard discharge cutoff
    lower_limit_1 → full discharge capability restored

    upper_limit_1 → charge derating starts
    upper_limit_2 → hard charge cutoff
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
    """

    def __init__(
        self,
        capacity_kwh: float,
        initial_soc: float = 50.0,
        round_trip_eff: float = 0.92,
        internal_resistance: float = 0.05,
        ambient_temp_c: float = 20.0,
        limits: BatteryLimits | None = None,
        max_charge_kw: float | None = None,
        max_discharge_kw: float | None = None,
    ):
        self.capacity_kwh = capacity_kwh
        self.energy_kwh = capacity_kwh * initial_soc / 100.0

        # Efficiency split
        self.round_trip_eff = round_trip_eff
        self.charge_eff = math.sqrt(round_trip_eff)
        self.discharge_eff = math.sqrt(round_trip_eff)

        # Electrical
        self.internal_resistance = internal_resistance

        # Capability limits (absolute)
        self.max_charge_kw = max_charge_kw if max_charge_kw is not None else capacity_kwh
        self.max_discharge_kw = max_discharge_kw if max_discharge_kw is not None else capacity_kwh

        # Thermal
        self.ambient_temp_c = ambient_temp_c
        self.temperature_c = ambient_temp_c
        self.thermal_capacity_j_per_k = 5000.0
        self.thermal_conductance_w_per_k = 0.5

        # Lifetime tracking
        self.charge_energy_total_kwh = 0.0
        self.discharge_energy_total_kwh = 0.0
        self.cycles = 0.0
        self.soh = 100.0

        # Limits
        self.limits = limits or BatteryLimits()

    # -------------------------------------------------
    # Core properties
    # -------------------------------------------------

    @property
    def soc(self) -> float:
        return 100.0 * self.energy_kwh / self.capacity_kwh

    # -------------------------------------------------
    # Capability limits
    # -------------------------------------------------

    def get_power_limits(self) -> tuple[float, float]:
        """
        Returns allowed DC power range (min_kw, max_kw)

        min_kw = max allowed charge (negative)
        max_kw = max allowed discharge (positive)
        """

        soc_scale_discharge = self._soc_discharge_scale()
        soc_scale_charge = self._soc_charge_scale()

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
    # SOC scaling
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

        power_kw = self.apply_capability_limits(requested_power_kw)

        dt_h = dt / 3600.0

        if power_kw > 0:  # discharge
            delta_kwh = -(power_kw * self.discharge_eff * dt_h)
            self.discharge_energy_total_kwh += -delta_kwh
        elif power_kw < 0:  # charge
            delta_kwh = -(power_kw * self.charge_eff * dt_h)
            self.charge_energy_total_kwh += delta_kwh
        else:
            delta_kwh = 0.0

        self.energy_kwh = max(
            0.0,
            min(self.capacity_kwh, self.energy_kwh + delta_kwh),
        )

        self.cycles = (
            self.charge_energy_total_kwh + self.discharge_energy_total_kwh
        ) / (2.0 * self.capacity_kwh)

        # SOH linear decay: 20% capacity loss over 4000 full cycles (0.005% per cycle)
        self.soh = max(0.0, 100.0 - self.cycles * 0.005)

        self.update_temperature(power_kw, dt)

        return power_kw

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