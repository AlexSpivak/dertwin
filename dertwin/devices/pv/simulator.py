from typing import Dict

from dertwin.core.device import SimulatedDevice
from dertwin.devices.pv.panel import PVArrayModel
from dertwin.devices.pv.inverter import PVInverterModel
from dertwin.devices.pv.pv import PVModel
from dertwin.devices.pv.controller import PVController


class PVSimulator(SimulatedDevice):
    """
    Compatibility wrapper around new PV architecture.

    Exposes similar public API as old InverterSimulator
    while internally using:
        Panel → Inverter → PVModel → Controller
    """

    def __init__(
        self,
        rated_kw: float = 10.0,
        ambient_temp_c: float = 20.0,
        module_efficiency: float = 0.20,
        area_m2: float | None = None,
    ):
        super().__init__()

        rated_power_w = rated_kw * 1000.0

        # If area not provided, size array to roughly match rated power at 1000 W/m²
        if area_m2 is None:
            area_m2 = rated_power_w / (1000.0 * module_efficiency)

        # --- Core models ---
        self.panel = PVArrayModel(
            area_m2=area_m2,
            module_efficiency=module_efficiency,
            ambient_temp_c=ambient_temp_c,
        )

        self.inverter = PVInverterModel(
            rated_ac_power_w=rated_power_w,
            ambient_temp_c=ambient_temp_c,
        )

        self.pv = PVModel(self.panel, self.inverter)
        self.controller = PVController(self.pv)

        self._last_telemetry: Dict[str, float] = {}

    # =========================================================
    # ---- Compatibility Properties (OLD STYLE API)
    # =========================================================

    @property
    def rated_power_w(self):
        return self.inverter.rated_ac_power_w

    @property
    def active_power_w(self):
        return self.inverter.active_power_w

    @property
    def temperature_c(self):
        return self.inverter.temperature_c

    @property
    def ambient_temp_c(self):
        return self.inverter.ambient_temp_c

    @property
    def today_energy_kwh(self):
        return self.pv.today_energy_kwh

    @property
    def lifetime_energy_kwh(self):
        return self.pv.lifetime_energy_kwh

    @property
    def fault_code(self):
        return self.inverter.fault_code

    @property
    def efficiency(self):
        return self.inverter.efficiency

    # =========================================================
    # ---- External Control Inputs
    # =========================================================

    def set_irradiance(self, irradiance_w_per_m2: float):
        self.panel.set_irradiance(irradiance_w_per_m2)

    def set_grid_conditions(self, voltage: float, frequency: float):
        self.inverter.grid_voltage = float(voltage)
        self.inverter.grid_frequency = float(frequency)

    # =========================================================
    # ---- SimulatedDevice Interface
    # =========================================================

    def update(self, dt: float) -> None:
        self._last_telemetry = self.controller.step(dt)

    def get_telemetry(self) -> Dict[str, float]:
        return self._last_telemetry

    def apply_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        return self.controller.apply_commands(commands)

    def init_applied_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        return self.controller.init_applied_commands(commands)