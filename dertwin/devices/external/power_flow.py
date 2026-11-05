from typing import Callable, Optional


class SitePowerModel:
    """
    Pure deterministic site-level power balance model.

    Responsibilities:
    - Aggregate load, auxiliary load, PV, BESS, and CHP
    - Compute net grid power
    - Integrate import/export energy

    Auxiliary load is device-owned consumption that is not the site load and
    not inverter power either — a managed BESS container's HVAC is the first
    of it. It enters the balance on the load side, exactly as the site load
    does, so it shows up in grid power, the import counter and the meter
    without being netted off the device that drew it.
    """

    def __init__(
        self,
        base_load_supplier: Callable[[float], float],
        pv_supplier: Optional[Callable[[], float]] = None,
        bess_supplier: Optional[Callable[[], float]] = None,
        chp_supplier: Optional[Callable[[], float]] = None,
        aux_load_supplier: Optional[Callable[[], float]] = None,
    ):
        self._sim_time = 0.0

        self.base_load_supplier = base_load_supplier
        self.pv_supplier = pv_supplier
        self.bess_supplier = bess_supplier
        self.chp_supplier = chp_supplier
        self.aux_load_supplier = aux_load_supplier

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
        chp_kw = self.chp_supplier() if self.chp_supplier else 0.0
        aux_load_kw = self.aux_load_supplier() if self.aux_load_supplier else 0.0

        # Positive = import, Negative = export
        # All generators (PV, BESS discharge, CHP) reduce grid import
        self.grid_power_kw = base_load_kw + aux_load_kw - pv_kw - bess_kw - chp_kw

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