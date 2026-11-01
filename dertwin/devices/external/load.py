"""
Site load model.

The site load used to be a constant: `lambda t: base_load`, five kilowatts by
default, the same at 03:00 as at 18:00 and the same on a Sunday as on a
Tuesday. An EMS tested against that never sees the thing it exists to handle.

This model keeps the load multiplicative, which is what makes it configurable
without the pieces fighting each other:

    load_kw = base_kw · profile(hour) · weekday · drift · (1 + noise)

- `profile` is the daily shape: flat, 24 hourly factors, a simple parametric
  two-peak curve, or a CSV. Normalised to average 1.0 so it only ever changes
  the shape, never the mean.
- `weekday` is one of two factors, picked by the day of the week.
- `drift` is a slow mean-reverting random walk (Ornstein-Uhlenbeck, hours), the
  "this week is busier than usual" term, bounded.
- `noise` is a fast one (seconds to a minute), the switching of individual
  equipment.

Everything is seedable, and one seed drives both random terms.

The model is an external world model, not a device: it has no registers and no
protocol of its own. It reaches the meter the way the constant always did,
through the site power balance.
"""

import csv
import logging
import math
import random
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from dertwin.devices.external.stochastic import OrnsteinUhlenbeck

logger = logging.getLogger(__name__)


PROFILE_FLAT = "flat"
PROFILE_HOURLY = "hourly"
PROFILE_PARAMETRIC = "parametric"
PROFILE_CSV = "csv"
VALID_PROFILE_MODELS = (
    PROFILE_FLAT, PROFILE_HOURLY, PROFILE_PARAMETRIC, PROFILE_CSV,
)

WEEKDAY_NAMES = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)

HOURS_PER_DAY = 24.0
SECONDS_PER_DAY = 86400.0

# Resolution used to normalise a profile to a mean of 1.0.
_NORMALISE_SAMPLES = 96

DEFAULT_BASE_KW = 5.0


def _circular_hour_delta(a: float, b: float) -> float:
    """Signed distance from b to a in hours, wrapped to [-12, 12)."""
    return ((a - b + 12.0) % HOURS_PER_DAY) - 12.0


# ----------------------------------------------------------------------
# Daily shape
# ----------------------------------------------------------------------

class DailyProfile(ABC):
    """
    A 24-hour shape, as a multiplier on the base load.

    `normalize` rescales the shape so its daily mean is exactly 1.0, which is
    what keeps `base_kw` meaning "average load" whatever shape you give it.
    """

    def __init__(self, normalize: bool = True):
        self._scale = 1.0

        if normalize:
            mean = sum(
                self._raw_factor_at(i * HOURS_PER_DAY / _NORMALISE_SAMPLES)
                for i in range(_NORMALISE_SAMPLES)
            ) / _NORMALISE_SAMPLES

            if mean <= 0.0:
                raise ValueError(
                    "Daily profile cannot be normalised: its mean is not positive"
                )

            self._scale = 1.0 / mean

    @abstractmethod
    def _raw_factor_at(self, hour_of_day: float) -> float:
        ...

    def factor_at(self, hour_of_day: float) -> float:
        return self._scale * self._raw_factor_at(hour_of_day % HOURS_PER_DAY)

    def daily_mean(self) -> float:
        return sum(
            self.factor_at(i * HOURS_PER_DAY / _NORMALISE_SAMPLES)
            for i in range(_NORMALISE_SAMPLES)
        ) / _NORMALISE_SAMPLES


class FlatProfile(DailyProfile):
    """No shape at all — the pre-0.2 behaviour."""

    def __init__(self):
        super().__init__(normalize=False)

    def _raw_factor_at(self, hour_of_day: float) -> float:
        return 1.0


class TabulatedProfile(DailyProfile):
    """
    A shape given as (hour, factor) points, interpolated circularly.

    Points may be at any resolution and in any order. Interpolation wraps
    across midnight, so a profile never has a discontinuity there.
    """

    def __init__(
        self,
        points: Sequence[Tuple[float, float]],
        interpolate: bool = True,
        normalize: bool = True,
    ):
        if not points:
            raise ValueError("A tabulated profile needs at least one point")

        ordered = sorted((float(h) % HOURS_PER_DAY, float(v)) for h, v in points)

        self._hours = [h for h, _ in ordered]
        self._values = [v for _, v in ordered]
        self._interpolate = interpolate

        super().__init__(normalize=normalize)

    def _raw_factor_at(self, hour_of_day: float) -> float:
        hours = self._hours
        values = self._values
        count = len(hours)

        if count == 1:
            return values[0]

        # Index of the last point at or before this hour, or -1 to wrap back
        # to the final point of the previous day.
        index = -1
        for i, h in enumerate(hours):
            if h <= hour_of_day:
                index = i
            else:
                break

        if index < 0:
            # Before the first point: the bracket opens on the last point of
            # the previous day.
            left_hour = hours[-1] - HOURS_PER_DAY
            left_value = values[-1]
            right_hour = hours[0]
            right_value = values[0]
        elif index == count - 1:
            # After the last point: it closes on the first point of the next.
            left_hour = hours[-1]
            left_value = values[-1]
            right_hour = hours[0] + HOURS_PER_DAY
            right_value = values[0]
        else:
            left_hour = hours[index]
            left_value = values[index]
            right_hour = hours[index + 1]
            right_value = values[index + 1]

        if not self._interpolate:
            # Nearest point, not the one before: with hourly figures placed at
            # their hour centres, nearest is what makes hour 8's value apply
            # across the whole of hour 8.
            return (
                left_value
                if (hour_of_day - left_hour) < (right_hour - hour_of_day)
                else right_value
            )

        span = right_hour - left_hour
        if span <= 0.0:
            return right_value

        weight = (hour_of_day - left_hour) / span

        return left_value + (right_value - left_value) * weight


