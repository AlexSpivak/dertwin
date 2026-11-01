import logging
from typing import Optional

from dertwin.devices.external import solar
from dertwin.devices.external.solar import IrradianceComponents

logger = logging.getLogger(__name__)


class PVArrayModel:
    """
    DC-side photovoltaic array model.

    Deterministic physics:
    - Irradiance-based production
    - Optional plane-of-array transposition for a tilted, oriented array
    - Temperature derating
    - Simple thermal cell model
    - Optional system loss derate

    Two DC sizing paths:

    - capacity: `dc_capacity_kw` is the STC nameplate, and production scales
      with in-plane irradiance over 1000 W/m². Preferred for new configs.
    - area: `area_m2` × `module_efficiency`, the original formulation. Used
      whenever `dc_capacity_kw` is not given, so existing configs are
      unaffected.

    Transposition only happens when `tilt_deg` is configured *and* the
    irradiance source knows where the sun is. Otherwise the incoming value is
    treated as already in-plane, which is what the legacy sinusoidal
    irradiance model implies.
    """

    def __init__(
        self,
        area_m2: Optional[float] = None,
        module_efficiency: float = 0.20,
        temp_coefficient: float = -0.004,  # per °C
        noct_c: float = 45.0,  # nominal operating cell temp
        ambient_temp_c: float = 25.0,
        dc_capacity_kw: Optional[float] = None,
        tilt_deg: Optional[float] = None,
        azimuth_deg: Optional[float] = None,
        albedo: float = 0.2,
        derate: float = 1.0,
    ):
        if dc_capacity_kw is None and area_m2 is None:
            raise ValueError("PVArrayModel needs either dc_capacity_kw or area_m2")

        if not 0.0 < derate <= 1.0:
            raise ValueError(f"derate must be in (0, 1], got {derate}")

        self.area_m2 = area_m2
        self.module_efficiency = module_efficiency
        self.temp_coefficient = temp_coefficient
        self.noct_c = noct_c
        self.dc_capacity_kw = dc_capacity_kw
        self.tilt_deg = tilt_deg
        self.azimuth_deg = azimuth_deg
        self.albedo = albedo
        self.derate = derate

        self.irradiance_w_per_m2 = 0.0
        self.ambient_temp_c = ambient_temp_c
        self.cell_temperature_c = ambient_temp_c

        self._warned_missing_geometry = False

    # -------------------------------------------------
    # External inputs
    # -------------------------------------------------

    def set_irradiance(self, irradiance_w_per_m2: float):
        """Set in-plane irradiance directly, bypassing any transposition."""
        self.irradiance_w_per_m2 = max(0.0, irradiance_w_per_m2)

    def set_irradiance_components(self, components: IrradianceComponents):
        """Set in-plane irradiance from horizontal components."""
        self.irradiance_w_per_m2 = max(
            0.0, self.plane_of_array_irradiance(components)
        )

    def plane_of_array_irradiance(self, components: IrradianceComponents) -> float:
        if self.tilt_deg is None:
            return components.ghi

        if not components.has_solar_geometry:
            if not self._warned_missing_geometry:
                self._warned_missing_geometry = True
                logger.warning(
                    "tilt_deg is configured but the irradiance model has no "
                    "solar geometry (irradiance.model='sinusoidal'); treating "
                    "irradiance as already in-plane. Set irradiance.model to "
                    "'clearsky' to use tilt and azimuth."
                )
            return components.ghi

        return solar.plane_of_array_irradiance(
            components,
            tilt_deg=self.tilt_deg,
            # Due south unless told otherwise; PVSimulator resolves the
            # hemisphere-aware default from the site latitude.
            azimuth_deg=180.0 if self.azimuth_deg is None else self.azimuth_deg,
            albedo=self.albedo,
        )

    def set_ambient_temperature(self, temp_c: float):
        self.ambient_temp_c = temp_c

    # -------------------------------------------------
    # Thermal model (NOCT approximation)
    # -------------------------------------------------

    def update_cell_temperature(self):
        # Simplified NOCT-based temperature model
        delta = (self.noct_c - 20.0) / 800.0 * self.irradiance_w_per_m2
        self.cell_temperature_c = self.ambient_temp_c + delta

    # -------------------------------------------------
    # DC Power Output
    # -------------------------------------------------

    def dc_power_w(self) -> float:
        self.update_cell_temperature()

        if self.irradiance_w_per_m2 <= 0:
            return 0.0

        # Temperature correction
        temp_factor = 1.0 + self.temp_coefficient * (
            self.cell_temperature_c - 25.0
        )

        if self.dc_capacity_kw is not None:
            raw_power = (
                self.dc_capacity_kw
                * 1000.0
                * (self.irradiance_w_per_m2 / 1000.0)
                * temp_factor
            )
        else:
            raw_power = (
                self.irradiance_w_per_m2
                * self.area_m2
                * self.module_efficiency
                * temp_factor
            )

        return max(0.0, raw_power * self.derate)
