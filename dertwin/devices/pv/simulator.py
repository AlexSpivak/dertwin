from typing import Dict, Optional

from dertwin.core.device import SimulatedDevice
from dertwin.devices.external.ambient_temperature import AmbientTemperatureModel
from dertwin.devices.external.grid_frequency import GridFrequencyModel
from dertwin.devices.external.grid_voltage import GridVoltageModel
from dertwin.devices.external.irradiance import IrradianceModel
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
        ambient_temp_model: Optional[AmbientTemperatureModel] = None,
        grid_frequency_model: Optional[GridFrequencyModel] = None,
        grid_voltage_model: Optional[GridVoltageModel] = None,
        irradiance_model: Optional[IrradianceModel] = None,
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


        self.ambient_temp_model = ambient_temp_model
        self.grid_frequency_model = grid_frequency_model
        self.grid_voltage_model = grid_voltage_model
        self.irradiance_model = irradiance_model

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

    # =========================================================
    # ---- SimulatedDevice Interface
    # =========================================================

    def update(self, dt: float) -> None:
        # Ambient temperature
        if self.ambient_temp_model:
            ambient = self.ambient_temp_model.get_temperature()
            self.panel.set_ambient_temperature(ambient)

        # Grid frequency
        if self.grid_frequency_model:
            freq = self.grid_frequency_model.get_frequency()
            self.inverter.grid_frequency = float(freq)

        # Grid voltage
        if self.grid_voltage_model:
            voltage = self.grid_voltage_model.get_voltage_ln()
            self.inverter.grid_voltage = float(voltage)

        # Irradiance
        if self.irradiance_model:
            irradiance = self.irradiance_model.get_irradiance()
            self.panel.set_irradiance(irradiance)

        self._last_telemetry = self.controller.step(dt)

    def get_telemetry(self) -> Dict[str, float]:
        return self._last_telemetry

    def apply_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        return self.controller.apply_commands(commands)

    def init_applied_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        return self.controller.init_applied_commands(commands)