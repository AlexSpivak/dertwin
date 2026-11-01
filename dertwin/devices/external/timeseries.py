"""
CSV time series, for driving an external model from recorded data.

Standard library only. Used by the irradiance override (`series_csv`) and
available to any other external model that wants a measured input instead of a
synthetic one.
"""

import bisect
import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from dertwin.core.clock import parse_utc

logger = logging.getLogger(__name__)


INTERPOLATE_LINEAR = "linear"
INTERPOLATE_STEP = "step"
VALID_INTERPOLATIONS = (INTERPOLATE_LINEAR, INTERPOLATE_STEP)


class TimeSeries:
    """
    A sorted series of (UTC instant, value) samples.

    `value_at` returns None outside the covered range, so each caller decides
    what that means — zero for irradiance, hold-last for a load profile.
    """

    def __init__(
        self,
        samples: List[Tuple[datetime, float]],
        interpolate: str = INTERPOLATE_LINEAR,
        name: str = "series",
    ):
        if interpolate not in VALID_INTERPOLATIONS:
            raise ValueError(
                f"Unknown interpolation '{interpolate}'. "
                f"Must be one of {VALID_INTERPOLATIONS}."
            )
        if not samples:
            raise ValueError(f"{name}: time series is empty")

        ordered = sorted(samples, key=lambda item: item[0])

        self.name = name
        self.interpolate = interpolate
        self._times = [t.timestamp() for t, _ in ordered]
        self._values = [v for _, v in ordered]
        self._warned_out_of_range = False

    # --------------------------------------------------

    @property
    def start_utc(self) -> datetime:
        return datetime.fromtimestamp(self._times[0], tz=timezone.utc)

    @property
    def end_utc(self) -> datetime:
        return datetime.fromtimestamp(self._times[-1], tz=timezone.utc)

    def __len__(self) -> int:
        return len(self._times)

    # --------------------------------------------------

    @classmethod
    def from_csv(
        cls,
        path: Path,
        value_column: str,
        time_column: str = "timestamp",
        interpolate: str = INTERPOLATE_LINEAR,
    ) -> "TimeSeries":
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f"Time series CSV not found: {path}")

        samples: List[Tuple[datetime, float]] = []

        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)

            if reader.fieldnames is None:
                raise ValueError(f"{path}: CSV has no header row")

            for column in (time_column, value_column):
                if column not in reader.fieldnames:
                    raise ValueError(
                        f"{path}: missing column '{column}'. "
                        f"Found: {', '.join(reader.fieldnames)}"
                    )

            for line_no, row in enumerate(reader, start=2):
                raw_time = (row.get(time_column) or "").strip()
                raw_value = (row.get(value_column) or "").strip()

                if not raw_time or not raw_value:
                    continue

                try:
                    samples.append((parse_utc(raw_time), float(raw_value)))
                except ValueError as e:
                    raise ValueError(f"{path}:{line_no}: {e}") from e

        if not samples:
            raise ValueError(f"{path}: no usable rows")

        logger.info(
            "Loaded %d samples of '%s' from %s", len(samples), value_column, path
        )

        return cls(samples, interpolate=interpolate, name=str(path))

    # --------------------------------------------------

    def value_at(self, utc_time: datetime) -> Optional[float]:
        t = utc_time.timestamp()

        if t < self._times[0] or t > self._times[-1]:
            if not self._warned_out_of_range:
                self._warned_out_of_range = True
                logger.warning(
                    "%s: %s is outside the series range %s .. %s; "
                    "callers will see no value",
                    self.name, utc_time.isoformat(),
                    self.start_utc.isoformat(), self.end_utc.isoformat(),
                )
            return None

        index = bisect.bisect_right(self._times, t) - 1
        index = max(0, min(index, len(self._times) - 1))

        if self.interpolate == INTERPOLATE_STEP or index == len(self._times) - 1:
            return self._values[index]

        t0, t1 = self._times[index], self._times[index + 1]
        v0, v1 = self._values[index], self._values[index + 1]

        if t1 == t0:
            return v1

        return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
