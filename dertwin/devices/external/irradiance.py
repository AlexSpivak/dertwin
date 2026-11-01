"""
Site-level irradiance models.

Three of them, selected by `irradiance.model` in the config:

- "sinusoidal" — the original fixed-shape day: a sine between a configured
  sunrise and sunset, identical every day of the year everywhere on earth.
  Still the default so existing configs behave exactly as before.
- "clearsky"   — real solar geometry for a date and location, so sunrise,
  sunset and the seasonal peak are right, multiplied by a stochastic cloud
  index. This is what you want for anything resembling a real site.
- "series"     — GHI read from a CSV, for replaying measured data.

All three publish `IrradianceComponents` (GHI, DNI, DHI and the sun's
position). Plane-of-array transposition is deliberately *not* done here: one
irradiance model is shared by every PV asset on the site, and each array has
its own tilt and azimuth, so transposition belongs to the array.
"""

import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

from dertwin.devices.external import solar
from dertwin.devices.external.clouds import (
    CloudModel,
    ClearSkyCloudModel,
    build_cloud_model,
)
from dertwin.devices.external.solar import (
    IrradianceComponents,
    SolarPosition,
    VALID_CLEARSKY_MODELS,
)
from dertwin.devices.external.timeseries import (
    INTERPOLATE_LINEAR,
    TimeSeries,
)

logger = logging.getLogger(__name__)


MODEL_SINUSOIDAL = "sinusoidal"
MODEL_CLEARSKY = "clearsky"
MODEL_SERIES = "series"
VALID_IRRADIANCE_MODELS = (MODEL_SINUSOIDAL, MODEL_CLEARSKY, MODEL_SERIES)

SERIES_COLUMN_KT = "kt"


class IrradianceModelBase:
    """
    Common interface.

    `advance` gets both an absolute UTC instant and the relative simulation
    time: date-aware models use the former, the legacy fixed-shape model the
    latter. Every model records the instant it was last advanced to, which is
    how PV assets find the UTC day boundary without needing a clock of their
    own.
    """

    last_utc_time: Optional[datetime] = None

    def advance(self, utc_time: datetime, sim_time: float, dt: float) -> None:
        raise NotImplementedError

    def get_components(self) -> IrradianceComponents:
        raise NotImplementedError

    def get_irradiance(self) -> float:
        """Global horizontal irradiance, W/m²."""
        return self.get_components().ghi


class IrradianceModel(IrradianceModelBase):
    """
    Deterministic clear-sky irradiance model.

    Features:
    - Daily sinusoidal irradiance curve
    - Configurable peak irradiance
    - Configurable sunrise/sunset
    - Fully deterministic
    - 24h periodic

    The shape does not depend on date or location — every day is the same
    length and the same height. `ClearSkyIrradianceModel` is the date- and
    location-aware alternative; this one is kept as-is so existing configs and
    their expectations are untouched.
    """

    def __init__(
        self,
        peak_irradiance_w_m2: float = 1000.0,
        sunrise_hour: float = 6.0,
        sunset_hour: float = 18.0,
    ):
        if sunset_hour <= sunrise_hour:
            raise ValueError("sunset_hour must be greater than sunrise_hour")

        self.peak = peak_irradiance_w_m2
        self.sunrise = sunrise_hour
        self.sunset = sunset_hour

        if peak_irradiance_w_m2 < 0:
            raise ValueError("peak_irradiance_w_m2 must be positive")
        self._irradiance = 0.0

    # --------------------------------------------------

    def update(self, sim_time: float, dt: float):
        """
        sim_time: seconds since simulation start
        dt: timestep (unused, deterministic model)
        """

        hours = (sim_time / 3600.0) % 24.0

        if hours <= self.sunrise or hours >= self.sunset:
            self._irradiance = 0.0
            return

        daylight_duration = self.sunset - self.sunrise
        normalized_time = (hours - self.sunrise) / daylight_duration

        self._irradiance = (
            self.peak * math.sin(math.pi * normalized_time)
        )

    def advance(self, utc_time: datetime, sim_time: float, dt: float) -> None:
        self.last_utc_time = utc_time
        self.update(sim_time, dt)

    # --------------------------------------------------

    def get_irradiance(self) -> float:
        return max(self._irradiance, 0.0)

    def set_irradiance(self, irradiance: float):
        self._irradiance = irradiance

    def get_components(self) -> IrradianceComponents:
        """
        No solar geometry exists for this model, so `has_solar_geometry` is
        False and a PV array treats the value as already in-plane.
        """
        ghi = self.get_irradiance()
        return IrradianceComponents(
            ghi=ghi,
            dni=0.0,
            dhi=ghi,
            position=SolarPosition(
                elevation_deg=90.0 if ghi > 0.0 else -90.0,
                azimuth_deg=180.0,
                zenith_deg=0.0 if ghi > 0.0 else 180.0,
            ),
            clear_sky_ghi=ghi,
            clear_sky_index=1.0,
            has_solar_geometry=False,
        )