class HourlyProfile(TabulatedProfile):
    """
    24 factors, one per hour, each taken to apply at the centre of its hour.

    Hourly figures are averages over the hour, so placing them at the centre
    and interpolating is what reproduces the intended shape; placing them on
    the hour would shift the whole curve half an hour early.
    """

    def __init__(
        self,
        factors: Sequence[float],
        interpolate: bool = True,
        normalize: bool = True,
    ):
        if len(factors) != 24:
            raise ValueError(
                f"An hourly profile needs exactly 24 factors, got {len(factors)}"
            )

        super().__init__(
            [(hour + 0.5, factor) for hour, factor in enumerate(factors)],
            interpolate=interpolate,
            normalize=normalize,
        )


class ParametricProfile(DailyProfile):
    """
    A night baseline with a morning and an evening bump.

    Two Gaussians on a circular clock — enough to look like a real demand curve
    without asking anyone to type 24 numbers. `peak_width_h` is the full width
    at half maximum of each bump.
    """

    def __init__(
        self,
        night_factor: float = 0.55,
        morning_peak_hour: float = 8.0,
        morning_peak_factor: float = 1.05,
        evening_peak_hour: float = 18.0,
        evening_peak_factor: float = 1.35,
        peak_width_h: float = 3.0,
        normalize: bool = True,
    ):
        if night_factor < 0.0:
            raise ValueError(f"night_factor must be non-negative, got {night_factor}")
        if peak_width_h <= 0.0:
            raise ValueError(f"peak_width_h must be positive, got {peak_width_h}")

        self.night_factor = night_factor
        self.morning_peak_hour = morning_peak_hour
        self.morning_peak_factor = morning_peak_factor
        self.evening_peak_hour = evening_peak_hour
        self.evening_peak_factor = evening_peak_factor
        self.peak_width_h = peak_width_h

        # FWHM -> Gaussian sigma
        self._sigma_h = peak_width_h / (2.0 * math.sqrt(2.0 * math.log(2.0)))

        super().__init__(normalize=normalize)

    def _bump(self, hour: float, peak_hour: float, peak_factor: float) -> float:
        delta = _circular_hour_delta(hour, peak_hour)
        amplitude = peak_factor - self.night_factor

        return amplitude * math.exp(-0.5 * (delta / self._sigma_h) ** 2)

    def _raw_factor_at(self, hour_of_day: float) -> float:
        return max(
            0.0,
            self.night_factor
            + self._bump(
                hour_of_day, self.morning_peak_hour, self.morning_peak_factor
            )
            + self._bump(
                hour_of_day, self.evening_peak_hour, self.evening_peak_factor
            ),
        )


def load_profile_csv(
    path: Path,
    value_column: str = "factor",
    hour_column: str = "hour",
) -> List[Tuple[float, float]]:
    """
    Read an hour-of-day shape from a CSV.

    Two columns: the hour (0–24, any resolution) and a value. Absolute kW and
    dimensionless factors are both fine — the caller normalises.
    """

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Load profile CSV not found: {path}")

    points: List[Tuple[float, float]] = []

    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)

        if reader.fieldnames is None:
            raise ValueError(f"{path}: CSV has no header row")

        for column in (hour_column, value_column):
            if column not in reader.fieldnames:
                raise ValueError(
                    f"{path}: missing column '{column}'. "
                    f"Found: {', '.join(reader.fieldnames)}"
                )

        for line_no, row in enumerate(reader, start=2):
            raw_hour = (row.get(hour_column) or "").strip()
            raw_value = (row.get(value_column) or "").strip()

            if not raw_hour or not raw_value:
                continue

            try:
                points.append((float(raw_hour), float(raw_value)))
            except ValueError as e:
                raise ValueError(f"{path}:{line_no}: {e}") from e

    if not points:
        raise ValueError(f"{path}: no usable rows")

    logger.info("Loaded %d load profile points from %s", len(points), path)

    return points


