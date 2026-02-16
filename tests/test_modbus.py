from dertwin.protocol.modbus import (
    ModbusSimulator,
    write_command_registers,
    collect_write_instructions,
)

from dertwin.core.registers import RegisterDefinition, RegisterDirection


TEST_REGISTERS = [
    RegisterDefinition(
        name="start_stop_standby",
        address=10055,
        func=0x06,
        direction=RegisterDirection.WRITE,
        type="int16",
        count=1,
        scale=1.0,
    ),
    RegisterDefinition(
        name="on_grid_power",
        address=10126,
        func=0x10,
        direction=RegisterDirection.WRITE,
        type="int32",
        count=2,
        scale=0.1,
    ),
]


def test_write_and_collect_command():
    modbus = ModbusSimulator(address="0.0.0.0", port=5021, unit_id=1)

    write_command_registers(
        TEST_REGISTERS,
        modbus.context,
        1,
        {"on_grid_power": 50.0},
    )

    instructions = collect_write_instructions(
        TEST_REGISTERS,
        modbus.context,
        1,
    )

    assert instructions["on_grid_power"] == 50.0


def test_collect_int16_signed():
    modbus = ModbusSimulator(address="0.0.0.0", port=5021, unit_id=1)

    write_command_registers(
        TEST_REGISTERS,
        modbus.context,
        1,
        {"start_stop_standby": -1},
    )

    instructions = collect_write_instructions(
        TEST_REGISTERS,
        modbus.context,
        1,
    )

    assert instructions["start_stop_standby"] == -1
