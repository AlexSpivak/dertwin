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

        self.external_models: Optional[ExternalModels] = None

    # ==========================================================
    # BUILD SITE
    # ==========================================================

    def build(self) -> None:
        logger.info("Building site: %s", self.config.get("site_name", "unnamed"))


        if self.config.get("external_models"):
            self.external_models = ExternalModels.from_config(self.config.get("external_models"))
        else:
            self.external_models = ExternalModels.build_default()

        devices_by_type: Dict[str, List] = {}
        devices: List = []

        # Create devices
        for asset in [a for a in self.config["assets"] if a["type"] != "energy_meter"]:
            device = self._create_device(asset)
            devices.append(device)
            devices_by_type.setdefault(asset["type"], []).append(device)

        self.external_models.power_model = ExternalModels.build_power_model(devices_by_type, self.config.get("power_model"))


        # ------------------------------------------------------
        # Create Energy Meters
        # ------------------------------------------------------

        for _ in [a for a in self.config["assets"] if a["type"] == "energy_meter"]:
            meter = EnergyMeterSimulator(
                power_model=self.external_models.power_model,
                grid_model=self.external_models.grid_frequency_model,
                grid_voltage_model=self.external_models.grid_voltage_model,
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

        self.engine = SimulationEngine(
            devices=self.controllers,
            clock=self.clock,
            external_models=self.external_models,
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
            return BESSSimulator(
                capacity_kwh=asset_cfg.get("capacity_kwh", 100.0),
                initial_soc=asset_cfg.get("initial_soc", 50.0),
                max_charge_kw=asset_cfg.get("max_charge_kw", 20.0),
                max_discharge_kw=asset_cfg.get("max_discharge_kw", 20.0),
                ramp_rate_kw_per_s=asset_cfg.get("ramp_rate_kw_per_s", 100.0),
                ambient_temp_model=self.external_models.ambient_temperature_model,
                grid_voltage_model=self.external_models.grid_voltage_model,
                grid_frequency_model=self.external_models.grid_frequency_model,
            )

        if dtype == "inverter":
            return PVSimulator(
                rated_kw=asset_cfg.get("rated_kw", 10.0),
                module_efficiency=asset_cfg.get("module_efficiency", 0.20),
                area_m2=asset_cfg.get("area_m2", None),
                ambient_temp_model=self.external_models.ambient_temperature_model,
                grid_voltage_model=self.external_models.grid_voltage_model,
                grid_frequency_model=self.external_models.grid_frequency_model,
                irradiance_model=self.external_models.irradiance_model,
            )

        raise ValueError(f"Unknown or unsupported asset type: {dtype}")
