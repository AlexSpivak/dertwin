import logging
from typing import List

from dertwin.controllers.device_controller import DeviceController
from dertwin.core.clock import SimulationClock

logger = logging.getLogger(__name__)


class SimulationEngine:
    def __init__(self, devices: List[DeviceController], clock: SimulationClock):
        self.devices = devices
        self.clock = clock
        self._running = False
        self._tick_count = 0

    async def run(self):
        logger.info(
            "Simulation engine started | step=%.3fs",
            self.clock.step,
        )

        self._running = True
        dt = self.clock.step

        while self._running:
            for device in self.devices:
                device.step(dt)

            self._tick_count += 1

            if self._tick_count % 100 == 0:
                logger.info(
                    "Simulation tick | t=%.2fs | ticks=%d",
                    self.clock.time,
                    self._tick_count,
                )

            await self.clock.tick()

        logger.info("Simulation engine stopped")

    def stop(self):
        self._running = False
