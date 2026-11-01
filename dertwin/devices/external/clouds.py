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

Everything is seedable and deterministic.
"""

import logging
import math
import random
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


CLOUD_MODEL_NONE = "none"
CLOUD_MODEL_AR1 = "ar1"
CLOUD_MODEL_MARKOV = "markov"
VALID_CLOUD_MODELS = (CLOUD_MODEL_NONE, CLOUD_MODEL_AR1, CLOUD_MODEL_MARKOV)

# Guard against a config that jumps the clock by centuries.
_MAX_DAY_CATCHUP = 10_000


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

        z   <- phi·z + sqrt(1 - phi²)·sigma_day·N(0,1)      once per UTC day
        kt_day = kt_min + (1 - kt_min)·expit(logit(m) + z)
        x   <- a·x + sqrt(1 - a²)·intraday_sigma·N(0,1)     every step, a = exp(-dt/tau)
        kt  = clamp(kt_day + x, kt_min, 1)

    Both processes are drawn from their stationary distribution at
    construction, so there is no warm-up transient.
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

        self._rng = random.Random(seed)
        self._mean_logit = _logit((mean_kt - kt_min) / (1.0 - kt_min))

        # Start both processes in their stationary distribution
        self._z = self._rng.gauss(0.0, sigma_day)
        self._x = self._rng.gauss(0.0, intraday_sigma)

        self._date = None
        self._kt_day = self._kt_day_from_z()

    # --------------------------------------------------

    def _kt_day_from_z(self) -> float:
        return self.kt_min + (1.0 - self.kt_min) * _expit(self._mean_logit + self._z)

    def _step_day(self) -> None:
        self._z = self.phi * self._z + math.sqrt(
            1.0 - self.phi ** 2
        ) * self.sigma_day * self._rng.gauss(0.0, 1.0)
        self._kt_day = self._kt_day_from_z()

    def _step_intraday(self, dt: float) -> None:
        # Skipping the draw, not just scaling it to zero, keeps the random
        # stream independent of how many steps the intraday process took.
        if dt <= 0.0 or self.intraday_sigma == 0.0:
            return

        a = math.exp(-dt / self.intraday_tau_s)
        self._x = a * self._x + math.sqrt(
            1.0 - a ** 2
        ) * self.intraday_sigma * self._rng.gauss(0.0, 1.0)

    # --------------------------------------------------

    def advance(self, utc_time: datetime, dt: float) -> None:
        date = utc_time.date()

        if self._date is None:
            self._date = date
        elif date != self._date:
            elapsed_days = (date - self._date).days

            if elapsed_days > _MAX_DAY_CATCHUP:
                logger.warning(
                    "Cloud model skipped %d days at once; advancing %d and "
                    "resyncing to %s",
                    elapsed_days, _MAX_DAY_CATCHUP, date,
                )
                elapsed_days = _MAX_DAY_CATCHUP

            # A clock that moved backwards just resyncs, without redrawing.
            for _ in range(max(0, elapsed_days)):
                self._step_day()

            self._date = date

        self._step_intraday(dt)

    def clear_sky_index(self) -> float:
        return max(self.kt_min, min(1.0, self._kt_day + self._x))

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
    direct construction does not. Set `cloud.seed` for reproducible runs.
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
