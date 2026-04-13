from typing import List, Dict
from dertwin.core.device import SimulatedDevice
from dertwin.core.registers import RegisterMap

from dertwin.protocol.modbus_helpers import (
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
        self._initialized = False

    def step(self, dt: float):
        commands = self.collect_commands()

        # First run: initialize baseline without applying
        if not self._initialized:
            # initiate commands if there are values already requested.
            if any(commands.values()):
                filtered_commands = {k: v for k, v in commands.items() if v != 0.0}
                applied = self.device.apply_commands(filtered_commands)
                self.write_protocol_commands(applied)
            self._last_commands = dict(commands)
            self.device.init_applied_commands(commands)
            self._initialized = True

        elif commands and commands != self._last_commands:
            applied = self.device.apply_commands(commands)
            self.write_protocol_commands(applied)
            self._last_commands = dict(commands)

        self.device.update(dt)

        telemetry = self.device.get_telemetry().to_dict()
        self.apply_telemetry(telemetry)

    def collect_commands(self) -> Dict[str, float]:
        merged: Dict[str, float] = {}

        for proto in self.protocols:
            # Pass full register_map — helpers extract .writes internally
            raw_cmds = collect_write_instructions(
                register_map=self.register_map,
                context=proto.context,
                unit_id=proto.unit_id,
            )
            # collect_write_instructions now returns internal_name → value directly
            merged.update(raw_cmds)

        return merged

    def apply_telemetry(self, telemetry: Dict[str, float]) -> None:
        for proto in self.protocols:
            write_telemetry_registers(
                context=proto.context,
                unit_id=proto.unit_id,
                telemetry=telemetry,
                register_map=self.register_map,
            )

    def write_protocol_commands(self, commands: Dict[str, float]) -> None:
        for proto in self.protocols:
            write_command_registers(
                context=proto.context,
                unit_id=proto.unit_id,
                commands=commands,
                register_map=self.register_map,
            )
