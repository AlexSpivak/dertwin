import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional, Callable

from dertwin.core.clock import SimulationClock
from dertwin.core.engine import SimulationEngine
from dertwin.controllers.device_controller import DeviceController
from dertwin.core.registers import RegisterMap

from dertwin.devices.bess.simulator import BESSSimulator
from dertwin.devices.pv.simulator import PVSimulator
from dertwin.devices.energy_meter.simulator import EnergyMeterSimulator

from dertwin.devices.external.power_flow import SitePowerModel
from dertwin.devices.external.grid_frequency import GridFrequencyModel
from dertwin.devices.external.grid_voltage import GridVoltageModel
from dertwin.devices.external.external_models import ExternalModels

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

        # ======================================================
        # External models (optional except power flow)
        # ======================================================

        self.power_model: Optional[SitePowerModel] = None
        self.grid_frequency_model: Optional[GridFrequencyModel] = None
        self.grid_voltage_model: Optional[GridVoltageModel] = None

        # future optional models
        self.ambient_temperature_model = None
        self.irradiance_model = None

    # ==========================================================
    # BUILD SITE
    # ==========================================================

    def build(self) -> None:
        logger.info("Building site: %s", self.config.get("site_name", "unnamed"))

        devices_by_type: Dict[str, List] = {}
        devices: List = []

        # Create devices
        for asset in [a for a in self.config["assets"] if a["type"] != "energy_meter"]:

            device = self._create_device(asset)
            devices.append(device)
            devices_by_type.setdefault(asset["type"], []).append(device)

        # ------------------------------------------------------
        # Create SitePowerModel (world power balance)
        # ------------------------------------------------------

        bess_devices: List[BESSSimulator] = devices_by_type.get("bess", [])
        pv_devices: List[PVSimulator] = devices_by_type.get("inverter", [])

        # ------------------------------------------------------
        # Create world models
        # ------------------------------------------------------

        self.grid_frequency_model = GridFrequencyModel()
        self.grid_voltage_model = GridVoltageModel()

        # base load configurable later
        base_load_supplier: Callable[[float], float] = (
            lambda t: 5.0
        )

        self.power_model = SitePowerModel(
            base_load_supplier=base_load_supplier,
            pv_supplier=lambda: sum(
                p.get_telemetry().get("total_active_power", 0.0)
                for p in pv_devices
            ),
            bess_supplier=lambda: sum(
                b.get_telemetry().get("active_power", 0.0) * 1000.0
                for b in bess_devices
            ),
        )

        # ------------------------------------------------------
        # Create Energy Meters
        # ------------------------------------------------------

        for _ in [a for a in self.config["assets"] if a["type"] == "energy_meter"]:
            meter = EnergyMeterSimulator(
                power_model=self.power_model,
                grid_model=self.grid_frequency_model,
                grid_voltage_model=self.grid_voltage_model,
            )

            devices.append(meter)
            devices_by_type.setdefault("energy_meter", []).append(meter)

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

        # ------------------------------------------------------
        # Create ExternalModels container
        # ------------------------------------------------------

        external_models = ExternalModels(
            power_model=self.power_model,
            grid_frequency_model=self.grid_frequency_model,
            grid_voltage_model=self.grid_voltage_model,
        )

        self.engine = SimulationEngine(
            devices=self.controllers,
            clock=self.clock,
            external_models=external_models,
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
            return PVSimulator()

        raise ValueError(f"Unknown or unsupported asset type: {dtype}")
