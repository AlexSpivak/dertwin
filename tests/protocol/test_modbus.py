import pytest

from dertwin.protocol.modbus import (
    ModbusTCPSimulator,
    ModbusRTUSimulator,
    create_device_context,
)
from dertwin.protocol.encoding import encode_value
from dertwin.protocol.modbus_helpers import (
    write_command_registers,
    write_telemetry_registers,
    collect_write_instructions,
)

from dertwin.core.registers import (
    RegisterDefinition,
    RegisterDirection,
    RegisterEndian,
    RegisterMap,
)


# ==========================================================
# SHARED REGISTER FIXTURES
# ==========================================================

WRITE_INT16 = RegisterDefinition(
    name="start_stop_standby",
    internal_name="start_stop_standby",
    address=10055,
    func=0x06,
    direction=RegisterDirection.WRITE,
    type="int16",
    count=1,
    scale=1.0,
)

WRITE_INT32 = RegisterDefinition(
    name="on_grid_power",
    internal_name="on_grid_power",
    address=10126,
    func=0x10,
    direction=RegisterDirection.WRITE,
    type="int32",
    count=2,
    scale=0.1,
)

WRITE_UINT16 = RegisterDefinition(
    name="fault_reset",
    internal_name="fault_reset",
    address=11004,
    func=0x06,
    direction=RegisterDirection.WRITE,
    type="uint16",
    count=1,
    scale=1.0,
)

WRITE_UINT32 = RegisterDefinition(
    name="current_transformer_ratio",
    internal_name="current_transformer_ratio",
    address=4099,
    func=0x06,
    direction=RegisterDirection.WRITE,
    type="uint32",
    count=2,
    scale=0.1,
)

READ_UINT16 = RegisterDefinition(
    name="grid_frequency",
    internal_name="grid_frequency",
    address=13,
    func=0x04,
    direction=RegisterDirection.READ,
    type="uint16",
    count=1,
    scale=0.01,
)

READ_INT16 = RegisterDefinition(
    name="total_power_factor",
    internal_name="total_power_factor",
    address=12,
    func=0x04,
    direction=RegisterDirection.READ,
    type="int16",
    count=1,
    scale=0.001,
)

READ_INT32 = RegisterDefinition(
    name="active_power",
    internal_name="active_power",
    address=10270,
    func=0x04,
    direction=RegisterDirection.READ,
    type="int32",
    count=2,
    scale=0.1,
)

READ_UINT32 = RegisterDefinition(
    name="total_import_energy",
    internal_name="total_import_energy",
    address=26,
    func=0x04,
    direction=RegisterDirection.READ,
    type="uint32",
    count=2,
    scale=0.01,
)

ALL_WRITE_REGISTERS = [WRITE_INT16, WRITE_INT32, WRITE_UINT16, WRITE_UINT32]
ALL_READ_REGISTERS  = [READ_UINT16, READ_INT16, READ_INT32, READ_UINT32]
ALL_REGISTERS       = ALL_WRITE_REGISTERS + ALL_READ_REGISTERS


def make_register_map(registers: list) -> RegisterMap:
    """Build a RegisterMap from a list of RegisterDefinition objects."""
    return RegisterMap(registers)


# ==========================================================
# HELPERS
# ==========================================================

def make_tcp(unit_id: int = 1) -> ModbusTCPSimulator:
    return ModbusTCPSimulator(address="0.0.0.0", port=5021, unit_id=unit_id)


def make_rtu(unit_id: int = 1) -> ModbusRTUSimulator:
    return ModbusRTUSimulator(port="/dev/null", unit_id=unit_id)


def _write_and_collect(context, unit_id, registers: list, commands: dict) -> dict:
    """Write commands then collect them back — returns collected dict."""
    register_map = make_register_map(registers)
    write_command_registers(context=context, unit_id=unit_id, commands=commands, register_map=register_map)
    return collect_write_instructions(register_map=register_map, context=context, unit_id=unit_id)


# ==========================================================
# ENCODE VALUE
# ==========================================================