class ClearSkyIrradianceModel(IrradianceModelBase):
    """
    Date- and location-aware irradiance.

    Pipeline per step:

        1. solar position for the UTC instant at (latitude, longitude)
        2. clear-sky GHI (Ineichen-Perez by default, or Haurwitz)
        3. × the cloud model's clear-sky index
        4. split into DNI/DHI with the Erbs correlation

    Step 4 runs on the *clouded* GHI, so a thick overcast comes out almost
    entirely diffuse on its own.
    """

    def __init__(
        self,
        latitude: float,
        longitude: float,
        altitude_m: float = 0.0,
        clearsky_model: str = solar.CLEARSKY_INEICHEN,
        linke_turbidity: float = 3.0,
        cloud_model: Optional[CloudModel] = None,
        timezone_name: Optional[str] = None,
    ):
        if not -90.0 <= latitude <= 90.0:
            raise ValueError(f"latitude must be in [-90, 90], got {latitude}")
        if not -180.0 <= longitude <= 180.0:
            raise ValueError(f"longitude must be in [-180, 180], got {longitude}")
        if clearsky_model not in VALID_CLEARSKY_MODELS:
            raise ValueError(
                f"Unknown clearsky_model '{clearsky_model}'. "
                f"Must be one of {VALID_CLEARSKY_MODELS}."
            )
        if linke_turbidity <= 0.0:
            raise ValueError(
                f"linke_turbidity must be positive, got {linke_turbidity}"
            )

        self.latitude = latitude
        self.longitude = longitude
        self.altitude_m = altitude_m
        self.clearsky_model = clearsky_model
        self.linke_turbidity = linke_turbidity
        self.cloud_model = cloud_model or ClearSkyCloudModel()

        # Display only — every internal instant is UTC.
        self.timezone_name = timezone_name

        self._components = IrradianceComponents.dark()

    # --------------------------------------------------

    def advance(self, utc_time: datetime, sim_time: float, dt: float) -> None:
        self.last_utc_time = utc_time
        self.cloud_model.advance(utc_time, dt)

        position = solar.solar_position(utc_time, self.latitude, self.longitude)

        if not position.is_up:
            self._components = IrradianceComponents.dark(position)
            return

        clear_sky = solar.clear_sky_ghi(
            position,
            utc_time,
            model=self.clearsky_model,
            altitude_m=self.altitude_m,
            linke_turbidity=self.linke_turbidity,
        )

        kt = self.cloud_model.clear_sky_index()
        ghi = max(0.0, clear_sky * kt)
        dni, dhi = solar.erbs_split(ghi, position, utc_time)

        self._components = solar.limit_beam_to_clear_sky(
            IrradianceComponents(
                ghi=ghi,
                dni=dni,
                dhi=dhi,
                position=position,
                clear_sky_ghi=clear_sky,
                clear_sky_index=kt,
            ),
            utc_time,
            altitude_m=self.altitude_m,
            linke_turbidity=self.linke_turbidity,
        )

    def get_components(self) -> IrradianceComponents:
        return self._components

    def daylight_hours(self, utc_time: datetime) -> float:
        return solar.daylight_hours(utc_time, self.latitude, self.longitude)


class SeriesIrradianceModel(IrradianceModelBase):
    """
    GHI replayed from a CSV, with real solar geometry for the DNI/DHI split.

    Instants outside the series are dark, with one warning.
    """

    def __init__(
        self,
        latitude: float,
        longitude: float,
        series: TimeSeries,
        altitude_m: float = 0.0,
        linke_turbidity: float = 3.0,
        timezone_name: Optional[str] = None,
    ):
        self.latitude = latitude
        self.longitude = longitude
        self.series = series
        self.altitude_m = altitude_m
        self.linke_turbidity = linke_turbidity
        self.timezone_name = timezone_name

        self._components = IrradianceComponents.dark()

    def advance(self, utc_time: datetime, sim_time: float, dt: float) -> None:
        self.last_utc_time = utc_time
        position = solar.solar_position(utc_time, self.latitude, self.longitude)
        value = self.series.value_at(utc_time)

        if value is None or value <= 0.0 or not position.is_up:
            self._components = IrradianceComponents.dark(position)
            return

        ghi = float(value)
        dni, dhi = solar.erbs_split(ghi, position, utc_time)

        self._components = solar.limit_beam_to_clear_sky(
            IrradianceComponents(
                ghi=ghi,
                dni=dni,
                dhi=dhi,
                position=position,
                clear_sky_ghi=ghi,
                clear_sky_index=1.0,
            ),
            utc_time,
            altitude_m=self.altitude_m,
            linke_turbidity=self.linke_turbidity,
        )

    def get_components(self) -> IrradianceComponents:
        return self._components


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------

