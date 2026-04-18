import logging
from dertwin.core.registers import RegisterMap
from dertwin.protocol.encoding import encode_value, decode_value

logger = logging.getLogger(__name__)


def write_telemetry_registers(context, unit_id: int, telemetry: dict, register_map: RegisterMap):
    """
    Write device telemetry values into Modbus input registers (FC04).
    Respects per-register endianness from the register map.
    """
    for reg_def in register_map.reads:
        value = telemetry.get(reg_def.name)
        if value is None:
            continue
        try:
            words = encode_value(
                value=float(value),
                data_type=reg_def.type,
                scale=reg_def.scale,
                count=reg_def.count,
                endian=reg_def.endian,
            )
            context[unit_id].setValues(4, reg_def.address, words)
        except Exception as e:
            logger.warning("Failed to write telemetry register %s: %s", reg_def.name, e)


def write_command_registers(context, unit_id: int, commands: dict, register_map: RegisterMap):
    """
    Write command values into Modbus holding registers (FC03).
    Only writes registers that are present in commands — does not overwrite
    unrelated registers with 0.
    Respects per-register endianness from the register map.
    """
    for reg_def in register_map.writes:
        # Only write registers explicitly present in commands
        value = commands.get(reg_def.internal_name)
        if value is None:
            value = commands.get(reg_def.name)
        if value is None:
            continue
        try:
            words = encode_value(
                value=float(value),
                data_type=reg_def.type,
                scale=reg_def.scale,
                count=reg_def.count,
                endian=reg_def.endian,
            )
            context[unit_id].setValues(3, reg_def.address, words)
        except Exception as e:
            logger.warning("Failed to write command register %s: %s", reg_def.name, e)


def collect_write_instructions(register_map: RegisterMap, context, unit_id: int) -> dict:
    """
    Read command register values from Modbus holding registers and decode them.
    Respects per-register endianness from the register map.
    Returns dict of internal_name → decoded float value.
    """
    instructions = {}
    for reg_def in register_map.writes:
        try:
            raw = context[unit_id].getValues(3, reg_def.address, count=reg_def.count)
            value = decode_value(
                registers=list(raw),
                data_type=reg_def.type,
                scale=reg_def.scale,
                endian=reg_def.endian,
            )
            instructions[reg_def.internal_name] = value
        except Exception as e:
            logger.warning("Failed to read command register %s: %s", reg_def.name, e)
    return instructions