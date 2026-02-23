import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional

from dertwin.core.clock import SimulationClock
from dertwin.core.engine import SimulationEngine
from dertwin.controllers.device_controller import DeviceController
from dertwin.core.registers import RegisterMap
from dertwin.devices.bess.simulator import BESSSimulator
from dertwin.devices.inverter import InverterSimulator
from dertwin.devices.energy_meter import EnergyMeterSimulator
from dertwin.devices.grid_frequency import GridFrequencyModel
from dertwin.protocol.modbus import ModbusSimulator

logger = logging.getLogger(__name__)


class SiteController:
    """
    Config-driven site runtime orchestrator.
    Fully owns lifecycle of engine + protocols.
    """

    def __init__(self, config: Dict):
        self.config = config
        self.register_map_root = Path(config.get("register_map_root", "."))

        self.clock = SimulationClock(
            step=config.get("step", 0.1),
            real_time=config.get("real_time", True),
        )

        self.engine: Optional[SimulationEngine] = None
        self.controllers: List[DeviceController] = []
        self.protocols: List[ModbusSimulator] = []

        self._tasks: List[asyncio.Task] = []
        self._built = False
        self._running = False

    # ------------------------------------------------------------------
    # BUILD
    # ------------------------------------------------------------------

    def build(self) -> None:
        logger.info("Building site: %s", self.config.get("site_name", "unnamed"))

        devices_by_type: Dict[str, List] = {}
        devices: List = []

        # Create devices
        for asset in self.config["assets"]:
            device = self._create_device(asset)
            devices.append(device)
            devices_by_type.setdefault(asset["type"], []).append(device)

        # Cross-wire dependencies
        bess_devices = devices_by_type.get("bess", [])
        inverter_devices = devices_by_type.get("inverter", [])
        meter_devices = devices_by_type.get("energy_meter", [])

        if meter_devices:
            for meter in meter_devices:
                meter.pv_supplier = lambda inv=inverter_devices: sum(
                    i.active_power_w for i in inv
                )
                meter.bess_supplier = lambda b=bess_devices: sum(
                    bs.commanded_power_kw * 1000.0 for bs in b
                )

        # Create controllers + protocols
        for asset, device in zip(self.config["assets"], devices):

            for proto_cfg in asset.get("protocols", []):

                if proto_cfg["kind"] != "modbus_tcp":
                    raise ValueError(f"Unsupported protocol kind: {proto_cfg['kind']}")

                map_path = Path(proto_cfg["register_map"])
                if not map_path.is_absolute():
                    map_path = self.register_map_root / map_path

                register_map = RegisterMap.from_yaml(map_path)

                modbus = ModbusSimulator(
                    address=proto_cfg["ip"],
                    port=proto_cfg["port"],
                    unit_id=proto_cfg.get("unit_id", 1),
                )

                self.protocols.append(modbus)

                controller = DeviceController(
                    device=device,
                    protocols=[modbus],
                    register_map=register_map,
                )

                self.controllers.append(controller)

        # Engine
        self.engine = SimulationEngine(
            devices=self.controllers,
            clock=self.clock,
        )

        self._built = True

    async def start(self):
        if not self._built:
            raise RuntimeError("Site must be built before start()")

        if self._running:
            return

        logger.info("Starting site runtime")
        self._running = True

        # Start protocol servers
        for proto in self.protocols:
            task = asyncio.create_task(proto.run_server())
            self._tasks.append(task)

        # Only run engine loop in real-time mode
        if self.clock.real_time:
            engine_task = asyncio.create_task(self.engine.run())
            self._tasks.append(engine_task)

            try:
                await asyncio.gather(*self._tasks)
            except asyncio.CancelledError:
                logger.info("Site tasks cancelled")

    async def stop(self):
        if not self._running:
            return

        logger.info("Stopping site runtime")

        # Stop engine loop
        if self.engine:
            self.engine.stop()

        # Graceful protocol shutdown
        for proto in self.protocols:
            await proto.shutdown()

        # Cancel remaining tasks
        for task in self._tasks:
            task.cancel()

        await asyncio.gather(*self._tasks, return_exceptions=True)

        self._tasks.clear()
        self._running = False

    # ------------------------------------------------------------------

    def _create_device(self, asset_cfg: Dict):
        dtype = asset_cfg["type"]

        if dtype == "bess":
            return BESSSimulator()

        if dtype == "inverter":
            return InverterSimulator()

        if dtype == "energy_meter":
            return EnergyMeterSimulator(
                base_load_supplier=lambda t: 5.0,
                grid_frequency_model=GridFrequencyModel(),
            )

        raise ValueError(f"Unknown asset type: {dtype}")
