import asyncio
import logging
import warnings
from pathlib import Path
from typing import Dict, List, Optional

from dertwin.core.clock import SimulationClock
from dertwin.core.engine import SimulationEngine
from dertwin.controllers.device_controller import DeviceController
from dertwin.core.registers import RegisterMap
from dertwin.devices.bess.battery import BatteryLimits
from dertwin.devices.bess.simulator import BESSSimulator
from dertwin.devices.chp.simulator import CHPSimulator
from dertwin.devices.pv.simulator import PVSimulator
from dertwin.devices.energy_meter.simulator import EnergyMeterSimulator
from dertwin.devices.external.external_models import ExternalModels
from dertwin.protocol.modbus import ModbusTCPSimulator, ModbusRTUSimulator

logger = logging.getLogger(__name__)


DEFAULT_REGISTER_MAPS = {
    "bess": "bess_modbus.yaml",
    "inverter": "pv_inverter_modbus.yaml",
    "energy_meter": "energy_meter_modbus.yaml",
    "chp": "chp_modbus.yaml",
}


class SiteController:
    """
    Site runtime orchestrator with two configuration paths:

    Legacy ('protocols: [...]'):
        Full multi-protocol library API. An asset can declare any number of
        protocol bindings (TCP, RTU, mixed) per device. Used by tests and any
        consumer that needs dual-protocol exposure.

    Flat spec ({type, ip, port, unit_id, ...}):
        Single-TCP-endpoint shape. Used by the runtime MQTT add_asset path.
        One asset = one TCP server.

    build() handles both shapes. Runtime add_asset() only handles the flat
    shape (the runtime never needs multi-protocol).
    """

    def __init__(self, config: Dict):
        self.config = config
        self.register_map_root = Path(config.get("register_map_root", "."))

        self.clock = SimulationClock(
            step=config.get("step", 0.1),
            real_time=config.get("real_time", True),
        )

        self.engine: Optional[SimulationEngine] = None
        self.external_models: Optional[ExternalModels] = None

        # Two synchronized stores: list preserves config order, dict gives
        # O(1) lookup for runtime add/remove.
        self._controllers: List[DeviceController] = []
        self._controllers_by_id: Dict[str, DeviceController] = {}
        self._protocols: List = []
        self._protocols_by_id: Dict[str, list] = {}
        self._protocol_tasks: Dict[str, list] = {}

        self._devices_by_type: Dict[str, list] = {
            "bess": [], "inverter": [], "chp": [], "energy_meter": [],
        }
        self._engine_devices: List[DeviceController] = []

        self._lock = asyncio.Lock()
        self._engine_task: Optional[asyncio.Task] = None
        self._built = False
        self._running = False

    @property
    def controllers(self) -> List[DeviceController]:
        return self._controllers

    @property
    def protocols(self) -> List:
        return self._protocols

    # ==========================================================
    # BUILD
    # ==========================================================

    def build(self) -> None:
        logger.info("Building site: %s", self.config.get("site_name", "unnamed"))

        if self.config.get("external_models"):
            self.external_models = ExternalModels.from_config(
                self.config["external_models"]
            )
        else:
            self.external_models = ExternalModels.build_default()

        self.external_models.power_model = ExternalModels.build_power_model(
            self._devices_by_type,
            self.config.get("external_models"),
        )

        self.engine = SimulationEngine(
            devices=self._engine_devices,
            clock=self.clock,
            external_models=self.external_models,
        )

        start_time_h = self.config.get("start_time_h", 0.0)
        if start_time_h:
            self.clock.time = start_time_h * 3600.0

        self._built = True

        # Two-pass build: non-meter devices first (so the power model has
        # something to sum), then meters. Controllers/protocols always wired
        # in original config order.
        assets = self.config.get("assets", [])

        prebuilt_devices: Dict[int, object] = {}
        for i, asset_cfg in enumerate(assets):
            if asset_cfg["type"] == "energy_meter":
                continue
            device = self._create_device_from_cfg(asset_cfg)
            self._devices_by_type[asset_cfg["type"]].append(device)
            prebuilt_devices[i] = device

        for i, asset_cfg in enumerate(assets):
            if asset_cfg["type"] == "energy_meter":
                self._register_from_cfg(asset_cfg, device=None)
            else:
                self._register_from_cfg(asset_cfg, device=prebuilt_devices[i])

    def _create_device_from_cfg(self, asset_cfg: Dict):
        """Create a device using whichever fields the cfg exposes (legacy or flat)."""
        return self._create_device(asset_cfg)

    def _register_from_cfg(self, asset_cfg: Dict, device=None) -> None:
        """Register an asset declared via legacy or flat config.

        - Legacy: 'protocols: [...]' list → may have 0, 1, or many protocols,
          each possibly TCP or RTU.
        - Flat:   'ip'/'port' top-level → exactly one TCP protocol.
        """
        if "protocols" in asset_cfg:
            self._register_legacy(asset_cfg, device=device)
        else:
            spec = dict(asset_cfg)
            self._register_asset(spec, device=device)

    def _register_legacy(self, asset_cfg: Dict, device=None) -> None:
        """Legacy code path: arbitrary list of protocols per asset.

        One DeviceController per protocol, mirroring the pre-refactor behavior.
        Asset ID is auto-derived from type + sequence (no asset_id in legacy
        configs).
        """
        if device is None:
            device = self._create_device(asset_cfg)
            self._devices_by_type[asset_cfg["type"]].append(device)

        protocols_cfg = asset_cfg.get("protocols", [])

        # Empty protocols list: device-only (test convenience)
        if not protocols_cfg:
            asset_id = self._derive_asset_id(asset_cfg)
            controller = DeviceController(
                device=device, protocols=[], register_map=None,
            )
            self._controllers.append(controller)
            self._controllers_by_id[asset_id] = controller
            self._protocols_by_id[asset_id] = []
            self._protocol_tasks[asset_id] = []
            self._engine_devices.append(controller)
            logger.info("Asset %s (%s) registered device-only",
                        asset_id, asset_cfg["type"])
            return

        for proto_cfg in protocols_cfg:
            asset_id = self._derive_asset_id(asset_cfg)

            reg_map_name = proto_cfg.get("register_map") or DEFAULT_REGISTER_MAPS[asset_cfg["type"]]
            map_path = Path(reg_map_name)
            if not map_path.is_absolute():
                map_path = self.register_map_root / map_path
            register_map = RegisterMap.from_yaml(map_path)

            protocol = self._create_protocol(proto_cfg)
            controller = DeviceController(
                device=device, protocols=[protocol], register_map=register_map,
            )

            self._controllers.append(controller)
            self._controllers_by_id[asset_id] = controller
            self._protocols.append(protocol)
            self._protocols_by_id[asset_id] = [protocol]
            self._protocol_tasks[asset_id] = []
            self._engine_devices.append(controller)

            logger.info(
                "Asset %s (%s) registered via %s",
                asset_id, asset_cfg["type"], proto_cfg["kind"],
            )

    @staticmethod
    def _create_protocol(proto_cfg: Dict):
        kind = proto_cfg["kind"]

        if kind == "modbus_tcp":
            return ModbusTCPSimulator(
                address=proto_cfg.get("ip", "0.0.0.0"),
                port=proto_cfg["port"],
                unit_id=proto_cfg.get("unit_id", 1),
            )

        if kind == "modbus_rtu":
            return ModbusRTUSimulator(
                port=proto_cfg["port"],
                unit_id=proto_cfg.get("unit_id", 1),
                baudrate=proto_cfg.get("baudrate", 9600),
                bytesize=proto_cfg.get("bytesize", 8),
                parity=proto_cfg.get("parity", "N"),
                stopbits=proto_cfg.get("stopbits", 1),
                timeout=proto_cfg.get("timeout", 1.0),
            )

        raise ValueError(f"Unsupported protocol kind: {kind}")

    # ==========================================================
    # RUNTIME ASSET MANAGEMENT (flat spec only)
    # ==========================================================

    async def add_asset(self, spec: Dict) -> None:
        """Register a new device at runtime via the flat spec shape:

        {"asset_id", "type", "ip", "port", "unit_id", ...}

        The legacy `protocols: [{...}]` wrapper is NOT supported here — runtime
        registration is single-TCP-endpoint only. Use the config-driven build()
        path for multi-protocol or RTU assets.

        Idempotent: re-registering an existing asset_id is a complete no-op.
        """
        spec = dict(spec)
        async with self._lock:
            asset_id = spec.get("asset_id") or self._derive_asset_id(spec)
            # Snapshot membership BEFORE register so we know if this call
            # actually creates a new asset or hits the already-registered guard.
            was_new = asset_id not in self._controllers_by_id

            self._register_asset(spec, device=None)

            # Only start a protocol server task if this was a NEW registration.
            # Re-publishes (broker fan-out, manual mosquitto_pub, retained replays)
            # must not spawn duplicate server tasks bound to the same port.
            if self._running and was_new:
                for proto in self._protocols_by_id.get(asset_id, []):
                    task = asyncio.create_task(proto.run_server())
                    self._protocol_tasks[asset_id].append(task)

    def _derive_asset_id(self, spec: Dict) -> str:
        return f"{spec['type']}-{len(self._controllers)}"

    def _register_asset(self, spec: Dict, device=None) -> None:
        """Flat-spec asset registration: exactly one TCP protocol (or none)."""
        asset_id = spec.get("asset_id") or self._derive_asset_id(spec)
        spec["asset_id"] = asset_id

        if asset_id in self._controllers_by_id:
            logger.info("Asset %s already registered, skipping", asset_id)
            return

        if device is None:
            device = self._create_device(spec)
            self._devices_by_type[spec["type"]].append(device)

        # Device-only mode (no endpoint)
        if "port" not in spec:
            controller = DeviceController(
                device=device, protocols=[], register_map=None,
            )
            self._controllers.append(controller)
            self._controllers_by_id[asset_id] = controller
            self._protocols_by_id[asset_id] = []
            self._protocol_tasks[asset_id] = []
            self._engine_devices.append(controller)
            logger.info("Asset %s (%s) registered device-only",
                        asset_id, spec["type"])
            return

        reg_map_name = spec.get("register_map") or DEFAULT_REGISTER_MAPS[spec["type"]]
        map_path = Path(reg_map_name)
        if not map_path.is_absolute():
            map_path = self.register_map_root / map_path
        register_map = RegisterMap.from_yaml(map_path)

        protocol = ModbusTCPSimulator(
            address=spec.get("ip", "0.0.0.0"),
            port=spec["port"],
            unit_id=spec.get("unit_id", 1),
        )
        controller = DeviceController(
            device=device, protocols=[protocol], register_map=register_map,
        )

        self._controllers.append(controller)
        self._controllers_by_id[asset_id] = controller
        self._protocols.append(protocol)
        self._protocols_by_id[asset_id] = [protocol]
        self._protocol_tasks[asset_id] = []
        self._engine_devices.append(controller)

        logger.info("Asset %s (%s) registered on %s:%d",
                    asset_id, spec["type"], spec.get("ip"), spec["port"])

    async def remove_asset(self, asset_id: str) -> None:
        async with self._lock:
            if asset_id not in self._controllers_by_id:
                logger.info("Asset %s not registered, skipping remove", asset_id)
                return

            tasks = self._protocol_tasks.pop(asset_id, [])
            for task in tasks:
                task.cancel()

            protocols = self._protocols_by_id.pop(asset_id, [])
            for proto in protocols:
                try:
                    await proto.shutdown()
                except Exception:
                    logger.exception("Error shutting down protocol for %s", asset_id)
                if proto in self._protocols:
                    self._protocols.remove(proto)

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            controller = self._controllers_by_id.pop(asset_id)
            if controller in self._controllers:
                self._controllers.remove(controller)
            if controller in self._engine_devices:
                self._engine_devices.remove(controller)

            for devices in self._devices_by_type.values():
                if controller.device in devices:
                    devices.remove(controller.device)
                    break

            logger.info("Asset %s removed", asset_id)

    # ==========================================================
    # LIFECYCLE
    # ==========================================================

    async def start(self):
        if not self._built:
            raise RuntimeError("Site must be built before start()")
        if self._running:
            return

        logger.info("Starting site runtime")
        self._running = True

        for asset_id, protocols in self._protocols_by_id.items():
            for proto in protocols:
                task = asyncio.create_task(proto.run_server())
                self._protocol_tasks[asset_id].append(task)

        if self.clock.real_time:
            self._engine_task = asyncio.create_task(self.engine.run())
            try:
                await self._engine_task
            except asyncio.CancelledError:
                logger.info("Engine task cancelled")

    async def stop(self):
        if not self._running:
            return

        logger.info("Stopping site runtime")

        if self.engine:
            self.engine.stop()

        for asset_id, protocols in self._protocols_by_id.items():
            for proto in protocols:
                try:
                    await proto.shutdown()
                except Exception:
                    logger.exception("Error shutting down protocol for %s", asset_id)

        all_tasks = [
            t for tasks in self._protocol_tasks.values() for t in tasks
        ]
        if self._engine_task:
            all_tasks.append(self._engine_task)
        for t in all_tasks:
            t.cancel()
        await asyncio.gather(*all_tasks, return_exceptions=True)

        self._running = False

    # ==========================================================
    # DEVICE FACTORY
    # ==========================================================

    def _create_device(self, spec: Dict):
        dtype = spec["type"]

        if dtype == "bess":
            soc_limits_cfg = spec.get("soc_limits") or {}
            limits = BatteryLimits(
                soc_lower_limit_1=soc_limits_cfg.get("lower_1", 25.0),
                soc_lower_limit_2=soc_limits_cfg.get("lower_2", 20.0),
                soc_upper_limit_1=soc_limits_cfg.get("upper_1", 85.0),
                soc_upper_limit_2=soc_limits_cfg.get("upper_2", 90.0),
            )
            return BESSSimulator(
                capacity_kwh=spec.get("capacity_kwh", 100.0),
                initial_soc=spec.get("initial_soc", 50.0),
                max_charge_kw=spec.get("max_charge_kw", 20.0),
                max_discharge_kw=spec.get("max_discharge_kw", 20.0),
                ramp_rate_kw_per_s=spec.get("ramp_rate_kw_per_s", 100.0),
                ambient_temp_c=spec.get("ambient_temp_c", 20.0),
                round_trip_eff=spec.get("round_trip_eff", 0.92),
                internal_resistance=spec.get("internal_resistance"),
                thermal_capacity_j_per_k=spec.get("thermal_capacity_j_per_k"),
                thermal_conductance_w_per_k=spec.get("thermal_conductance_w_per_k"),
                limits=limits,
                ambient_temp_model=self.external_models.ambient_temperature_model,
                grid_voltage_model=self.external_models.grid_voltage_model,
                grid_frequency_model=self.external_models.grid_frequency_model,
            )

        if dtype == "inverter":
            return PVSimulator(
                rated_kw=spec.get("rated_kw", 10.0),
                module_efficiency=spec.get("module_efficiency", 0.20),
                area_m2=spec.get("area_m2"),
                ambient_temp_model=self.external_models.ambient_temperature_model,
                grid_voltage_model=self.external_models.grid_voltage_model,
                grid_frequency_model=self.external_models.grid_frequency_model,
                irradiance_model=self.external_models.irradiance_model,
            )

        if dtype == "chp":
            return CHPSimulator(
                rated_kw=spec.get("rated_kw", 4000.0),
                heat_to_power_ratio=spec.get("heat_to_power_ratio", 1.0),
                min_load_percent=spec.get("min_load_percent", 30.0),
                max_load_percent=spec.get("max_load_percent", 110.0),
                ambient_temp_model=self.external_models.ambient_temperature_model,
                grid_voltage_model=self.external_models.grid_voltage_model,
                grid_frequency_model=self.external_models.grid_frequency_model,
            )

        if dtype == "energy_meter":
            return EnergyMeterSimulator(
                power_model=self.external_models.power_model,
                grid_model=self.external_models.grid_frequency_model,
                grid_voltage_model=self.external_models.grid_voltage_model,
            )

        raise ValueError(f"Unknown asset type: {dtype}")