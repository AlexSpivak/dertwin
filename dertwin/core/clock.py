from time import monotonic
import asyncio


class SimulationClock:
    def __init__(self, step: float = 0.1, real_time: bool = True):
        self.time = 0.0
        self.step = step
        self.real_time = real_time
        self._last_wall_time = monotonic()

    async def tick(self):
        self.time += self.step

        if self.real_time:
            target = self._last_wall_time + self.step
            now = monotonic()
            sleep_time = max(0.0, target - now)
            self._last_wall_time = target
            await asyncio.sleep(sleep_time)
