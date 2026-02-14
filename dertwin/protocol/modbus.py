import asyncio
from typing import List, Dict

from pymodbus.datastore import ModbusServerContext, ModbusSequentialDataBlock, ModbusDeviceContext
from pymodbus.server import StartAsyncTcpServer

from dertwin.devices.device import DeviceSimulator

def create_device_context() -> ModbusDeviceContext:
    di_block = ModbusSequentialDataBlock(0, [0] * 100)
    co_block = ModbusSequentialDataBlock(0, [0] * 100)
    ir_block = ModbusSequentialDataBlock(0, [0] * 40000)
    hr_block = ModbusSequentialDataBlock(0, [0] * 40000)

    return ModbusDeviceContext(
        di=di_block,
        co=co_block,
        ir=ir_block,
        hr=hr_block,
    )


def encode_value(value: float, data_type: str, scale: float, count: int) -> List[int]:
    reg_value = int(value / scale)

    if data_type == "uint16":
        return [max(0, min(0xFFFF, reg_value))]

    if data_type == "int16":
        if reg_value < 0:
            reg_value = (1 << 16) + reg_value
        return [reg_value & 0xFFFF]

    if data_type in ("uint32", "int32"):
        if data_type == "int32" and reg_value < 0:
            reg_value = (1 << 32) + reg_value
        reg_value &= 0xFFFFFFFF
        high = (reg_value >> 16) & 0xFFFF
        low = reg_value & 0xFFFF
        return [high, low]

    return [reg_value & 0xFFFF] * count

def write_telemetry_registers(configs, context, unit_id, telemetry):
    for entry in configs:
        name = entry["name"]
        if name not in telemetry:
            continue

        addr = entry["address"]
        scale = entry.get("scale", 1.0)
        dtype = entry.get("type", "uint16")
        count = entry.get("count", 1)

        values = encode_value(telemetry[name], dtype, scale, count)
        context[unit_id].setValues(4, addr, values)  # input registers


def write_command_registers(configs, context, unit_id, commands):
    for entry in configs:
        if entry.get("func") not in (0x06, 0x10):
            continue   # not writable

        name = entry["name"]
        if name not in commands:
            continue

        addr = entry["address"]
        scale = entry.get("scale", 1.0)
        dtype = entry.get("type", "uint16")
        count = entry.get("count", 1)

        values = encode_value(commands[name], dtype, scale, count)
        context[unit_id].setValues(3, addr, values)  # HR


def collect_write_instructions(
        configs: List[dict],
        context: ModbusServerContext,
        unit_id: int
) -> Dict[str, float]:
    """
    Iterates over configs for writable registers and returns dict {name: value}.
    Does NOT call device methods, just collects.
    """
    slave = context[unit_id]
    instructions = {}

    for entry in configs:
        addr = int(entry["address"])
        count = int(entry.get("count", 1))
        scale = float(entry.get("scale", 1.0))
        data_type = entry.get("type", "uint16")
        func = entry.get("func", 0x06)  # write holding register

        # Only consider writable registers (HR, CO)
        reg_type_code = 3 if func in (0x06, 0x10) else None
        if reg_type_code is None:
            continue

        try:
            raw_values = slave.getValues(reg_type_code, addr, count)
            raw_value = raw_values[0]

            # Decode signed
            if data_type == "int16" and raw_value > 0x7FFF:
                raw_value -= 1 << 16
            elif data_type == "int32" and count == 2:
                raw_value = (raw_values[0] << 16) + raw_values[1]
                if entry.get("type") == "int32" and raw_value > 0x7FFFFFFF:
                    raw_value -= 1 << 32

            instructions[entry["name"]] = raw_value * scale

        except Exception as e:
            print(f"[WARN] Could not read writable register {entry['name']} at {addr}: {e}")
            continue

    return instructions


class ModbusSimulator:
    def __init__(self, port: int, unit_id: int, configs: List[dict], device_sim: DeviceSimulator):
        self.port = port
        self.unit_id = unit_id
        self.configs = configs
        self.device_sim = device_sim
        device_context = create_device_context()

        self.context = ModbusServerContext(
            devices={unit_id: device_context},
            single=False
        )

    async def run_server(self):
        asyncio.create_task(self.update_loop())
        print(f"[ModbusSimulator] Started device on port {self.port}, unit {self.unit_id}")
        await StartAsyncTcpServer(context=self.context, address=("0.0.0.0", self.port))

    async def update_loop(self):
        # collect empty instructions at the beginning of the loop to avoid overwriting preset metrics
        prev_instructions = collect_write_instructions(self.configs, self.context, self.unit_id)
        self.device_sim.init_applied_commands(prev_instructions)
        interval = 0.1 # simulation interval use it as minimal for fast responsiveness
        while True:
            write_instructions = collect_write_instructions(self.configs, self.context, self.unit_id)
            if write_instructions and write_instructions != prev_instructions:
                applied = self.device_sim.execute_write_instructions(write_instructions)
                write_command_registers(self.configs, self.context, self.unit_id, applied)
                print(f"Applied: {applied}")
                prev_instructions = write_instructions

            vals = self.device_sim.simulate_values(interval)
            write_telemetry_registers(self.configs, self.context, self.unit_id, vals)
            await asyncio.sleep(interval)