# ----------------------------------------------------------------------
# The load model
# ----------------------------------------------------------------------

class SiteLoadModel:
    """
    Site load in kW, always positive.

    Advanced once per tick by `ExternalModels`, then read by the site power
    balance. Because it is advanced from the engine's own simulation time, its
    daily profile is in phase with the irradiance and ambient temperature
    models, which a supplier keyed off the power model's private clock was not.
    """

    def __init__(
        self,
        base_kw: float = DEFAULT_BASE_KW,
        profile: Optional[DailyProfile] = None,
        weekday_factor: float = 1.0,
        weekend_factor: float = 1.0,
        start_weekday: int = 0,
        drift_sigma: float = 0.0,
        drift_tau_s: float = 10800.0,
        drift_min_factor: float = 0.7,
        drift_max_factor: float = 1.3,
        noise_sigma: float = 0.0,
        noise_tau_s: float = 30.0,
        min_kw: float = 0.0,
        max_kw: Optional[float] = None,
        seed: Optional[int] = None,
    ):
        if base_kw < 0.0:
            raise ValueError(f"base_kw must be non-negative, got {base_kw}")
        if weekday_factor < 0.0 or weekend_factor < 0.0:
            raise ValueError("weekday_factor and weekend_factor must be non-negative")
        if not 0 <= start_weekday <= 6:
            raise ValueError(
                f"start_weekday must be 0 (Monday) to 6 (Sunday), got {start_weekday}"
            )
        if drift_min_factor > drift_max_factor:
            raise ValueError(
                f"drift_min_factor ({drift_min_factor}) must not exceed "
                f"drift_max_factor ({drift_max_factor})"
            )
        if min_kw < 0.0:
            raise ValueError(f"min_kw must be non-negative, got {min_kw}")
        if max_kw is not None and max_kw < min_kw:
            raise ValueError(
                f"max_kw ({max_kw}) must not be below min_kw ({min_kw})"
            )

        self.base_kw = base_kw
        self.profile = profile or FlatProfile()
        self.weekday_factor = weekday_factor
        self.weekend_factor = weekend_factor
        self.start_weekday = start_weekday
        self.drift_min_factor = drift_min_factor
        self.drift_max_factor = drift_max_factor
        self.min_kw = min_kw
        self.max_kw = max_kw

        # One stream, so a single seed drives both random terms.
        self._rng = random.Random(seed)
        self._drift = OrnsteinUhlenbeck(
            sigma=drift_sigma, tau_s=drift_tau_s, rng=self._rng
        )
        self._noise = OrnsteinUhlenbeck(
            sigma=noise_sigma, tau_s=noise_tau_s, rng=self._rng
        )

        self._power_kw = 0.0
        self._recompute(0.0, None)

    # --------------------------------------------------

    def weekday_at(self, sim_time: float, utc_time: Optional[datetime] = None) -> int:
        """
        Day of the week, Monday = 0.

        With an absolute instant, the real calendar wins. Without one — the
        default, since the clock is a bare offset from the start of the run —
        the week is placed by `start_weekday` and advanced at each simulated
        midnight.
        """

        if utc_time is not None:
            return utc_time.weekday()

        return (self.start_weekday + int(sim_time // SECONDS_PER_DAY)) % 7

    def is_weekend(self, sim_time: float, utc_time: Optional[datetime] = None) -> bool:
        return self.weekday_at(sim_time, utc_time) >= 5

    def drift_factor(self) -> float:
        return max(
            self.drift_min_factor,
            min(self.drift_max_factor, 1.0 + self._drift.value),
        )

    def noise_factor(self) -> float:
        return max(0.0, 1.0 + self._noise.value)

    # --------------------------------------------------

    def _recompute(self, sim_time: float, utc_time: Optional[datetime]) -> None:
        hour = (sim_time / 3600.0) % HOURS_PER_DAY

        power_kw = (
            self.base_kw
            * self.profile.factor_at(hour)
            * (
                self.weekend_factor
                if self.is_weekend(sim_time, utc_time)
                else self.weekday_factor
            )
            * self.drift_factor()
            * self.noise_factor()
        )

        if self.max_kw is not None:
            power_kw = min(power_kw, self.max_kw)

        self._power_kw = max(self.min_kw, power_kw)

    def advance(
        self, sim_time: float, dt: float, utc_time: Optional[datetime] = None
    ) -> None:
        self._drift.advance(dt)
        self._noise.advance(dt)
        self._recompute(sim_time, utc_time)

    def get_power_kw(self) -> float:
        return self._power_kw


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------

def _parse_weekday(value) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Invalid start_weekday: {value!r}")

    if isinstance(value, int):
        if not 0 <= value <= 6:
            raise ValueError(
                f"start_weekday must be 0 (Monday) to 6 (Sunday), got {value}"
            )
        return value

    name = str(value).strip().lower()

    if name not in WEEKDAY_NAMES:
        raise ValueError(
            f"Unknown start_weekday '{value}'. "
            f"Must be one of {WEEKDAY_NAMES}, or 0-6 with Monday = 0."
        )

    return WEEKDAY_NAMES.index(name)


def build_daily_profile(
    config: Optional[dict], data_root: Optional[Path] = None
) -> Tuple[DailyProfile, Optional[float]]:
    """
    Build the daily shape.

    Returns the profile and, for a CSV of absolute kW, the mean of those values
    so the caller can adopt it as `base_kw`.
    """

    cfg = dict(config or {})
    model = cfg.get("model", PROFILE_FLAT)

    if model not in VALID_PROFILE_MODELS:
        raise ValueError(
            f"Unknown load profile model '{model}'. "
            f"Must be one of {VALID_PROFILE_MODELS}."
        )

    normalize = cfg.get("normalize", True)
    interpolate = cfg.get("interpolate", True)

    if model == PROFILE_FLAT:
        return FlatProfile(), None

    if model == PROFILE_HOURLY:
        factors = cfg.get("hourly")
        if not factors:
            raise ValueError("load profile model 'hourly' requires 'hourly'")
        return (
            HourlyProfile(factors, interpolate=interpolate, normalize=normalize),
            None,
        )

    if model == PROFILE_PARAMETRIC:
        parametric = dict(cfg.get("parametric") or {})
        return (
            ParametricProfile(normalize=normalize, **parametric),
            None,
        )

    # CSV
    csv_name = cfg.get("csv")
    if not csv_name:
        raise ValueError("load profile model 'csv' requires 'csv'")

    csv_path = Path(csv_name)
    if not csv_path.is_absolute():
        csv_path = Path(data_root or ".") / csv_path

    value_column = cfg.get("csv_column", "factor")
    points = load_profile_csv(
        csv_path,
        value_column=value_column,
        hour_column=cfg.get("csv_hour_column", "hour"),
    )

    mean_value = sum(value for _, value in points) / len(points)

    # A CSV of absolute kW is a shape plus a level: normalising it to a mean of
    # 1.0 keeps the multiplicative decomposition, and the level becomes the
    # default base_kw.
    implied_base_kw = mean_value if value_column == "kw" else None

    return (
        TabulatedProfile(points, interpolate=interpolate, normalize=normalize),
        implied_base_kw,
    )


def build_load_model(
    config: Optional[dict], data_root: Optional[Path] = None
) -> SiteLoadModel:
    """
    Build the site load model from the `external_models` config block.

    With no `load` block, falls back to the legacy `power.base_load_w` as a
    flat load — so a config written before any of this existed behaves exactly
    as it did.
    """

    external = dict(config or {})
    cfg = external.get("load")

    if cfg is None:
        legacy_w = (external.get("power") or {}).get("base_load_w")
        base_kw = (
            float(legacy_w) / 1000.0 if legacy_w is not None else DEFAULT_BASE_KW
        )
        return SiteLoadModel(base_kw=base_kw)

    cfg = dict(cfg)
    profile, implied_base_kw = build_daily_profile(cfg.get("profile"), data_root)

    base_kw = cfg.get("base_kw")
    if base_kw is None:
        if implied_base_kw is not None:
            base_kw = implied_base_kw
        else:
            legacy_w = (external.get("power") or {}).get("base_load_w")
            base_kw = (
                float(legacy_w) / 1000.0
                if legacy_w is not None
                else DEFAULT_BASE_KW
            )

    drift = dict(cfg.get("drift") or {})
    noise = dict(cfg.get("noise") or {})

    drift_enabled = drift.get("enabled", True) and bool(drift)
    noise_enabled = noise.get("enabled", True) and bool(noise)

    return SiteLoadModel(
        base_kw=float(base_kw),
        profile=profile,
        weekday_factor=cfg.get("weekday_factor", 1.0),
        weekend_factor=cfg.get("weekend_factor", 1.0),
        start_weekday=_parse_weekday(cfg.get("start_weekday", 0)),
        drift_sigma=drift.get("sigma", 0.0) if drift_enabled else 0.0,
        drift_tau_s=drift.get("tau_s", 10800.0),
        drift_min_factor=drift.get("min_factor", 0.7),
        drift_max_factor=drift.get("max_factor", 1.3),
        noise_sigma=noise.get("sigma", 0.0) if noise_enabled else 0.0,
        noise_tau_s=noise.get("tau_s", 30.0),
        min_kw=cfg.get("min_kw", 0.0),
        max_kw=cfg.get("max_kw"),
        seed=cfg.get("seed"),
    )
