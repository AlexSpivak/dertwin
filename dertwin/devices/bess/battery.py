import math
from dataclasses import dataclass


@dataclass
class BatteryLimits:
    soc_lower_limit_1: float = 25.0
    soc_lower_limit_2: float = 20.0
    soc_upper_limit_1: float = 85.0
    soc_upper_limit_2: float = 90.0


class BatteryModel:
    """
    Deterministic energy-based battery model.
    Pure physics + limits.
    """

    def __init__(
        self,
        capacity_kwh: float,
        initial_soc: float = 50.0,
        round_trip_eff: float = 0.92,
        internal_resistance: float = 0.05,
        ambient_temp_c: float = 20.0,
        limits: BatteryLimits | None = None,
    ):
        self.capacity_kwh = capacity_kwh
        self.energy_kwh = capacity_kwh * initial_soc / 100.0

        self.round_trip_eff = round_trip_eff
        self.charge_eff = math.sqrt(round_trip_eff)
        self.discharge_eff = math.sqrt(round_trip_eff)

        self.internal_resistance = internal_resistance
        self.ambient_temp_c = ambient_temp_c

        self.temperature_c = 25.0
        self.thermal_capacity_j_per_k = 5000.0
        self.thermal_conductance_w_per_k = 0.5

        self.charge_energy_total_kwh = 0.0
        self.discharge_energy_total_kwh = 0.0

        self.soh = 100.0
        self.cycles = 0.0

        self.limits = limits or BatteryLimits()

    # -------------------------------------------------
    # Core properties
    # -------------------------------------------------

    @property
    def soc(self) -> float:
        return 100.0 * self.energy_kwh / self.capacity_kwh

    # -------------------------------------------------
    # SOC LIMIT LOGIC (NEW)
    # -------------------------------------------------

    def limit_power(self, power_kw: float) -> float:
        soc = self.soc
        limits = self.limits

        # ---- HARD CUTS ----
        if power_kw > 0 and soc <= limits.soc_lower_limit_2:
            return 0.0
        if power_kw < 0 and soc >= limits.soc_upper_limit_2:
            return 0.0

        # ---- SOFT DERATING ----
        if power_kw > 0 and limits.soc_lower_limit_2 < soc < limits.soc_lower_limit_1:
            factor = (soc - limits.soc_lower_limit_2) / (
                limits.soc_lower_limit_1 - limits.soc_lower_limit_2
            )
            return power_kw * max(0.0, min(1.0, factor))

        if power_kw < 0 and limits.soc_upper_limit_1 < soc < limits.soc_upper_limit_2:
            factor = (limits.soc_upper_limit_2 - soc) / (
                limits.soc_upper_limit_2 - limits.soc_upper_limit_1
            )
            return power_kw * max(0.0, min(1.0, factor))

        return power_kw

    # -------------------------------------------------
    # Energy Integration
    # -------------------------------------------------

    def step(self, power_kw: float, dt: float) -> float:

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
        ) / self.capacity_kwh

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