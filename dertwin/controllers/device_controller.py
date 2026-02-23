from typing import List, Dict
from dertwin.core.device import SimulatedDevice
from dertwin.core.registers import RegisterMap
from dertwin.protocol.modbus import (
    collect_write_instructions,
    write_telemetry_registers,
    write_command_registers,
)

class DeviceController:

    def __init__(
        self,
        device: SimulatedDevice,
        protocols: List,
        register_map: RegisterMap,
    ):
        self.device = device
        self.protocols = protocols
        self.register_map = register_map

        self._last_commands: Dict[str, float] = {}

        self._last_commands: Dict[str, float] = {}
        self._initialized = False

    def step(self, dt: float):
        commands = self.collect_commands()

        # First run: initialize baseline without applying
        if not self._initialized:
            self._last_commands = dict(commands)
            self.device.init_applied_commands(commands)
            self._initialized = True

        elif commands and commands != self._last_commands:
            applied = self.device.apply_commands(commands)
            self.write_protocol_commands(applied)
            self._last_commands = dict(commands)

        self.device.update(dt)

        telemetry = self.device.get_telemetry()
        self.apply_telemetry(telemetry)

    def collect_commands(self) -> Dict[str, float]:
        merged: Dict[str, float] = {}

        for proto in self.protocols:
            raw_cmds = collect_write_instructions(
                self.register_map.writes,
                proto.context,
                proto.unit_id,
            )

            for vendor_name, value in raw_cmds.items():
                reg = self.register_map.get_by_name(vendor_name)

                internal_name = reg.internal_name or reg.name
                merged[internal_name] = value

        return merged

    def apply_telemetry(self, telemetry: Dict[str, float]) -> None:
        for proto in self.protocols:
            write_telemetry_registers(
                self.register_map.reads,
                proto.context,
                proto.unit_id,
                telemetry,
            )

    def write_protocol_commands(self, commands: Dict[str, float]) -> None:
        for proto in self.protocols:
            write_command_registers(
                self.register_map.writes,
                proto.context,
                proto.unit_id,
                commands,
            )