class TestEncodeValue:

    def test_uint16_positive(self):
        assert encode_value(100.0, "uint16", 1.0, 1) == [100]

    def test_uint16_with_scale(self):
        assert encode_value(50.0, "uint16", 0.01, 1) == [5000]

    def test_int16_positive(self):
        assert encode_value(42.0, "int16", 1.0, 1) == [42]

    def test_int16_negative(self):
        result = encode_value(-1.0, "int16", 1.0, 1)
        assert result == [0xFFFF]

    def test_int16_negative_large(self):
        result = encode_value(-100.0, "int16", 1.0, 1)
        assert result == [(1 << 16) - 100]

    def test_int32_positive(self):
        result = encode_value(100000.0, "int32", 1.0, 2)
        high = (100000 >> 16) & 0xFFFF
        low = 100000 & 0xFFFF
        assert result == [high, low]

    def test_int32_negative(self):
        result = encode_value(-500.0, "int32", 0.1, 2)
        reg_value = int(-500.0 / 0.1)   # -5000
        unsigned = (1 << 32) + reg_value
        high = (unsigned >> 16) & 0xFFFF
        low = unsigned & 0xFFFF
        assert result == [high, low]

    def test_uint32_positive(self):
        result = encode_value(200000.0, "uint32", 1.0, 2)
        high = (200000 >> 16) & 0xFFFF
        low = 200000 & 0xFFFF
        assert result == [high, low]

    def test_uint32_with_scale(self):
        result = encode_value(50.0, "uint32", 0.01, 2)
        reg_value = 5000
        high = (reg_value >> 16) & 0xFFFF
        low = reg_value & 0xFFFF
        assert result == [high, low]

    def test_int32_big_endian_default(self):
        """Default endian is BIG — [high, low]."""
        result = encode_value(500.0, "int32", 0.1, 2)
        assert result == encode_value(500.0, "int32", 0.1, 2, RegisterEndian.BIG)

    def test_int32_little_endian_differs_from_big(self):
        big    = encode_value(500.0, "int32", 0.1, 2, RegisterEndian.BIG)
        little = encode_value(500.0, "int32", 0.1, 2, RegisterEndian.LITTLE)
        assert big != little
        assert big == list(reversed(little))


# ==========================================================
# CREATE DEVICE CONTEXT
# ==========================================================

def test_create_device_context():
    ctx = create_device_context()
    assert ctx is not None
    assert ctx.getValues(4, 0, 1) == [0]   # input registers
    assert ctx.getValues(3, 0, 1) == [0]   # holding registers


# ==========================================================
# TCP SIMULATOR — COMMAND ROUND-TRIP
# ==========================================================

class TestTCPCommandRoundTrip:

    def test_int32_write_and_collect(self):
        sim = make_tcp()
        result = _write_and_collect(sim.context, 1, [WRITE_INT32], {"on_grid_power": 50.0})
        assert result["on_grid_power"] == 50.0

    def test_int32_negative(self):
        sim = make_tcp()
        result = _write_and_collect(sim.context, 1, [WRITE_INT32], {"on_grid_power": -75.0})
        assert result["on_grid_power"] == pytest.approx(-75.0, abs=0.1)

    def test_int16_positive(self):
        sim = make_tcp()
        result = _write_and_collect(sim.context, 1, [WRITE_INT16], {"start_stop_standby": 1})
        assert result["start_stop_standby"] == 1.0

    def test_int16_negative(self):
        sim = make_tcp()
        result = _write_and_collect(sim.context, 1, [WRITE_INT16], {"start_stop_standby": -1})
        assert result["start_stop_standby"] == -1.0

    def test_uint16(self):
        sim = make_tcp()
        result = _write_and_collect(sim.context, 1, [WRITE_UINT16], {"fault_reset": 1})
        assert result["fault_reset"] == 1.0

    def test_uint32_with_scale(self):
        sim = make_tcp()
        result = _write_and_collect(sim.context, 1, [WRITE_UINT32], {"current_transformer_ratio": 100.0})
        assert result["current_transformer_ratio"] == pytest.approx(100.0, abs=0.1)

    def test_multiple_commands(self):
        sim = make_tcp()
        commands = {
            "start_stop_standby": 2,
            "on_grid_power": -30.0,
            "fault_reset": 1,
        }
        result = _write_and_collect(sim.context, 1, ALL_WRITE_REGISTERS, commands)
        assert result["start_stop_standby"] == 2.0
        assert result["on_grid_power"] == pytest.approx(-30.0, abs=0.1)
        assert result["fault_reset"] == 1.0

    def test_missing_command_defaults_to_zero(self):
        sim = make_tcp()
        register_map = make_register_map([WRITE_INT16, WRITE_INT32])
        write_command_registers(context=sim.context, unit_id=1, commands={"on_grid_power": 50.0}, register_map=register_map)
        result = collect_write_instructions(register_map=register_map, context=sim.context, unit_id=1)
        assert result["on_grid_power"] == 50.0
        assert result["start_stop_standby"] == 0.0

    def test_overwrite_updates_value(self):
        sim = make_tcp()
        _write_and_collect(sim.context, 1, [WRITE_INT32], {"on_grid_power": 50.0})
        result = _write_and_collect(sim.context, 1, [WRITE_INT32], {"on_grid_power": -25.0})
        assert result["on_grid_power"] == pytest.approx(-25.0, abs=0.1)

    def test_zero_value(self):
        sim = make_tcp()
        result = _write_and_collect(sim.context, 1, [WRITE_INT32], {"on_grid_power": 0.0})
        assert result["on_grid_power"] == 0.0

    def test_little_endian_roundtrip(self):
        """Register with little endian — write and collect must return original value."""
        little_reg = RegisterDefinition(
            name="on_grid_power_le",
            internal_name="on_grid_power_le",
            address=10200,
            func=0x10,
            direction=RegisterDirection.WRITE,
            type="int32",
            count=2,
            scale=0.1,
            endian=RegisterEndian.LITTLE,
        )
        sim = make_tcp()
        result = _write_and_collect(sim.context, 1, [little_reg], {"on_grid_power_le": -75.0})
        assert result["on_grid_power_le"] == pytest.approx(-75.0, abs=0.1)

    def test_big_vs_little_endian_different_wire_encoding(self):
        """Same value must produce different register words for big vs little endian."""
        big_reg = RegisterDefinition(
            name="power_big", internal_name="power_big",
            address=10300, func=0x10,
            direction=RegisterDirection.WRITE, type="int32", count=2, scale=0.1,
            endian=RegisterEndian.BIG,
        )
        little_reg = RegisterDefinition(
            name="power_little", internal_name="power_little",
            address=10302, func=0x10,
            direction=RegisterDirection.WRITE, type="int32", count=2, scale=0.1,
            endian=RegisterEndian.LITTLE,
        )
        sim = make_tcp()
        register_map = make_register_map([big_reg, little_reg])
        write_command_registers(
            context=sim.context, unit_id=1,
            commands={"power_big": 500.0, "power_little": 500.0},
            register_map=register_map,
        )
        raw_big    = sim.context[1].getValues(3, big_reg.address, 2)
        raw_little = sim.context[1].getValues(3, little_reg.address, 2)
        assert list(raw_big) != list(raw_little)
        assert list(raw_big) == list(reversed(raw_little))


