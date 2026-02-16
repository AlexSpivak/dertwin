import logging
from typing import List, Dict

from pymodbus.datastore import ModbusServerContext, ModbusSequentialDataBlock, ModbusDeviceContext
from pymodbus.server import StartAsyncTcpServer

from dertwin.core.registers import RegisterDefinition, RegisterDirection

logger = logging.getLogger(__name__)

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


def write_telemetry_registers(
    registers: List[RegisterDefinition],
    context,
    unit_id: int,
    telemetry: Dict[str, float],
):
    for reg in registers:
        if reg.direction != RegisterDirection.READ:
            continue

        if reg.name not in telemetry:
            continue

        values = encode_value(
            value=telemetry[reg.name],
            data_type=reg.type,
            scale=reg.scale,
            count=reg.count,
        )

        context[unit_id].setValues(4, reg.address, values)

        logger.debug(
            "Telemetry written | unit=%s | register=%s | values=%s",
            unit_id,
            reg.address,
            values,
        )

def write_command_registers(
    registers: List[RegisterDefinition],
    context,
    unit_id: int,
    commands: Dict[str, float],
):
    for reg in registers:

        if reg.direction != RegisterDirection.WRITE:
            continue

        if reg.name not in commands:
            continue

        values = encode_value(
            value=commands[reg.name],
            data_type=reg.type,
            scale=reg.scale,
            count=reg.count,
        )

        # 3 = Holding Registers
        context[unit_id].setValues(3, reg.address, values)

        logger.debug(
            "Command register written | unit=%s | register=%s | values=%s",
            unit_id,
            reg.address,
            values,
        )

def collect_write_instructions(
    registers: List[RegisterDefinition],
    context: ModbusServerContext,
    unit_id: int,
) -> Dict[str, float]:

    slave = context[unit_id]
    instructions: Dict[str, float] = {}

    for reg in registers:

        if reg.direction != RegisterDirection.WRITE:
            continue

        try:
            raw_values = slave.getValues(3, reg.address, reg.count)

            if reg.count == 1:
                raw_value = raw_values[0]

                if reg.type == "int16" and raw_value > 0x7FFF:
                    raw_value -= 1 << 16

            elif reg.count == 2:
                raw_value = (raw_values[0] << 16) + raw_values[1]

                if reg.type == "int32" and raw_value > 0x7FFFFFFF:
                    raw_value -= 1 << 32
            else:
                raw_value = raw_values[0]

            instructions[reg.name] = raw_value * reg.scale

            logger.debug(
                "Collected write instruction | name=%s | value=%s",
                reg.name,
                instructions[reg.name],
            )

        except Exception as e:
            logger.warning(
                "Failed to read writable register | name=%s | addr=%s | error=%s",
                reg.name,
                reg.address,
                e,
            )

    return instructions


class ModbusSimulator:
    def __init__(self, address: str, port: int, unit_id: int):
        self.address = address
        self.port = port
        self.unit_id = unit_id
        device_context = create_device_context()

        self.context = ModbusServerContext(
            devices={unit_id: device_context},
            single=False
        )

    async def run_server(self):
        logger.info(
            "Modbus device started | port=%s | unit_id=%s",
            self.port,
            self.unit_id,
        )
        await StartAsyncTcpServer(context=self.context, address=(self.address, self.port))
