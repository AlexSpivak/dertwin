"""
Live CSV feeds for the external weather models.

A feed sits between a CSV on disk and a model that wants a value for an
instant. One class does both jobs it is asked to do:

- read once at build time and never look at the file again — the original
  `series_csv` behaviour, and still the default;
- or own the path: re-read it on an interval, notice that an external fetcher
  has replaced it, hold its last row for a configured grace period, and say
  plainly when it has nothing usable for the instant being asked about.

Two decisions worth knowing about.

**Everything is keyed on simulated time.** The refresh interval and the
staleness grace period are both measured in simulated UTC instants, not
wall-clock seconds, for the same reason the cloud regime is (0.2.1): a run
that is not in real time, or one replayed from the same config, has to behave
the same way twice. In a real-time deployment — the case a live feed is
actually for — the two coincide.

**A read that might have been torn is thrown away, not used.** The file is
stat'ed before and after the read, and a snapshot whose signature moved while
we were reading it is dropped and retried on the next interval. A writer that
replaces the file with `os.replace` can never produce a torn read in the first
place, which is what the documented file contract asks for; the check is there
for the writer that appends in place anyway.
"""

import logging
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from dertwin.devices.external.timeseries import (
    INTERPOLATE_LINEAR,
    SeriesTable,
    read_series_table,
)

logger = logging.getLogger(__name__)


# Default grace period for a live feed whose config does not name one: one
# hour, which tolerates a fetcher that has missed a single hourly update.
DEFAULT_STALE_AFTER_S = 3600.0


class SeriesFeed:
    """
    A CSV column set, optionally re-read as it changes on disk.

    `refresh_s` of 0 — the default — is one-shot mode: the file is read once,
    in the constructor, and a bad or missing file is an error there, exactly as
    it has always been. A positive `refresh_s` is live mode, and it changes
    both halves of that: the file is re-read as the run goes on, and a file
    that is missing or unreadable at startup is a warning rather than an
    error, because the whole point of the fallback is that a run survives its
    fetcher not being up yet.

    `stale_after_s` is how long the last row keeps being served once simulated
    time has passed it. A fetcher writing hourly rows is always a little
    behind; without the hold, every instant between the last row and the next
    update would have no data. Past the grace period there is no sample, and
    the caller falls back.
    """

    def __init__(
        self,
        path: Optional[Path],
        primary: str,
        time_column: str = "timestamp",
        extra_columns: Iterable[str] = (),
        interpolate: str = INTERPOLATE_LINEAR,
        refresh_s: float = 0.0,
        stale_after_s: Optional[float] = None,
        max_gap_s: float = 0.0,
        table: Optional[SeriesTable] = None,
    ):
        if refresh_s < 0.0:
            raise ValueError(f"refresh_s must be non-negative, got {refresh_s}")
        if stale_after_s is not None and stale_after_s < 0.0:
            raise ValueError(
                f"stale_after_s must be non-negative, got {stale_after_s}"
            )

        self.path = Path(path) if path is not None else None
        self.primary = primary
        self.time_column = time_column
        self.extra_columns = tuple(extra_columns)
        self.interpolate = interpolate
        self.refresh_s = refresh_s
        self.stale_after_s = stale_after_s
        self.max_gap_s = max_gap_s

        self._table = table
        self._signature: Optional[Tuple[int, int, int, int]] = None
        self._next_refresh_t: Optional[float] = None
        self._unreadable_since: Optional[str] = None
        self.reload_count = 0

        if self._table is None:
            if self.path is None:
                raise ValueError("SeriesFeed needs either a path or a table")

            if self.is_live:
                self._reload(initial=True)

                if self._table is None:
                    logger.warning(
                        "%s: no usable series at startup; running on the "
                        "stochastic cloud model until the file appears",
                        self.path,
                    )
            else:
                # One-shot: a missing or malformed file is a configuration
                # error and has always been raised from here.
                self._table = self._read(skip_bad_rows=False)
                self._signature = self._stat()
                logger.info(
                    "Loaded %d rows of '%s' from %s",
                    self._table.rows, self.primary, self.path,
                )

    # --------------------------------------------------

    @classmethod
    def from_table(cls, table: SeriesTable, **kwargs) -> "SeriesFeed":
        """A feed over an already-read table, for direct construction."""
        return cls(path=None, primary=table.primary, table=table, **kwargs)

    @property
    def is_live(self) -> bool:
        return self.refresh_s > 0.0 and self.path is not None

    @property
    def name(self) -> str:
        return str(self.path) if self.path is not None else self.primary

    @property
    def has_data(self) -> bool:
        return self._table is not None

    @property
    def rows(self) -> int:
        return self._table.rows if self._table is not None else 0

    @property
    def end_utc(self) -> Optional[datetime]:
        return self._table.end_utc if self._table is not None else None

    # --------------------------------------------------

    def _stat(self) -> Optional[Tuple[int, int, int, int]]:
        """
        A signature that changes whenever the bytes behind the path could
        have. The inode is in it because a writer that replaces the file
        atomically may well land on the same size and the same mtime
        resolution, and a new inode is the one thing such a replacement
        cannot avoid.
        """

        try:
            st = os.stat(self.path)
        except OSError:
            return None

        return (st.st_mtime_ns, st.st_size, st.st_ino, st.st_dev)

    def _read(self, skip_bad_rows: bool) -> SeriesTable:
        return read_series_table(
            self.path,
            primary=self.primary,
            time_column=self.time_column,
            extra_columns=self.extra_columns,
            interpolate=self.interpolate,
            max_gap_s=self.max_gap_s,
            skip_bad_rows=skip_bad_rows,
        )

    def _note_unreadable(self, reason: str) -> None:
        # One line per failure streak, not one per attempt: a feed whose
        # fetcher has died for an hour should say so once.
        if self._unreadable_since == reason:
            return

        self._unreadable_since = reason
        logger.warning("%s: cannot read series (%s)", self.path, reason)

    def _reload(self, initial: bool = False) -> bool:
        before = self._stat()

        if before is None:
            self._note_unreadable("file is missing")
            return False

        if before == self._signature:
            return False

        try:
            table = self._read(skip_bad_rows=True)
        except (OSError, ValueError) as e:
            self._note_unreadable(str(e))
            return False

        if self._stat() != before:
            # The file moved while we were reading it. Whatever we have may be
            # half of two versions, so it is dropped; the next interval will
            # read the settled file.
            logger.debug("%s: changed during the read; retrying later", self.path)
            return False

        previous_rows = self.rows
        self._table = table
        self._signature = before
        self._unreadable_since = None

        if not initial:
            # Counts re-reads only. The constructor's first read is not one,
            # so a feed nobody has written to still reports zero.
            self.reload_count += 1

            logger.info(
                "%s: re-read %d rows (was %d), covering %s .. %s%s",
                self.path, table.rows, previous_rows,
                table.start_utc.isoformat(), table.end_utc.isoformat(),
                f", {table.skipped_rows} unusable row(s) skipped"
                if table.skipped_rows else "",
            )
        elif table.skipped_rows:
            logger.warning(
                "%s: %d unusable row(s) skipped", self.path, table.skipped_rows
            )

        return True

    # --------------------------------------------------

    def refresh(self, utc_time: datetime) -> bool:
        """
        Re-read if the interval has elapsed. True when new data was taken on.

        The file is only parsed when its signature has moved, so a feed
        pointed at a file nobody is writing costs one `stat` per interval.
        """

        if not self.is_live:
            return False

        t = utc_time.timestamp()

        if self._next_refresh_t is None:
            # The constructor has just read the file; start the clock rather
            # than reading it again on the first tick.
            self._next_refresh_t = t + self.refresh_s
            return False

        # A clock that jumped backwards would otherwise never refresh again.
        if t < self._next_refresh_t - self.refresh_s:
            self._next_refresh_t = t + self.refresh_s
            return False

        if t < self._next_refresh_t:
            return False

        self._next_refresh_t = t + self.refresh_s

        return self._reload()

    def sample(self, utc_time: datetime) -> Optional[Dict[str, float]]:
        """
        Every column's value at `utc_time`, or None when there is no usable
        data for that instant — no file, before the first row, inside a gap
        wider than `max_gap_s`, or past the last row by more than
        `stale_after_s`.
        """

        if self._table is None:
            return None

        end = self._table.end_utc

        if (
            self.stale_after_s is not None
            and utc_time > end
            and utc_time <= end + timedelta(seconds=self.stale_after_s)
        ):
            # Inside the grace period the last row is held, which is what lets
            # a feed that is a few minutes behind still count as current.
            return self._table.values_at(end)

        return self._table.values_at(utc_time)


