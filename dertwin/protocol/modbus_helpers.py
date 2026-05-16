"""
Modbus register helpers — drop into dertwin/protocol/modbus_helpers.py

Function code handling:
  - FC02 (read discrete inputs)     → setValues(2, ...) on context, single bit
  - FC03 (read holding registers)   → setValues(3, ...) on context, command readback
  - FC04 (read input registers)     → setValues(4, ...) on context, telemetry
  - FC06 (write single register)    → datastore 3 (holding registers)
  - FC10 (write multiple registers) → datastore 3 (holding registers)
"""

import logging
from dertwin.core.registers import RegisterMap
from dertwin.protocol.encoding import encode_value, decode_value

logger = logging.getLogger(__name__)


def write_telemetry_registers(context, unit_id: int, telemetry: dict, register_map: RegisterMap):
    """
    Write device telemetry values into the appropriate Modbus datastore.

    Routes by func code:
      FC02 (discrete inputs)  → datastore 2, single-bit value (0/1)
      FC04 (input registers)  → datastore 4, multi-byte encoded value
    """
    for reg_def in register_map.reads:
        value = telemetry.get(reg_def.name)
        if value is None:
            continue

        try:
            if reg_def.func == 0x02:
                # Discrete input — single bit
                bit = 1 if int(value) else 0
                context[unit_id].setValues(2, reg_def.address, [bit])
                continue

            # Default: input register (FC04)
            words = encode_value(
                value=float(value),
                data_type=reg_def.type,
                scale=reg_def.scale,
                count=reg_def.count,
                endian=reg_def.endian,
            )
            context[unit_id].setValues(reg_def.func, reg_def.address, words)
        except Exception as e:
            logger.warning("Failed to write telemetry register %s: %s", reg_def.name, e)


def write_command_registers(context, unit_id: int, commands: dict, register_map: RegisterMap):
    """
    Write command values into Modbus holding registers (FC03/06/10 share datastore 3).
    Only writes registers explicitly present in commands — does not overwrite
    unrelated registers with 0.
    """
    for reg_def in register_map.writes:
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