"""
Endian-aware register encoding/decoding for Modbus protocol layer.

Endianness convention:
  RegisterEndian.BIG    (default): [high_word, low_word]  — standard Modbus
  RegisterEndian.LITTLE          : [low_word, high_word]
"""

from __future__ import annotations
from dertwin.core.registers import RegisterEndian


def encode_value(
    value: float,
    data_type: str,
    scale: float,
    count: int,
    endian: RegisterEndian = RegisterEndian.BIG,
) -> list[int]:
    """
    Convert a float value into a list of 16-bit Modbus register words.

    Args:
        value:     Physical value (e.g. 50.0 kW)
        data_type: uint16 | int16 | uint32 | int32
        scale:     Register scale factor (physical = raw * scale)
        count:     Number of registers (1 for 16-bit, 2 for 32-bit)
        endian:    BIG → [high, low], LITTLE → [low, high]

    Returns:
        List of uint16 register values.
    """
    raw = int(round(value / scale))

    if data_type == "uint16":
        return [raw & 0xFFFF]

    if data_type == "int16":
        if raw < 0:
            raw = (1 << 16) + raw
        return [raw & 0xFFFF]

    if data_type in ("uint32", "int32"):
        if data_type == "int32" and raw < 0:
            raw = (1 << 32) + raw
        raw = raw & 0xFFFFFFFF
        high = (raw >> 16) & 0xFFFF
        low = raw & 0xFFFF
        if endian == RegisterEndian.LITTLE:
            return [low, high]
        return [high, low]

    raise ValueError(f"Unsupported data type: {data_type}")


def decode_value(
    registers: list[int],
    data_type: str,
    scale: float,
    endian: RegisterEndian = RegisterEndian.BIG,
) -> float:
    """
    Decode a list of Modbus register words into a physical float value.

    Args:
        registers: Raw uint16 register values from Modbus frame
        data_type: uint16 | int16 | uint32 | int32
        scale:     Register scale factor
        endian:    BIG → [high, low], LITTLE → [low, high]

    Returns:
        Physical value as float.
    """
    if data_type == "uint16":
        return registers[0] * scale

    if data_type == "int16":
        raw = registers[0]
        if raw >= 0x8000:
            raw -= 0x10000
        return raw * scale

    if data_type in ("uint32", "int32"):
        if endian == RegisterEndian.LITTLE:
            low, high = registers[0], registers[1]
        else:
            high, low = registers[0], registers[1]
        raw = (high << 16) | low
        if data_type == "int32" and raw >= 0x80000000:
            raw -= 0x100000000
        return raw * scale

    raise ValueError(f"Unsupported data type: {data_type}")