def _resolve_model_name(cfg: dict) -> str:
    explicit = cfg.get("model")

    if explicit is not None:
        if explicit not in VALID_IRRADIANCE_MODELS:
            raise ValueError(
                f"Unknown irradiance.model '{explicit}'. "
                f"Must be one of {VALID_IRRADIANCE_MODELS}."
            )
        return explicit

    # Giving a location is an unambiguous request for real solar geometry.
    if "latitude" in cfg:
        return MODEL_CLEARSKY

    return MODEL_SINUSOIDAL


def _build_series(cfg: dict, data_root: Path) -> TimeSeries:
    csv_path = Path(cfg["series_csv"])
    if not csv_path.is_absolute():
        csv_path = data_root / csv_path

    return TimeSeries.from_csv(
        csv_path,
        value_column=cfg.get("series_column", "ghi_w_m2"),
        time_column=cfg.get("series_time_column", "timestamp"),
        interpolate=cfg.get("series_interpolate", INTERPOLATE_LINEAR),
    )


def build_irradiance_model(
    config: Optional[dict],
    data_root: Optional[Path] = None,
    sim_start_utc_configured: bool = False,
) -> IrradianceModelBase:
    """
    Build the site irradiance model from the `irradiance` config block.

    Defaults to the legacy sinusoidal model, so a config written before any of
    this existed keeps the behaviour it had. Supplying `latitude` — or setting
    `model` explicitly — selects a date- and location-aware model.
    """

    cfg = dict(config or {})
    data_root = Path(data_root or ".")

    model = _resolve_model_name(cfg)

    if model == MODEL_SINUSOIDAL:
        return IrradianceModel(
            peak_irradiance_w_m2=cfg.get("peak", 1000.0),
            sunrise_hour=cfg.get("sunrise", 6.0),
            sunset_hour=cfg.get("sunset", 18.0),
        )

    for key in ("latitude", "longitude"):
        if cfg.get(key) is None:
            raise ValueError(f"irradiance.model '{model}' requires '{key}'")

    # A date-aware model with no date would silently simulate January 1970.
    if not sim_start_utc_configured:
        raise ValueError(
            f"irradiance.model '{model}' requires a top-level 'sim_start_utc' "
            "(ISO 8601 UTC, e.g. \"2025-10-31T00:00:00Z\") — without a date "
            "there is no sun position to compute."
        )

    latitude = float(cfg["latitude"])
    longitude = float(cfg["longitude"])
    timezone_name = cfg.get("timezone")

    series_csv = cfg.get("series_csv")
    series_column = cfg.get("series_column", "ghi_w_m2")

    # An absolute GHI series replaces the clear-sky curve entirely; a kt series
    # modulates it in place of the stochastic cloud model.
    if series_csv and series_column != SERIES_COLUMN_KT:
        return SeriesIrradianceModel(
            latitude=latitude,
            longitude=longitude,
            series=_build_series(cfg, data_root),
            altitude_m=cfg.get("altitude_m", 0.0),
            linke_turbidity=cfg.get("linke_turbidity", 3.0),
            timezone_name=timezone_name,
        )

    if model == MODEL_SERIES and not series_csv:
        raise ValueError("irradiance.model 'series' requires 'series_csv'")

    if series_csv:
        from dertwin.devices.external.clouds import SeriesCloudModel

        cloud_model = SeriesCloudModel(
            _build_series(cfg, data_root),
            kt_min=(cfg.get("cloud") or {}).get("kt_min", 0.0),
        )
    else:
        cloud_model = build_cloud_model(cfg.get("cloud"))

    return ClearSkyIrradianceModel(
        latitude=latitude,
        longitude=longitude,
        altitude_m=cfg.get("altitude_m", 0.0),
        clearsky_model=cfg.get("clearsky_model", solar.CLEARSKY_INEICHEN),
        linke_turbidity=cfg.get("linke_turbidity", 3.0),
        cloud_model=cloud_model,
        timezone_name=timezone_name,
    )
