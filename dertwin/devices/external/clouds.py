"""
Cloud cover as a clear-sky index.

A cloud model produces kt in [kt_min, 1], the fraction of clear-sky GHI that
actually reaches the ground. Multiplying clear-sky irradiance by it gives a
realistic sky without modelling any actual meteorology.

Two timescales, because real weather has both:

- day to day, persistent regimes: an overcast Tuesday makes an overcast
  Wednesday more likely. Modelled as an AR(1) process on a logit-transformed
  daily index, so a single `phi` controls how long spells last.
- within the day, passing cloud: an Ornstein-Uhlenbeck perturbation with a
  correlation time of minutes.

Everything is seedable and deterministic. With a seed, the daily regime is
keyed on the UTC calendar date: the weather of a given day is a function of
(seed, date) and of nothing else, so restarting the process — however often,
and whatever date the run starts on — cannot change it. Without a seed the run
is random, as it has always been.
"""

import math
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Optional

from dertwin.devices.external.stochastic import (
    KeyedAR1,
    OrnsteinUhlenbeck,
    derive_seed,
)


CLOUD_MODEL_NONE = "none"
CLOUD_MODEL_AR1 = "ar1"
CLOUD_MODEL_MARKOV = "markov"
VALID_CLOUD_MODELS = (CLOUD_MODEL_NONE, CLOUD_MODEL_AR1, CLOUD_MODEL_MARKOV)

