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

from dertwin.devices.external.series_feed import (
    DecayingOffset,
    SeriesFeed,
    SourceSwitch,
)

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

    def seed_from_kt(self, kt: float) -> None:
        """
        Ask the model to report `kt` next, if it can.

        Called when a measured series hands the sky over to the model, so the
        handover is continuous. A model with no state to place — a fixed clear
        sky, a replayed series — has nothing to do here.
        """


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

    def seed_from_kt(self, kt: float) -> None:
        """
        Place the intraday perturbation so the next index reported is `kt`.

        kt is kt_day + x, and kt_day is this date's regime, which a handover
        has no business moving: a measurement that happens to end on a bright
        minute must not turn the whole day clear. So the whole difference goes
        into x, the perturbation, which is the part of the model that is meant
        to carry minute-to-minute weather and which mean-reverts at
        `intraday_tau_s`. The result is a switch with no step in it, followed
        by a decay back to the day's regime at the model's own rate.
        """

        self._intraday.set_value(kt - self._kt_day)

    @property
    def daily_clear_sky_index(self) -> float:
        """Today's regime, before the intraday perturbation."""
        return self._kt_day


class SeriesCloudModel(CloudModel):
    """
    Clear-sky index replayed from a CSV feed.

    With no `fallback` this is the original one-shot model, down to the
    detail that an instant outside the series reports clear sky — the sky the
    clear-sky curve alone would give. That is the wrong answer for anything
    live, which is why a feed that can go stale is given a fallback: when the
    feed has nothing for the instant, the stochastic model takes over, and
    neither the dark sky nor the cloudless one is ever reported for want of
    data.

    The handover is continuous in both directions, and by the same rule — the
    last index reported is carried across the switch and relaxed away at the
    cloud model's own correlation time:

    - into the fallback, by seeding its intraday perturbation, which already
      decays at exactly that rate;
    - back to the series, by an explicit offset with the same time constant,
      because the series is data and cannot be seeded.

    So a switch in either direction is a step of zero, and every step after it
    is one the stochastic model could have produced on its own.
    """

    def __init__(
        self,
        series=None,
        kt_min: float = 0.0,
        feed=None,
        fallback: Optional[CloudModel] = None,
        handover_tau_s: float = 0.0,
    ):
        if feed is None:
            if series is None:
                raise ValueError("SeriesCloudModel needs either a series or a feed")

            from dertwin.devices.external.timeseries import SeriesTable

            feed = SeriesFeed.from_table(
                SeriesTable(
                    columns={"kt": series}, primary="kt",
                    rows=len(series), skipped_rows=0,
                )
            )

        self.feed = feed
        self.series = series
        self.kt_min = kt_min
        self.fallback = fallback
        self.switch = SourceSwitch(feed.name)
        self.handover = DecayingOffset(handover_tau_s)

        self._kt = 1.0

    # --------------------------------------------------

    def _clamp(self, kt: float) -> float:
        return max(self.kt_min, min(1.0, kt))

    def advance(self, utc_time: datetime, dt: float) -> None:
        self.feed.refresh(utc_time)

        sample = self.feed.sample(utc_time)

        if self.fallback is None:
            # One-shot, no fallback configured: unchanged since 0.2.2, clear
            # sky outside the series included.
            self._kt = (
                1.0 if sample is None
                else self._clamp(sample[self.feed.primary])
            )
            return

        if sample is None:
            if self.switch.to_fallback(
                utc_time,
                "no data" if not self.feed.has_data else "past the last row",
            ):
                if self.handover.tau_s > 0.0:
                    self.fallback.seed_from_kt(self._kt)
                self.handover.clear()

            self.fallback.advance(utc_time, dt)
            self._kt = self._clamp(self.fallback.clear_sky_index())
            return

        measured = self._clamp(sample[self.feed.primary])

        if self.switch.to_series(utc_time):
            self.handover.start(self._kt - measured, utc_time)

        # The fallback keeps running while the series drives, so the index it
        # would report is always a current one rather than one frozen at the
        # last switch.
        self.fallback.advance(utc_time, dt)

        self._kt = self._clamp(measured + self.handover.value(utc_time))

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
