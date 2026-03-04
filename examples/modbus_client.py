import yaml
from pathlib import Path
from pymodbus.client import AsyncModbusTcpClient


def decode_registers(registers, data_type: str, scale: float):
    if not registers:
        return None

    if data_type == "uint16":
        return registers[0] * scale

    if data_type == "int16":
        raw = registers[0]
        if raw >= 0x8000:
            raw -= 0x10000
        return raw * scale

    if data_type in ("uint32", "int32"):
        high, low = registers[0], registers[1]
        raw = (high << 16) | low
        if data_type == "int32" and raw & 0x80000000:
            raw -= 0x100000000
        return raw * scale

    return registers[0] * scale


def encode_value(value: float, data_type: str, scale: float):
    reg_value = int(value / scale)

    if data_type == "uint16":
        return [reg_value & 0xFFFF]

    if data_type == "int16":
        if reg_value < 0:
            reg_value = (1 << 16) + reg_value
        return [reg_value & 0xFFFF]

    if data_type in ("uint32", "int32"):
        if data_type == "int32" and reg_value < 0:
            reg_value = (1 << 32) + reg_value
        high = (reg_value >> 16) & 0xFFFF
        low = reg_value & 0xFFFF
        return [high, low]

    return [reg_value & 0xFFFF]


class SimpleModbusClient:
    def __init__(self, host, port, unit_id, register_map_path):
        self.client = AsyncModbusTcpClient(host=host, port=port)
        self.unit_id = unit_id
        self.registers = self._load_register_map(register_map_path)

    def _load_register_map(self, path: str):
        with open(Path(path), "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # Expect structure:
        # telemetry: [...]
        # commands: [...]
        return {
            "telemetry": [item for item in data.get("registers", []) if item.get("direction") == "read"],
            "commands": [item for item in data.get("registers", []) if item.get("direction") == "write"],
        }

    async def connect(self):
        await self.client.connect()

    async def read_by_name(self, name: str):
        entry = next(
            (r for r in self.registers["telemetry"] if r["name"] == name),
            None,
        )
        if not entry:
            raise ValueError(f"Telemetry register '{name}' not found")

        address = entry["address"]
        data_type = entry.get("type", "uint16")
        scale = float(entry.get("scale", 1.0))
        count = 2 if data_type in ("uint32", "int32") else 1

        result = await self.client.read_input_registers(
            address=address,
            count=count
        )

        if result.isError():
            return None

        return decode_registers(result.registers, data_type, scale)

    async def write_by_name(self, name: str, value: float):
        entry = next(
            (c for c in self.registers["commands"] if c["name"] == name),
            None,
        )
        if not entry:
            raise ValueError(f"Command register '{name}' not found")

        address = entry["address"]
        data_type = entry.get("type", "uint16")
        scale = float(entry.get("scale", 1.0))

        regs = encode_value(value, data_type, scale)

        await self.client.write_registers(
            address=address,
            values=regs
        )