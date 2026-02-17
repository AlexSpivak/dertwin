import asyncio
from time import monotonic


class SimulationClock:
    """
    Supports:
    - Realtime mode (wall clock pacing)
    - Deterministic mode (no sleep, manual stepping)
    """

    def __init__(self, step: float = 0.1, real_time: bool = True):
        self.time = 0.0
        self.step = step
        self.real_time = real_time
        self._running = False
        self._last_wall_time = monotonic()

    async def tick(self):
        """
        Advance simulation time by one step.
        """

        self.time += self.step

        if self.real_time:
            target = self._last_wall_time + self.step
            now = monotonic()
            sleep_time = max(0.0, target - now)
            self._last_wall_time = target
            await asyncio.sleep(sleep_time)

    # ---------------------------------------------------------

    def reset(self):
        self.time = 0.0
        self._last_wall_time = monotonic()