# ==========================================================
# TCP SIMULATOR — TELEMETRY
# ==========================================================

class TestTCPTelemetry:

    def _write_tel(self, sim, registers, telemetry):
        register_map = make_register_map(registers)
        write_telemetry_registers(
            context=sim.context, unit_id=1,
            telemetry=telemetry, register_map=register_map,
        )
        return sim

    def test_uint16_telemetry(self):
        sim = make_tcp()
        self._write_tel(sim, [READ_UINT16], {"grid_frequency": 50.0})
        raw = sim.context[1].getValues(4, READ_UINT16.address, 1)
        assert raw[0] * READ_UINT16.scale == pytest.approx(50.0, abs=0.01)

    def test_int16_telemetry_positive(self):
        sim = make_tcp()
        self._write_tel(sim, [READ_INT16], {"total_power_factor": 0.95})
        raw = sim.context[1].getValues(4, READ_INT16.address, 1)
        value = raw[0] if raw[0] <= 0x7FFF else raw[0] - (1 << 16)
        assert value * READ_INT16.scale == pytest.approx(0.95, abs=0.001)

    def test_int16_telemetry_negative(self):
        sim = make_tcp()
        self._write_tel(sim, [READ_INT16], {"total_power_factor": -0.85})
        raw = sim.context[1].getValues(4, READ_INT16.address, 1)
        value = raw[0] if raw[0] <= 0x7FFF else raw[0] - (1 << 16)
        assert value * READ_INT16.scale == pytest.approx(-0.85, abs=0.001)

    def test_int32_telemetry_positive(self):
        sim = make_tcp()
        self._write_tel(sim, [READ_INT32], {"active_power": 250.0})
        raw = sim.context[1].getValues(4, READ_INT32.address, 2)
        value = (raw[0] << 16) + raw[1]
        assert value * READ_INT32.scale == pytest.approx(250.0, abs=0.1)

    def test_int32_telemetry_negative(self):
        sim = make_tcp()
        self._write_tel(sim, [READ_INT32], {"active_power": -100.0})
        raw = sim.context[1].getValues(4, READ_INT32.address, 2)
        value = (raw[0] << 16) + raw[1]
        if value > 0x7FFFFFFF:
            value -= 1 << 32
        assert value * READ_INT32.scale == pytest.approx(-100.0, abs=0.1)

    def test_uint32_telemetry(self):
        sim = make_tcp()
        self._write_tel(sim, [READ_UINT32], {"total_import_energy": 12345.67})
        raw = sim.context[1].getValues(4, READ_UINT32.address, 2)
        value = (raw[0] << 16) + raw[1]
        assert value * READ_UINT32.scale == pytest.approx(12345.67, abs=0.01)

    def test_missing_telemetry_key_is_skipped(self):
        sim = make_tcp()
        self._write_tel(sim, [READ_UINT16], {"nonexistent_field": 999.0})
        raw = sim.context[1].getValues(4, READ_UINT16.address, 1)
        assert raw[0] == 0

    def test_little_endian_telemetry_roundtrip(self):
        """Little endian read register — write then verify word order is swapped."""
        little_read = RegisterDefinition(
            name="active_power_le", internal_name="active_power_le",
            address=10400, func=0x04,
            direction=RegisterDirection.READ, type="int32", count=2, scale=0.1,
            endian=RegisterEndian.LITTLE,
        )
        sim = make_tcp()
        register_map = make_register_map([little_read])
        write_telemetry_registers(
            context=sim.context, unit_id=1,
            telemetry={"active_power_le": 250.0},
            register_map=register_map,
        )
        raw = sim.context[1].getValues(4, little_read.address, 2)
        # little endian: [low, high]
        low, high = raw[0], raw[1]
        value = (high << 16) | low
        assert value * little_read.scale == pytest.approx(250.0, abs=0.1)


