import math
import random
from typing import Dict


class EnergyMeterModel:
    """
    Deterministic PCC energy meter measurement model.

    This model is a pure observer. It NEVER updates physics.

    Observes:
        - SitePowerModel (grid power, energy counters)
        - GridFrequencyModel (frequency)

    Applies:
        - power factor drift
        - reactive power calculation
        - optional deterministic noise
    """

    def __init__(self, seed: int | None = None):
        self._rng = random.Random(seed)
        self._pf = 0.99

    # --------------------------------------------------
    # Measurement Snapshot
    # --------------------------------------------------
    def measure(
        self,
        grid_power_kw: float,
        import_energy_kwh: float,
        export_energy_kwh: float,
        grid_frequency: float,
        voltage_ll: float,
    ) -> Dict[str, float]:

        # deterministic PF drift
        pf_target = self._rng.uniform(0.95, 1.0)
        self._pf += (pf_target - self._pf) * 0.05

        # reactive power from PF
        reactive_power = grid_power_kw * math.tan(math.acos(self._pf))

        # balanced 3-phase assumption
        phase_power = grid_power_kw / 3.0

        voltage_ln = voltage_ll / math.sqrt(3.0)

        return {
            "total_active_power": grid_power_kw,
            "total_reactive_power": reactive_power,
            "total_power_factor": self._pf,
            "grid_frequency": grid_frequency,

            "phase_voltage_a": voltage_ln,
            "phase_voltage_b": voltage_ln,
            "phase_voltage_c": voltage_ln,

            "phase_active_power_a": phase_power,
            "phase_active_power_b": phase_power,
            "phase_active_power_c": phase_power,

            "total_import_energy": import_energy_kwh,
            "total_export_energy": export_energy_kwh,
        }