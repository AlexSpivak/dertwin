import math
import random
from typing import Callable, Dict, Optional

from dertwin.core.device import SimulatedDevice
from dertwin.devices.grid_frequency import GridFrequencyModel


class EnergyMeterSimulator(SimulatedDevice):
    """
    PCC energy meter simulator.

    - Purely observational
    - No control logic
    - Aggregates load, PV and BESS
    """

    def __init__(
        self,
        base_load_supplier: Callable[[float], float],
        pv_supplier: Optional[Callable[[], float]] = None,
        bess_supplier: Optional[Callable[[], float]] = None,
        grid_frequency_model: Optional[GridFrequencyModel] = None,
        seed: Optional[int] = None,
    ):
        self._sim_time = 0.0

        self.import_energy_total = 0.0
        self.export_energy_total = 0.0

        self.base_load_supplier = base_load_supplier
        self.pv_supplier = pv_supplier
        self.bess_supplier = bess_supplier

        self.grid_frequency_model = grid_frequency_model or GridFrequencyModel()

        self._rng = random.Random(seed)

        # Internal state cache
        self._grid_kw = 0.0
        self._pf = 0.99
        self._last_applied_commands = {}

    # --------------------------------------------------
    # Simulation step
    # --------------------------------------------------
    def update(self, dt: float) -> None:
        self._sim_time += dt
        dt_h = dt / 3600.0

        # --- Aggregate power
        base_load = self.base_load_supplier(self._sim_time)

        pv_kw = (self.pv_supplier() / 1000.0) if self.pv_supplier else 0.0
        bess_kw = (self.bess_supplier() / 1000.0) if self.bess_supplier else 0.0

        # Positive = import, Negative = export
        self._grid_kw = base_load - pv_kw - bess_kw

        # --- Energy accumulation
        energy_delta = self._grid_kw * dt_h
        if self._grid_kw > 0:
            self.import_energy_total += energy_delta
        else:
            self.export_energy_total += -energy_delta

        # --- Smooth PF drift
        pf_target = self._rng.uniform(0.95, 1.0)
        self._pf += (pf_target - self._pf) * 0.05

    # --------------------------------------------------
    # Telemetry snapshot
    # --------------------------------------------------
    def get_telemetry(self) -> Dict[str, float]:

        reactive_power = self._grid_kw * math.tan(math.acos(self._pf))
        phase_power = self._grid_kw / 3.0

        return {
            "total_active_power": self._grid_kw,
            "total_reactive_power": reactive_power,
            "total_power_factor": self._pf,
            "grid_frequency": self.grid_frequency_model.get_frequency(self._sim_time),

            "phase_active_power_a": phase_power,
            "phase_active_power_b": phase_power,
            "phase_active_power_c": phase_power,

            "total_import_energy": self.import_energy_total,
            "total_export_energy": self.export_energy_total,
        }

    # --------------------------------------------------
    # Energy meter does not accept commands
    # --------------------------------------------------
    def apply_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        # Meter is passive — no control effect
        return {}

    def init_applied_commands(self, commands: Dict[str, float]):
        self._last_applied_commands = dict(commands or {})
