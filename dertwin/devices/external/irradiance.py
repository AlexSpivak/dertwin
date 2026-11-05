"""
Site-level irradiance models.

Three of them, selected by `irradiance.model` in the config:

- "sinusoidal" — the original fixed-shape day: a sine between a configured
  sunrise and sunset, identical every day of the year everywhere on earth.
  Still the default so existing configs behave exactly as before.
- "clearsky"   — real solar geometry for a date and location, so sunrise,
  sunset and the seasonal peak are right, multiplied by a stochastic cloud
  index. This is what you want for anything resembling a real site.
- "series"     — GHI read from a CSV, for replaying measured data. The CSV
  can be re-read as it changes on disk, so an external fetcher can drive the
  sky live; when it goes stale the stochastic cloud model takes over.

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
from dertwin.devices.external.series_feed import (
    DEFAULT_STALE_AFTER_S,
    DecayingOffset,
    SeriesFeed,
    SourceSwitch,
)
from dertwin.devices.external.timeseries import (
    INTERPOLATE_LINEAR,
    SeriesTable,
    TimeSeries,
)

logger = logging.getLogger(__name__)


MODEL_SINUSOIDAL = "sinusoidal"
MODEL_CLEARSKY = "clearsky"
MODEL_SERIES = "series"
VALID_IRRADIANCE_MODELS = (MODEL_SINUSOIDAL, MODEL_CLEARSKY, MODEL_SERIES)

SERIES_COLUMN_KT = "kt"
DEFAULT_GHI_COLUMN = "ghi_w_m2"
DEFAULT_DNI_COLUMN = "dni_w_m2"
DEFAULT_DHI_COLUMN = "dhi_w_m2"

# Below this much clear-sky GHI there is no meaningful clear-sky index to
# divide out: the ratio is numerically useless and nothing downstream can
# produce power anyway, so the handover simply has nothing to carry.
KT_CLEAR_SKY_FLOOR_W_M2 = 1.0


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


def resolve_beam_split(
    ghi: float,
    position: SolarPosition,
    utc_time: datetime,
    dni: Optional[float] = None,
    dhi: Optional[float] = None,
) -> tuple:
    """
    Beam and diffuse for a recorded GHI.

    A file that carries measured components should be believed: the Erbs
    correlation is a good guess at the split, and a guess is all it is. So
    whichever of DNI and DHI the file provides is used, the other is derived
    from GHI when only one is there, and Erbs runs only when neither is.

    When the file gives both, they are taken as they stand and are *not*
    forced to close with GHI. Measured triples rarely close exactly, and
    quietly adjusting one of three measured numbers to make the arithmetic
    tidy would hide a data problem rather than report it.
    """

    cos_z = position.cos_zenith

    if dni is not None and dhi is not None:
        return max(0.0, dni), max(0.0, dhi)

    if cos_z > 0.0:
        if dni is not None:
            beam = max(0.0, dni)
            return beam, max(0.0, ghi - beam * cos_z)

        if dhi is not None:
            diffuse = max(0.0, min(dhi, ghi))
            return max(0.0, (ghi - diffuse) / cos_z), diffuse

    return solar.erbs_split(ghi, position, utc_time)


class SeriesIrradianceModel(IrradianceModelBase):
    """
    GHI replayed from a CSV feed, with real solar geometry for the DNI/DHI
    split unless the file carries the components itself.

    With no `fallback` this is the one-shot model it has always been, down to
    instants outside the series being dark. A feed that can go stale is given
    a fallback instead — a full `ClearSkyIrradianceModel` driven by the
    stochastic cloud model — because a live feed that stops arriving must not
    turn the site's sky off. Reusing the clear-sky model wholesale, rather
    than reimplementing a sky here, is what keeps the fallback's irradiance
    indistinguishable from a run that never had a series at all.

    The switch is made continuous in clear-sky-index space rather than in
    W/m², because the quantity that carries over a handover is how cloudy it
    is, not how much irradiance a particular sun elevation happened to
    deliver. Going into the fallback, the model's intraday perturbation is
    seeded with the last observed index; coming back, an offset with the same
    time constant decays onto the measurement. Either way the switch tick
    itself moves GHI no further than the clear-sky curve moves in one tick.
    """

    def __init__(
        self,
        latitude: float,
        longitude: float,
        series: Optional[TimeSeries] = None,
        altitude_m: float = 0.0,
        linke_turbidity: float = 3.0,
        timezone_name: Optional[str] = None,
        feed: Optional[SeriesFeed] = None,
        fallback: Optional["ClearSkyIrradianceModel"] = None,
        handover_tau_s: float = 0.0,
        clearsky_model: str = solar.CLEARSKY_INEICHEN,
        dni_column: Optional[str] = None,
        dhi_column: Optional[str] = None,
    ):
        if feed is None:
            if series is None:
                raise ValueError(
                    "SeriesIrradianceModel needs either a series or a feed"
                )
            feed = SeriesFeed.from_table(
                SeriesTable(
                    columns={DEFAULT_GHI_COLUMN: series},
                    primary=DEFAULT_GHI_COLUMN,
                    rows=len(series),
                    skipped_rows=0,
                )
            )

        self.latitude = latitude
        self.longitude = longitude
        self.feed = feed
        self.series = series
        self.altitude_m = altitude_m
        self.linke_turbidity = linke_turbidity
        self.timezone_name = timezone_name
        self.clearsky_model = clearsky_model
        self.fallback = fallback
        self.dni_column = dni_column
        self.dhi_column = dhi_column

        self.switch = SourceSwitch(feed.name)
        self.handover = DecayingOffset(handover_tau_s)

        self._components = IrradianceComponents.dark()
        self._last_kt: Optional[float] = None

    # --------------------------------------------------

    def _clear_sky(self, position: SolarPosition, utc_time: datetime) -> float:
        if not position.is_up:
            return 0.0

        return solar.clear_sky_ghi(
            position,
            utc_time,
            model=self.clearsky_model,
            altitude_m=self.altitude_m,
            linke_turbidity=self.linke_turbidity,
        )

    def _build(
        self,
        ghi: float,
        dni: float,
        dhi: float,
        position: SolarPosition,
        utc_time: datetime,
        clear_sky: float,
    ) -> IrradianceComponents:
        return solar.limit_beam_to_clear_sky(
            IrradianceComponents(
                ghi=ghi,
                dni=dni,
                dhi=dhi,
                position=position,
                # With no fallback there is no clear-sky curve in play at all,
                # so the recorded value is its own reference and the index is
                # 1 — what this model has always reported.
                clear_sky_ghi=clear_sky if self.fallback is not None else ghi,
                clear_sky_index=(
                    min(1.0, ghi / clear_sky)
                    if self.fallback is not None
                    and clear_sky > KT_CLEAR_SKY_FLOOR_W_M2
                    else 1.0
                ),
            ),
            utc_time,
            altitude_m=self.altitude_m,
            linke_turbidity=self.linke_turbidity,
        )

    def _sample_components(self, sample: dict) -> tuple:
        ghi = float(sample[self.feed.primary])
        dni = sample.get(self.dni_column) if self.dni_column else None
        dhi = sample.get(self.dhi_column) if self.dhi_column else None
        return ghi, dni, dhi

    # --------------------------------------------------

    def advance(self, utc_time: datetime, sim_time: float, dt: float) -> None:
        self.last_utc_time = utc_time
        self.feed.refresh(utc_time)

        position = solar.solar_position(utc_time, self.latitude, self.longitude)
        sample = self.feed.sample(utc_time)

        if self.fallback is None:
            if sample is None or not position.is_up:
                self._components = IrradianceComponents.dark(position)
                return

            ghi, dni, dhi = self._sample_components(sample)

            if ghi <= 0.0:
                self._components = IrradianceComponents.dark(position)
                return

            dni, dhi = resolve_beam_split(ghi, position, utc_time, dni, dhi)
            self._components = self._build(
                ghi, dni, dhi, position, utc_time, clear_sky=ghi
            )
            return

        clear_sky = self._clear_sky(position, utc_time)

        if sample is None:
            if self.switch.to_fallback(
                utc_time,
                "no data" if not self.feed.has_data else "past the last row",
            ):
                if self._last_kt is not None and self.handover.tau_s > 0.0:
                    self.fallback.cloud_model.seed_from_kt(self._last_kt)
                self.handover.clear()

            self.fallback.advance(utc_time, sim_time, dt)
            self._components = self.fallback.get_components()
            self._last_kt = self._components.clear_sky_index
            return

        ghi, dni, dhi = self._sample_components(sample)
        ghi = max(0.0, ghi)

        measured_kt = (
            ghi / clear_sky if clear_sky > KT_CLEAR_SKY_FLOOR_W_M2 else None
        )

        if self.switch.to_series(utc_time) and measured_kt is not None:
            model_kt = self.fallback.get_components().clear_sky_index
            self.handover.start(model_kt - measured_kt, utc_time)

        # Kept running while the series drives, so the index it would report
        # is a current one rather than one frozen at the last switch.
        self.fallback.advance(utc_time, sim_time, dt)

        offset = self.handover.value(utc_time)
        scale = 1.0

        if offset and measured_kt is not None and measured_kt > 0.0:
            scale = max(0.0, measured_kt + offset) / measured_kt

        served = ghi * scale

        if not position.is_up or served <= 0.0:
            self._components = IrradianceComponents.dark(position)
            if measured_kt is not None:
                self._last_kt = measured_kt * scale
            return

        if dni is None and dhi is None:
            # The Erbs split has to be taken from the GHI actually being
            # reported, not from the file's. Erbs is strongly non-linear in
            # the clear-sky index — a kt of 0.5 is ~93% diffuse and a kt of
            # 0.85 ~58% — so splitting the file's GHI and scaling the
            # components afterwards would hand a tilted array the diffuse
            # fraction of a cloudier sky than the one it is being told about.
            # GHI would still be continuous across a handover while plane-of-
            # array irradiance, and so PV power, stepped.
            dni, dhi = resolve_beam_split(served, position, utc_time)
        else:
            # Measured components: one scale for all three keeps both the
            # file's beam/diffuse ratio and whatever closure it had.
            dni, dhi = resolve_beam_split(ghi, position, utc_time, dni, dhi)
            dni, dhi = dni * scale, dhi * scale

        self._components = self._build(
            served, dni, dhi, position, utc_time, clear_sky=clear_sky,
        )
        self._last_kt = (
            self._components.ghi / clear_sky
            if clear_sky > KT_CLEAR_SKY_FLOOR_W_M2
            else self._last_kt
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


def _build_feed(
    cfg: dict, data_root: Path, primary: str, extra_columns=()
) -> SeriesFeed:
    """
    The feed behind `series_csv`.

    `series_refresh_s` of 0 — the default — keeps the one-shot read the key
    has always had. A positive value makes the feed live, and brings the
    staleness fallback with it: `series_stale_after_s` then defaults to an
    hour rather than to "never", because a live feed with no fallback is the
    one combination that has no sensible behaviour when the fetcher stops.

    Setting `series_stale_after_s` on its own is allowed and does what it
    says: a static file, read once, that stops being authoritative a given
    time after its last row.
    """

    csv_path = Path(cfg["series_csv"])
    if not csv_path.is_absolute():
        csv_path = data_root / csv_path

    refresh_s = float(cfg.get("series_refresh_s") or 0.0)
    stale_after_s = cfg.get("series_stale_after_s")

    if stale_after_s is None and refresh_s > 0.0:
        stale_after_s = DEFAULT_STALE_AFTER_S

    return SeriesFeed(
        csv_path,
        primary=primary,
        time_column=cfg.get("series_time_column", "timestamp"),
        extra_columns=extra_columns,
        interpolate=cfg.get("series_interpolate", INTERPOLATE_LINEAR),
        refresh_s=refresh_s,
        stale_after_s=None if stale_after_s is None else float(stale_after_s),
        max_gap_s=float(cfg.get("series_max_gap_s") or 0.0),
    )


def _handover_tau_s(cfg: dict) -> float:
    """
    How long a handover between the series and the model takes to complete.

    Defaults to the cloud model's own intraday correlation time, so the
    handover is not a new timescale in the simulation — it is the existing
    perturbation decaying, at the rate it always decays. `0` turns it off and
    makes each switch a plain jump to the new source.
    """

    explicit = cfg.get("series_handover_tau_s")

    if explicit is not None:
        return float(explicit)

    return float((cfg.get("cloud") or {}).get("intraday_tau_s", 900.0))


def _fallback_cloud_model(cfg: dict, feed: SeriesFeed) -> Optional[CloudModel]:
    """
    The cloud model a stale feed falls back to, or None when the feed cannot
    go stale and so needs none.
    """

    if feed.stale_after_s is None:
        return None

    cloud_model = build_cloud_model(cfg.get("cloud"))

    if isinstance(cloud_model, ClearSkyCloudModel):
        logger.warning(
            "%s can go stale, but clouds are switched off, so the fallback "
            "sky will be cloudless. Leave 'cloud.enabled' alone for a "
            "stochastic fallback.",
            feed.name,
        )

    return cloud_model


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
    series_column = cfg.get("series_column", DEFAULT_GHI_COLUMN)
    altitude_m = cfg.get("altitude_m", 0.0)
    linke_turbidity = cfg.get("linke_turbidity", 3.0)
    clearsky_model = cfg.get("clearsky_model", solar.CLEARSKY_INEICHEN)

    # An absolute GHI series replaces the clear-sky curve entirely; a kt series
    # modulates it in place of the stochastic cloud model.
    if series_csv and series_column != SERIES_COLUMN_KT:
        dni_column = cfg.get("series_dni_column", DEFAULT_DNI_COLUMN)
        dhi_column = cfg.get("series_dhi_column", DEFAULT_DHI_COLUMN)

        feed = _build_feed(
            cfg, data_root, series_column, (dni_column, dhi_column)
        )
        cloud_model = _fallback_cloud_model(cfg, feed)

        fallback = (
            ClearSkyIrradianceModel(
                latitude=latitude,
                longitude=longitude,
                altitude_m=altitude_m,
                clearsky_model=clearsky_model,
                linke_turbidity=linke_turbidity,
                cloud_model=cloud_model,
                timezone_name=timezone_name,
            )
            if cloud_model is not None
            else None
        )

        return SeriesIrradianceModel(
            latitude=latitude,
            longitude=longitude,
            feed=feed,
            altitude_m=altitude_m,
            linke_turbidity=linke_turbidity,
            timezone_name=timezone_name,
            fallback=fallback,
            handover_tau_s=_handover_tau_s(cfg) if fallback else 0.0,
            clearsky_model=clearsky_model,
            dni_column=dni_column,
            dhi_column=dhi_column,
        )

    if model == MODEL_SERIES and not series_csv:
        raise ValueError("irradiance.model 'series' requires 'series_csv'")

    if series_csv:
        from dertwin.devices.external.clouds import SeriesCloudModel

        feed = _build_feed(cfg, data_root, SERIES_COLUMN_KT)
        fallback = _fallback_cloud_model(cfg, feed)

        cloud_model = SeriesCloudModel(
            feed=feed,
            kt_min=(cfg.get("cloud") or {}).get("kt_min", 0.0),
            fallback=fallback,
            handover_tau_s=_handover_tau_s(cfg) if fallback else 0.0,
        )
    else:
        cloud_model = build_cloud_model(cfg.get("cloud"))

    return ClearSkyIrradianceModel(
        latitude=latitude,
        longitude=longitude,
        altitude_m=altitude_m,
        clearsky_model=clearsky_model,
        linke_turbidity=linke_turbidity,
        cloud_model=cloud_model,
        timezone_name=timezone_name,
    )