# ==========================================================
# TCP SIMULATOR — DIRECTION ISOLATION
# ==========================================================

class TestDirectionIsolation:

    def test_collect_returns_only_write_registers(self):
        sim = make_tcp()
        register_map = make_register_map(ALL_REGISTERS)
        result = collect_write_instructions(register_map=register_map, context=sim.context, unit_id=1)
        write_names = {r.internal_name for r in ALL_WRITE_REGISTERS}
        for key in result:
            assert key in write_names

    def test_write_commands_skips_read_registers(self):
        sim = make_tcp()
        register_map = make_register_map(ALL_REGISTERS)
        write_command_registers(
            context=sim.context, unit_id=1,
            commands={"grid_frequency": 60.0},
            register_map=register_map,
        )
        raw = sim.context[1].getValues(3, READ_UINT16.address, 1)
        assert raw[0] == 0


# ==========================================================
# TCP SIMULATOR — UNIT ID ISOLATION
# ==========================================================

class TestUnitId:

    def test_different_unit_ids_are_isolated(self):
        sim_a = ModbusTCPSimulator(address="0.0.0.0", port=5021, unit_id=1)
        sim_b = ModbusTCPSimulator(address="0.0.0.0", port=5022, unit_id=2)
        _write_and_collect(sim_a.context, 1, [WRITE_INT32], {"on_grid_power": 100.0})
        result_b = collect_write_instructions(
            register_map=make_register_map([WRITE_INT32]),
            context=sim_b.context, unit_id=2,
        )
        assert result_b["on_grid_power"] == 0.0


# ==========================================================
# RTU SIMULATOR — CONTEXT PARITY WITH TCP
# ==========================================================

