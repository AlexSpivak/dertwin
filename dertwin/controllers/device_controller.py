from typing import List, Dict
from dertwin.core.device import SimulatedDevice
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
        register_configs: List[dict],
    ):
        self.device = device
        self.protocols = protocols   # TODO refactor to abstract protocol class e.g. ModbusSimulator instance or other
        self.configs = register_configs

        self._last_commands: Dict[str, float] = {}

    def step(self, dt: float):
        commands = self.collect_commands()

        if commands and commands != self._last_commands:
            applied = self.device.apply_commands(commands)
            self.write_protocol_commands(applied)
            self._last_commands = commands
        self.device.update(dt)
        telemetry = self.device.get_telemetry()
        self.apply_telemetry(telemetry)

    def collect_commands(self) -> Dict[str, float]:
        merged: Dict[str, float] = {}

        for proto in self.protocols:
            cmds = collect_write_instructions(
                self.configs,
                proto.context,
                proto.unit_id
            )
            merged.update(cmds)

        return merged

    def apply_telemetry(self, telemetry: Dict[str, float]) -> None:
        for proto in self.protocols:
            write_telemetry_registers(
                self.configs,
                proto.context,
                proto.unit_id,
                telemetry
            )

    def write_protocol_commands(self, commands: Dict[str, float]) -> None:
        for proto in self.protocols:
            write_command_registers(
                self.configs,
                proto.context,
                proto.unit_id,
                commands
            )
