"""
CSV time series, for driving an external model from recorded data.

Standard library only. Used by the irradiance override (`series_csv`) and
available to any other external model that wants a measured input instead of a
synthetic one.
"""

import bisect
import csv
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

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
        max_gap_s: float = 0.0,
    ):
        if interpolate not in VALID_INTERPOLATIONS:
            raise ValueError(
                f"Unknown interpolation '{interpolate}'. "
                f"Must be one of {VALID_INTERPOLATIONS}."
            )
        if not samples:
            raise ValueError(f"{name}: time series is empty")
        if max_gap_s < 0.0:
            raise ValueError(f"max_gap_s must be non-negative, got {max_gap_s}")

        # Stable sort, so for two rows carrying the same timestamp the one
        # later in the file ends up last — and `value_at` reads the last of a
        # run of duplicates. An appended correction therefore wins over the
        # row it corrects, which is the only useful rule for a file an
        # external fetcher keeps rewriting.
        ordered = sorted(samples, key=lambda item: item[0])

        self.name = name
        self.interpolate = interpolate
        self.max_gap_s = max_gap_s
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

        # A gap wider than max_gap_s is missing data, not a straight line
        # between its ends: a fetcher that dropped six hours should hand the
        # caller nothing for those hours rather than a confident
        # interpolation. Exact samples always come back, however isolated.
        if (
            self.max_gap_s > 0.0
            and t != self._times[index]
            and index + 1 < len(self._times)
            and self._times[index + 1] - self._times[index] > self.max_gap_s
        ):
            return None

        if self.interpolate == INTERPOLATE_STEP or index == len(self._times) - 1:
            return self._values[index]

        t0, t1 = self._times[index], self._times[index + 1]
        v0, v1 = self._values[index], self._values[index + 1]

        if t1 == t0:
            return v1

        return v0 + (v1 - v0) * (t - t0) / (t1 - t0)


# ----------------------------------------------------------------------
# Several columns over one time axis
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class SeriesTable:
    """
    One CSV read, exposed as named columns sharing a time axis.

    Each column is its own `TimeSeries`, built from the rows where that cell
    actually held a number, so a file whose DNI column is only half filled in
    still yields a complete GHI series. `primary` is the column a caller
    cannot do without: an instant with no primary value has no sample at all,
    whatever the other columns say.
    """

    columns: Dict[str, TimeSeries]
    primary: str
    rows: int
    skipped_rows: int

    @property
    def start_utc(self) -> datetime:
        return self.columns[self.primary].start_utc

    @property
    def end_utc(self) -> datetime:
        return self.columns[self.primary].end_utc

    def values_at(self, utc_time: datetime) -> Optional[Dict[str, float]]:
        """Every column that has a value at `utc_time`, or None."""

        primary = self.columns[self.primary].value_at(utc_time)

        if primary is None:
            return None

        values = {self.primary: primary}

        for name, series in self.columns.items():
            if name == self.primary:
                continue
            value = series.value_at(utc_time)
            if value is not None:
                values[name] = value

        return values


def read_series_table(
    path: Path,
    primary: str,
    time_column: str = "timestamp",
    extra_columns: Iterable[str] = (),
    interpolate: str = INTERPOLATE_LINEAR,
    max_gap_s: float = 0.0,
    skip_bad_rows: bool = False,
) -> SeriesTable:
    """
    Read `primary` and whichever of `extra_columns` the header carries.

    A missing file, a missing header, a missing primary column and a file with
    no usable row are errors either way: a file in the wrong shape is no more
    usable for being readable. `skip_bad_rows` only relaxes *rows* — an
    unparseable timestamp or value is counted and dropped instead of ending
    the read. That is what a file being rewritten underneath a running
    simulation needs: one torn line must not take the sim with it.

    Columns named in `extra_columns` but absent from the header are simply not
    in the result, which is how an optional DNI/DHI column stays optional.
    """

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Time series CSV not found: {path}")

    samples: Dict[str, List[Tuple[datetime, float]]] = {}
    rows = 0
    skipped = 0

    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)

        if reader.fieldnames is None:
            raise ValueError(f"{path}: CSV has no header row")

        for column in (time_column, primary):
            if column not in reader.fieldnames:
                raise ValueError(
                    f"{path}: missing column '{column}'. "
                    f"Found: {', '.join(reader.fieldnames)}"
                )

        wanted = [primary] + [
            name
            for name in extra_columns
            if name and name != primary and name in reader.fieldnames
        ]

        for line_no, row in enumerate(reader, start=2):
            raw_time = (row.get(time_column) or "").strip()

            if not raw_time:
                continue

            try:
                when = parse_utc(raw_time)
            except ValueError as e:
                if not skip_bad_rows:
                    raise ValueError(f"{path}:{line_no}: {e}") from e
                skipped += 1
                continue

            kept = False

            for name in wanted:
                raw_value = (row.get(name) or "").strip()

                if not raw_value:
                    continue

                try:
                    value = float(raw_value)
                except ValueError as e:
                    if not skip_bad_rows:
                        raise ValueError(f"{path}:{line_no}: {e}") from e
                    skipped += 1
                    continue

                samples.setdefault(name, []).append((when, value))
                kept = True

            if kept:
                rows += 1

    if not samples.get(primary):
        raise ValueError(f"{path}: no usable rows")

    columns = {
        name: TimeSeries(
            values,
            interpolate=interpolate,
            name=f"{path}:{name}",
            max_gap_s=max_gap_s,
        )
        for name, values in samples.items()
    }

    return SeriesTable(
        columns=columns, primary=primary, rows=rows, skipped_rows=skipped
    )
