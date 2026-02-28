import logging
from typing import List, Optional

from dertwin.controllers.device_controller import DeviceController
from dertwin.core.clock import SimulationClock
from dertwin.devices.external.external_models import ExternalModels

logger = logging.getLogger(__name__)


class SimulationEngine:
    """
    Deterministic simulation engine.

    Execution order per tick:

        1. external_models.update()
        2. device_controller.step()
        3. clock.tick()
    """

    def __init__(
        self,
        devices: List[DeviceController],
        clock: SimulationClock,
        external_models: Optional[ExternalModels] = None,
    ):
        self.devices = devices
        self.clock = clock
        self.external_models = external_models
        self.sim_time = self.clock.time

        self._running = False
        self._tick_count = 0

    # ---------------------------------------------------------
    # REAL TIME LOOP
    # ---------------------------------------------------------

    async def run(self):

        if not self.clock.real_time:
            raise RuntimeError(
                "Engine.run() should not be used in deterministic mode"
            )

        logger.info(
            "Simulation engine started | step=%.3fs",
            self.clock.step,
        )

        self._running = True

        while self._running:
            await self.step_once()

    # ---------------------------------------------------------
    # SINGLE STEP (Deterministic Safe)
    # ---------------------------------------------------------

    async def step_once(self):

        dt = self.clock.step
        self.sim_time = self.clock.time

        # =====================================================
        # STEP EXTERNAL FIRST
        # =====================================================

        if self.external_models:
            self.external_models.update(self.sim_time, dt)

        # =====================================================
        # STEP DEVICES
        # =====================================================

        for device in self.devices:
            device.step(dt)

        # =====================================================
        # LOGGING
        # =====================================================

        self._tick_count += 1

        if self._tick_count % 100 == 0:
            logger.info(
                "Simulation tick | t=%.2fs | ticks=%d",
                self.sim_time,
                self._tick_count,
            )

        # =====================================================
        # ADVANCE CLOCK
        # =====================================================

        await self.clock.tick()

    def stop(self):
        self._running = False