# ----------------------------------------------------------------------
# Switching between a feed and a fallback model
# ----------------------------------------------------------------------

class SourceSwitch:
    """
    Which source a model is reporting from, with one log line per change.

    Logging the transitions rather than the state is what makes a long run
    readable: a run that never loses its feed says nothing at all, and one
    that flaps says exactly when and how often.
    """

    def __init__(self, name: str):
        self.name = name
        self.on_fallback = False
        self.transitions = 0

    def to_fallback(self, utc_time: datetime, reason: str) -> bool:
        if self.on_fallback:
            return False

        self.on_fallback = True
        self.transitions += 1

        logger.warning(
            "%s: series is stale at %s (%s); falling back to the stochastic "
            "cloud model",
            self.name, utc_time.isoformat(), reason,
        )

        return True

    def to_series(self, utc_time: datetime) -> bool:
        if not self.on_fallback:
            return False

        self.on_fallback = False
        self.transitions += 1

        logger.info(
            "%s: fresh series data at %s; leaving the stochastic cloud model",
            self.name, utc_time.isoformat(),
        )

        return True


class DecayingOffset:
    """
    A one-off offset that relaxes to zero with a time constant.

    Used to hand the sky over between the series and the stochastic model in
    the direction where the model cannot simply be seeded. Starting it at the
    difference between what was being reported and what the new source says
    makes the switch itself a step of zero, and decaying it at the cloud
    model's own correlation time means everything after the switch moves no
    faster than that model moves anyway.

    `tau_s` of 0 disables it, and the switch becomes a plain jump to the new
    source.
    """

    def __init__(self, tau_s: float):
        if tau_s < 0.0:
            raise ValueError(f"tau_s must be non-negative, got {tau_s}")

        self.tau_s = tau_s
        self._offset = 0.0
        self._started_t: Optional[float] = None

    @property
    def active(self) -> bool:
        return self._started_t is not None

    def start(self, offset: float, utc_time: datetime) -> None:
        if self.tau_s <= 0.0 or offset == 0.0:
            self.clear()
            return

        self._offset = offset
        self._started_t = utc_time.timestamp()

    def clear(self) -> None:
        self._offset = 0.0
        self._started_t = None

    def value(self, utc_time: datetime) -> float:
        if self._started_t is None:
            return 0.0

        elapsed = utc_time.timestamp() - self._started_t

        if elapsed < 0.0:
            return self._offset

        # Four time constants is 1.8% of the original offset — below anything
        # downstream can tell apart, and it stops the offset being carried,
        # and recomputed, for the rest of the run.
        if elapsed > 4.0 * self.tau_s:
            self.clear()
            return 0.0

        return self._offset * math.exp(-elapsed / self.tau_s)