# Random stream names. The daily regime and the intraday perturbation draw
# from separate streams, so neither can shift the other's numbers.
_DAY_STREAM = "cloud-day"
_INTRADAY_STREAM = "cloud-intraday"


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _expit(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


class CloudModel(ABC):
    """Produces the clear-sky index for the current instant."""

    @abstractmethod
    def advance(self, utc_time: datetime, dt: float) -> None:
        ...

    @abstractmethod
    def clear_sky_index(self) -> float:
        ...


class ClearSkyCloudModel(CloudModel):
    """No clouds — kt is always 1.0. What `cloud.enabled: false` selects."""

    def advance(self, utc_time: datetime, dt: float) -> None:
        pass

    def clear_sky_index(self) -> float:
        return 1.0


class AR1CloudModel(CloudModel):
    """
    AR(1) daily regime plus an Ornstein-Uhlenbeck intraday perturbation.

    The daily index is drawn in logit space and mapped into [kt_min, 1], which
    keeps it bounded without the clipping artefacts of working on kt directly.
    By symmetry of the innovation the *median* daily index is exactly mean_kt;
    the mean sits a little closer to the middle of the range.

        z      = keyed AR(1) at the UTC date's day number (persistence phi,
                 stationary spread sigma_day)
        kt_day = kt_min + (1 - kt_min)·expit(logit(m) + z)
        x     <- a·x + sqrt(1 - a²)·intraday_sigma·N(0,1)   every step, a = exp(-dt/tau)
        kt     = clamp(kt_day + x, kt_min, 1)

    The daily regime is *keyed*, not stepped: `z` for a date is read straight
    out of `KeyedAR1` at that date's proleptic day number, so it depends on
    (seed, date) and not on when the process started or how long it has been
    up. A process restarted three times on the same day reports the same
    regime each time, and starting the run a hundred days earlier changes
    nothing about any later day. The alternative — an independent draw per
    date — would be restart-proof too, but it would set phi to zero and take
    the day-to-day persistence with it; `KeyedAR1` explains how the sum keeps
    both.

    The intraday perturbation is deliberately *not* keyed. It is advanced with
    whatever dt the engine hands it, which is the only way its spread can stay
    independent of the simulator's timestep, and it is a zero-mean wobble with
    a correlation time of minutes: nothing about it is worth reproducing
    across a restart, and nothing about it moves the day's regime. It is drawn
    from its stationary distribution at construction, so there is no warm-up
    transient either way.
    """

    def __init__(
        self,
        mean_kt: float = 0.62,
        phi: float = 0.65,
        sigma_day: float = 0.9,
        kt_min: float = 0.08,
        intraday_sigma: float = 0.07,
        intraday_tau_s: float = 900.0,
        seed: Optional[int] = None,
    ):
        if not 0.0 <= kt_min < 1.0:
            raise ValueError(f"kt_min must be in [0, 1), got {kt_min}")
        if not kt_min < mean_kt < 1.0:
            raise ValueError(
                f"mean_kt must be in (kt_min, 1), got {mean_kt} with kt_min={kt_min}"
            )
        if not 0.0 <= phi < 1.0:
            raise ValueError(f"phi must be in [0, 1), got {phi}")
        if sigma_day < 0.0:
            raise ValueError(f"sigma_day must be non-negative, got {sigma_day}")
        if intraday_sigma < 0.0:
            raise ValueError(
                f"intraday_sigma must be non-negative, got {intraday_sigma}"
            )
        if intraday_tau_s <= 0.0:
            raise ValueError(
                f"intraday_tau_s must be positive, got {intraday_tau_s}"
            )

        self.mean_kt = mean_kt
        self.phi = phi
        self.sigma_day = sigma_day
        self.kt_min = kt_min
        self.intraday_sigma = intraday_sigma
        self.intraday_tau_s = intraday_tau_s

        self.seed = seed
        self._mean_logit = _logit((mean_kt - kt_min) / (1.0 - kt_min))

        self._day_regime = KeyedAR1(
            phi=phi, sigma=sigma_day, seed=seed, stream=_DAY_STREAM
        )
        self._intraday = OrnsteinUhlenbeck(
            sigma=intraday_sigma,
            tau_s=intraday_tau_s,
            seed=derive_seed(seed, _INTRADAY_STREAM),
        )

        self._date: Optional[date] = None

        # Until the first advance names a date there is no day to look up, so
        # the regime sits at the median, which is mean_kt.
        self._z = 0.0
        self._kt_day = self._kt_day_from_z()

    # --------------------------------------------------

    @property
    def day_regime_window(self) -> int:
        """How many keyed innovations one day's regime is summed from."""
        return self._day_regime.window

    def _kt_day_from_z(self) -> float:
        return self.kt_min + (1.0 - self.kt_min) * _expit(self._mean_logit + self._z)

    def _set_day(self, day: date) -> None:
        self._z = self._day_regime.value_at(day.toordinal())
        self._kt_day = self._kt_day_from_z()

    # --------------------------------------------------

    def advance(self, utc_time: datetime, dt: float) -> None:
        today = utc_time.date()

        if self._date is None or today > self._date:
            # Looking the day up costs the same whichever day it is, so a
            # fresh start, the next midnight and a jump of ten thousand days
            # are all one evaluation. There is no catch-up loop to bound.
            self._set_day(today)
            self._date = today
        elif today < self._date:
            # A clock that moved backwards resyncs without redrawing, so a
            # device that rewinds its clock does not get a new sky for the
            # instant it lands on. The next day it moves into is keyed as
            # usual.
            self._date = today

        self._intraday.advance(dt)

    def clear_sky_index(self) -> float:
        return max(self.kt_min, min(1.0, self._kt_day + self._intraday.value))

    @property
    def daily_clear_sky_index(self) -> float:
        """Today's regime, before the intraday perturbation."""
        return self._kt_day


class SeriesCloudModel(CloudModel):
    """
    Clear-sky index replayed from a CSV, for driving the sky from recorded
    cloud cover. Instants outside the series fall back to clear sky.
    """

    def __init__(self, series, kt_min: float = 0.0):
        self.series = series
        self.kt_min = kt_min
        self._kt = 1.0

    def advance(self, utc_time: datetime, dt: float) -> None:
        value = self.series.value_at(utc_time)
        self._kt = 1.0 if value is None else max(self.kt_min, min(1.0, float(value)))

    def clear_sky_index(self) -> float:
        return self._kt


def build_cloud_model(config: Optional[dict]) -> CloudModel:
    """
    Build a cloud model from the `irradiance.cloud` config block.

    Clouds are ON by default, because a permanently cloudless sky is no more
    realistic than a fixed 12-hour day. `enabled: false` (or `model: "none"`)
    turns them off. Note the asymmetry with `ClearSkyIrradianceModel`, whose
    own `cloud_model=None` default means no clouds: configs opt in by default,
    direct construction does not.

    Set `cloud.seed` for reproducible runs. With a seed, each UTC date's
    regime is a function of (seed, date), so restarts and a changed start date
    leave a given day's weather alone. Without one the run is random, and a
    restart gets a fresh sky.
    """

    cfg = config or {}

    if not cfg.get("enabled", True):
        return ClearSkyCloudModel()

    model = cfg.get("model", CLOUD_MODEL_AR1)

    if model == CLOUD_MODEL_NONE:
        return ClearSkyCloudModel()

    if model == CLOUD_MODEL_MARKOV:
        raise NotImplementedError(
            "cloud.model 'markov' is not implemented yet; use 'ar1'"
        )

    if model != CLOUD_MODEL_AR1:
        raise ValueError(
            f"Unknown cloud.model '{model}'. Must be one of {VALID_CLOUD_MODELS}."
        )

    return AR1CloudModel(
        mean_kt=cfg.get("mean_kt", 0.62),
        phi=cfg.get("phi", 0.65),
        sigma_day=cfg.get("sigma_day", 0.9),
        kt_min=cfg.get("kt_min", 0.08),
        intraday_sigma=cfg.get("intraday_sigma", 0.07),
        intraday_tau_s=cfg.get("intraday_tau_s", 900.0),
        seed=cfg.get("seed"),
    )
