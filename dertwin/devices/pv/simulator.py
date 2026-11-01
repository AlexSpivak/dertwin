import logging
from typing import Dict, Optional

from dertwin.core.device import SimulatedDevice
from dertwin.devices.external import solar
from dertwin.devices.external.ambient_temperature import AmbientTemperatureModel
from dertwin.devices.external.grid_frequency import GridFrequencyModel
from dertwin.devices.external.grid_voltage import GridVoltageModel
from dertwin.devices.external.irradiance import IrradianceModelBase
from dertwin.devices.pv.panel import PVArrayModel
from dertwin.devices.pv.inverter import PVInverterModel
from dertwin.devices.pv.pv import PVModel
from dertwin.devices.pv.controller import PVController
from dertwin.telemetry.pv import PVTelemetry

logger = logging.getLogger(__name__)


class PVSimulator(SimulatedDevice):
    """
    Compatibility wrapper around PV architecture.

    Exposes similar public API as old InverterSimulator
    while internally using:
        Panel → Inverter → PVModel → Controller

    DC sizing precedence, highest first:

        1. dc_capacity_kw            explicit STC DC nameplate
        2. area_m2 × module_efficiency   the original formulation
        3. ac_rating_kw × dc_ac_ratio
        4. ac_rating_kw              DC/AC = 1.0, the pre-0.2 default

    `rated_kw` is the AC rating and still works as the only sizing input, in
    which case DC/AC stays 1.0 and the array never clips — same as before.
    Real plants run 1.15–1.35 and clip for hours in summer, so set
    `dc_ac_ratio` or `dc_capacity_kw` for anything realistic.
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
        irradiance_model: Optional[IrradianceModelBase] = None,
        ac_rating_kw: Optional[float] = None,
        dc_capacity_kw: Optional[float] = None,
        dc_ac_ratio: Optional[float] = None,
        tilt_deg: Optional[float] = None,
        azimuth_deg: Optional[float] = None,
        albedo: float = 0.2,
        derate: Optional[float] = None,
        losses: Optional[Dict[str, float]] = None,
        temp_coefficient: float = -0.004,
        noct_c: float = 45.0,
    ):
        super().__init__()

        ac_rating_kw = ac_rating_kw if ac_rating_kw is not None else rated_kw
        rated_power_w = ac_rating_kw * 1000.0

        # --- DC sizing ---
        if dc_capacity_kw is None and area_m2 is None:
            dc_capacity_kw = ac_rating_kw * (
                dc_ac_ratio if dc_ac_ratio is not None else 1.0
            )
        elif dc_capacity_kw is None and dc_ac_ratio is not None:
            logger.warning(
                "Both area_m2 and dc_ac_ratio given; area_m2 wins and "
                "dc_ac_ratio is ignored."
            )

        # --- Loss derate: itemised losses multiply out and win over `derate` ---
        if losses:
            resolved_derate = 1.0
            for value in losses.values():
                resolved_derate *= float(value)
        elif derate is not None:
            resolved_derate = derate
        else:
            resolved_derate = 1.0

        # --- Array orientation ---
        latitude = getattr(irradiance_model, "latitude", None)

        if tilt_deg is None and azimuth_deg is not None and latitude is not None:
            tilt_deg = solar.default_tilt_deg(latitude)

        if tilt_deg is not None and azimuth_deg is None:
            azimuth_deg = (
                solar.default_azimuth_deg(latitude) if latitude is not None else 180.0
            )

        # --- Core models ---
        self.panel = PVArrayModel(
            area_m2=area_m2,
            module_efficiency=module_efficiency,
            ambient_temp_c=ambient_temp_c,
            dc_capacity_kw=dc_capacity_kw,
            tilt_deg=tilt_deg,
            azimuth_deg=azimuth_deg,
            albedo=albedo,
            derate=resolved_derate,
            temp_coefficient=temp_coefficient,
            noct_c=noct_c,
        )

        self.inverter = PVInverterModel(
            rated_ac_power_w=rated_power_w,
            ambient_temp_c=ambient_temp_c,
        )

        self.pv = PVModel(self.panel, self.inverter)
        self.controller = PVController(self.pv)

        self._last_telemetry: PVTelemetry = PVTelemetry.zero()
        self._utc_date = None

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

    @property
    def dc_capacity_kw(self):
        return self.panel.dc_capacity_kw

    @property
    def ac_rating_kw(self):
        return self.inverter.rated_ac_power_w / 1000.0

    @property
    def tilt_deg(self):
        return self.panel.tilt_deg

    @property
    def azimuth_deg(self):
        return self.panel.azimuth_deg

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
            get_components = getattr(self.irradiance_model, "get_components", None)
            if get_components is not None:
                self.panel.set_irradiance_components(get_components())
            else:
                self.panel.set_irradiance(self.irradiance_model.get_irradiance())

            self._roll_over_day()

        self._last_telemetry = self.controller.step(dt)

    def _roll_over_day(self) -> None:
        """Zero today_output_energy when the UTC date changes."""
        utc_time = getattr(self.irradiance_model, "last_utc_time", None)
        if utc_time is None:
            return

        date = utc_time.date()

        if self._utc_date is not None and date != self._utc_date:
            self.pv.reset_today_energy()

        self._utc_date = date

    def get_telemetry(self) -> PVTelemetry:
        return self._last_telemetry

    def apply_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        return self.controller.apply_commands(commands)

    def init_applied_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        return self.controller.init_applied_commands(commands)