class TestRTUContextParity:

    def test_int32_write_and_collect(self):
        sim = make_rtu()
        result = _write_and_collect(sim.context, 1, [WRITE_INT32], {"on_grid_power": 50.0})
        assert result["on_grid_power"] == 50.0

    def test_int32_negative(self):
        sim = make_rtu()
        result = _write_and_collect(sim.context, 1, [WRITE_INT32], {"on_grid_power": -75.0})
        assert result["on_grid_power"] == pytest.approx(-75.0, abs=0.1)

    def test_int16_signed(self):
        sim = make_rtu()
        result = _write_and_collect(sim.context, 1, [WRITE_INT16], {"start_stop_standby": -1})
        assert result["start_stop_standby"] == -1.0

    def test_uint16(self):
        sim = make_rtu()
        result = _write_and_collect(sim.context, 1, [WRITE_UINT16], {"fault_reset": 1})
        assert result["fault_reset"] == 1.0

    def test_uint32_with_scale(self):
        sim = make_rtu()
        result = _write_and_collect(sim.context, 1, [WRITE_UINT32], {"current_transformer_ratio": 100.0})
        assert result["current_transformer_ratio"] == pytest.approx(100.0, abs=0.1)

    def test_telemetry_uint16(self):
        sim = make_rtu()
        register_map = make_register_map([READ_UINT16])
        write_telemetry_registers(context=sim.context, unit_id=1, telemetry={"grid_frequency": 50.0}, register_map=register_map)
        raw = sim.context[1].getValues(4, READ_UINT16.address, 1)
        assert raw[0] * READ_UINT16.scale == pytest.approx(50.0, abs=0.01)

    def test_telemetry_int32_negative(self):
        sim = make_rtu()
        register_map = make_register_map([READ_INT32])
        write_telemetry_registers(context=sim.context, unit_id=1, telemetry={"active_power": -100.0}, register_map=register_map)
        raw = sim.context[1].getValues(4, READ_INT32.address, 2)
        value = (raw[0] << 16) + raw[1]
        if value > 0x7FFFFFFF:
            value -= 1 << 32
        assert value * READ_INT32.scale == pytest.approx(-100.0, abs=0.1)

    def test_telemetry_uint32(self):
        sim = make_rtu()
        register_map = make_register_map([READ_UINT32])
        write_telemetry_registers(context=sim.context, unit_id=1, telemetry={"total_import_energy": 9999.99}, register_map=register_map)
        raw = sim.context[1].getValues(4, READ_UINT32.address, 2)
        value = (raw[0] << 16) + raw[1]
        assert value * READ_UINT32.scale == pytest.approx(9999.99, abs=0.01)

    def test_multiple_commands(self):
        sim = make_rtu()
        commands = {
            "start_stop_standby": -1,
            "on_grid_power": 80.0,
            "fault_reset": 1,
            "current_transformer_ratio": 50.0,
        }
        result = _write_and_collect(sim.context, 1, ALL_WRITE_REGISTERS, commands)
        assert result["start_stop_standby"] == -1.0
        assert result["on_grid_power"] == pytest.approx(80.0, abs=0.1)
        assert result["fault_reset"] == 1.0
        assert result["current_transformer_ratio"] == pytest.approx(50.0, abs=0.1)


# ==========================================================
# RTU SIMULATOR — ATTRIBUTES
# ==========================================================

class TestRTUAttributes:

    def test_default_serial_params(self):
        sim = ModbusRTUSimulator(port="/dev/ttyUSB0", unit_id=1)
        assert sim.baudrate == 9600
        assert sim.bytesize == 8
        assert sim.parity == "N"
        assert sim.stopbits == 1
        assert sim.timeout == 1.0

    def test_custom_serial_params(self):
        sim = ModbusRTUSimulator(
            port="/dev/ttyS0", unit_id=5,
            baudrate=19200, bytesize=7,
            parity="E", stopbits=2, timeout=0.5,
        )
        assert sim.port == "/dev/ttyS0"
        assert sim.unit_id == 5
        assert sim.baudrate == 19200
        assert sim.bytesize == 7
        assert sim.parity == "E"
        assert sim.stopbits == 2
        assert sim.timeout == 0.5

    def test_context_created(self):
        sim = make_rtu()
        assert sim.context is not None
        assert sim.context[1] is not None

    def test_task_initially_none(self):
        sim = make_rtu()
        assert sim._task is None


# ==========================================================
# TCP SIMULATOR — ATTRIBUTES
# ==========================================================

class TestTCPAttributes:

    def test_attributes(self):
        sim = ModbusTCPSimulator(address="127.0.0.1", port=5020, unit_id=3)
        assert sim.address == "127.0.0.1"
        assert sim.port == 5020
        assert sim.unit_id == 3

    def test_context_created(self):
        sim = make_tcp()
        assert sim.context is not None
        assert sim.context[1] is not None

    def test_task_initially_none(self):
        sim = make_tcp()
        assert sim._task is None


class TestRegisterMapEndianValidation:
    """Register map must reject invalid endian values at parse time."""

    def _make_entry(self, endian: str) -> dict:
        return {
            "name": "test_reg",
            "internal_name": "test_reg",
            "address": 1000,
            "func": "0x04",
            "direction": "read",
            "type": "int32",
            "count": 2,
            "scale": 0.1,
            "endian": endian,
        }

    def test_invalid_endian_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid endian"):
            RegisterMap._parse_entry(self._make_entry("middle"))

    def test_invalid_endian_typo_raises(self):
        with pytest.raises(ValueError, match="Invalid endian"):
            RegisterMap._parse_entry(self._make_entry("Little"))  # wrong case

    def test_invalid_endian_empty_raises(self):
        with pytest.raises(ValueError, match="Invalid endian"):
            RegisterMap._parse_entry(self._make_entry(""))

    def test_valid_big_parses(self):
        reg = RegisterMap._parse_entry(self._make_entry("big"))
        assert reg.endian == RegisterEndian.BIG

    def test_valid_little_parses(self):
        reg = RegisterMap._parse_entry(self._make_entry("little"))
        assert reg.endian == RegisterEndian.LITTLE
