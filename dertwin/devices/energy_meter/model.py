import math
import random
from typing import Dict


class EnergyMeterModel:
    """
    Measurement model of a PCC energy meter.

    Observes:
        - SitePowerModel
        - GridModel (optional in future)

    Applies:
        - PF drift
        - Reactive calculation
        - Measurement noise (optional)
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
        grid_frequency_hz: float,
    ) -> Dict[str, float]:

        # Smooth PF drift
        pf_target = self._rng.uniform(0.95, 1.0)
        self._pf += (pf_target - self._pf) * 0.05

        reactive_power = grid_power_kw * math.tan(math.acos(self._pf))
        phase_power = grid_power_kw / 3.0

        return {
            "total_active_power": grid_power_kw,
            "total_reactive_power": reactive_power,
            "total_power_factor": self._pf,
            "grid_frequency": grid_frequency_hz,

            "phase_active_power_a": phase_power,
            "phase_active_power_b": phase_power,
            "phase_active_power_c": phase_power,

            "total_import_energy": import_energy_kwh,
            "total_export_energy": export_energy_kwh,
        }