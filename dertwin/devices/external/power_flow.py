from typing import Callable, Optional


class SitePowerModel:
    """
    Pure deterministic site-level power balance model.

    Responsibilities:
    - Aggregate load, PV and BESS
    - Compute net grid power
    - Integrate import/export energy

    All supplier callables must return values in kW:
        - base_load_supplier(sim_time) -> kW
        - pv_supplier()               -> kW
        - bess_supplier()             -> kW  (positive = discharge)
    """

    def __init__(
        self,
        base_load_supplier: Callable[[float], float],
        pv_supplier: Optional[Callable[[], float]] = None,
        bess_supplier: Optional[Callable[[], float]] = None,
    ):
        self._sim_time = 0.0

        self.base_load_supplier = base_load_supplier
        self.pv_supplier = pv_supplier
        self.bess_supplier = bess_supplier

        self.grid_power_kw = 0.0

        self.import_energy_kwh = 0.0
        self.export_energy_kwh = 0.0

    # --------------------------------------------------
    # Simulation Step
    # --------------------------------------------------
    def update(self, dt: float) -> None:
        self._sim_time += dt
        dt_h = dt / 3600.0

        base_load_kw = self.base_load_supplier(self._sim_time)
        pv_kw = self.pv_supplier() if self.pv_supplier else 0.0
        bess_kw = self.bess_supplier() if self.bess_supplier else 0.0

        # Positive = import, Negative = export
        self.grid_power_kw = base_load_kw - pv_kw - bess_kw

        if abs(self.grid_power_kw) < 1e-9:
            self.grid_power_kw = 0.0

        energy_delta = self.grid_power_kw * dt_h

        if self.grid_power_kw > 0:
            self.import_energy_kwh += energy_delta
        elif self.grid_power_kw < 0:
            self.export_energy_kwh += -energy_delta

    # --------------------------------------------------

    def get_sim_time(self) -> float:
        return self._sim_time