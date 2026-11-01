import asyncio
from datetime import datetime, timedelta, timezone
from time import monotonic


# Simulation time is always UTC internally. With no sim_start_utc in the config
# the clock is anchored here, so start_time_h keeps meaning "hours past
# midnight" exactly as it did before absolute time existed.
DEFAULT_START_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)


def parse_utc(value) -> datetime:
    """
    Parse an ISO 8601 instant into an aware UTC datetime.

    Accepts a datetime or a string, with a trailing 'Z', an explicit offset, or
    no zone at all (taken as UTC).
    """

    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as e:
            raise ValueError(
                f"Invalid ISO 8601 UTC timestamp: {value!r} ({e})"
            ) from e

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


class SimulationClock:
    def __init__(
        self,
        step: float = 0.1,
        real_time: bool = True,
        start_utc: datetime | str | None = None,
    ):
        self.time = 0.0
        self.step = step
        self.real_time = real_time
        self.start_utc = (
            parse_utc(start_utc) if start_utc is not None else DEFAULT_START_UTC
        )
        self._last_wall_time = monotonic()

    def now_utc(self) -> datetime:
        """Absolute UTC instant for the current simulation time."""
        return self.start_utc + timedelta(seconds=self.time)

    async def tick(self):
        self.time += self.step

        if self.real_time:
            target = self._last_wall_time + self.step
            now = monotonic()
            sleep_time = max(0.0, target - now)
            self._last_wall_time = target
            await asyncio.sleep(sleep_time)

    def reset(self):
        self.time = 0.0
        self._last_wall_time = monotonic()
