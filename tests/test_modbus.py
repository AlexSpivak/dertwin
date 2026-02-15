from dertwin.protocol.modbus import (
    ModbusSimulator,
    write_command_registers,
    collect_write_instructions,
)

TEST_CONFIG = [
    {"address": 10055, "name": "start_stop_standby", "func": 0x06, "type": "int16"},
    {"address": 10126, "name": "on_grid_power", "func": 0x10, "type": "int32", "scale": 0.1, "count": 2},
]


def test_write_and_collect_command():
    modbus = ModbusSimulator(port=5021, unit_id=1)

    write_command_registers(
        TEST_CONFIG,
        modbus.context,
        1,
        {"on_grid_power": 50.0},
    )

    instructions = collect_write_instructions(
        TEST_CONFIG,
        modbus.context,
        1,
    )

    assert instructions["on_grid_power"] == 50.0


def test_collect_int16_signed():
    modbus = ModbusSimulator(port=5021, unit_id=1)

    write_command_registers(
        TEST_CONFIG,
        modbus.context,
        1,
        {"start_stop_standby": -1},
    )

    instructions = collect_write_instructions(
        TEST_CONFIG,
        modbus.context,
        1,
    )

    assert instructions["start_stop_standby"] == -1
