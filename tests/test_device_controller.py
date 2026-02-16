import pytest

from dertwin.controllers.device_controller import DeviceController
from dertwin.core.registers import RegisterMap, RegisterDefinition, RegisterDirection
from dertwin.devices.bess import BESSSimulator
from dertwin.devices.inverter import InverterSimulator
from dertwin.devices.energy_meter import EnergyMeterSimulator
from dertwin.devices.grid_frequency import GridFrequencyModel
from dertwin.protocol.modbus import ModbusSimulator


# ============================================================
# BESS CONTROLLER TESTS
# ============================================================

BESS_REGISTERS = [
    RegisterDefinition(
        name="start_stop_standby",
        address=10055,
        func=0x06,
        direction=RegisterDirection.WRITE,
        type="uint16",
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

BESS_MAP = RegisterMap(BESS_REGISTERS)

def test_controller_forwards_commands_to_bess():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    modbus = ModbusSimulator(address="0.0.0.0", port=5021, unit_id=1)

    controller = DeviceController(
        device=bess,
        protocols=[modbus],
        register_map=BESS_MAP,
    )

    controller.write_protocol_commands({"start_stop_standby": 1})
    controller.step(dt=0.1)

    assert bess.mode == "discharge"


def test_controller_bess_ramp_flow():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    bess.max_discharge_kw = 50

    modbus = ModbusSimulator(address="0.0.0.0", port=5021, unit_id=1)

    controller = DeviceController(
        device=bess,
        protocols=[modbus],
        register_map=BESS_MAP,
    )

    controller.write_protocol_commands({"on_grid_power": 50.0})

    for _ in range(100):
        controller.step(dt=0.1)

    assert abs(bess.commanded_power_kw - 50.0) < 1e-6


def test_controller_bess_applies_only_on_change():
    bess = BESSSimulator()
    modbus = ModbusSimulator(address="0.0.0.0", port=5021, unit_id=1)

    controller = DeviceController(
        device=bess,
        protocols=[modbus],
        register_map=BESS_MAP,
    )

    controller.write_protocol_commands({"on_grid_power": 10})
    controller.step(dt=0.1)

    assert bess.on_grid_power_kw == 10

    controller.step(dt=0.1)
    assert bess.on_grid_power_kw == 10

    controller.write_protocol_commands({"on_grid_power": 20})
    controller.step(dt=0.1)

    assert bess.on_grid_power_kw == 20


# ============================================================
# INVERTER CONTROLLER TESTS
# ============================================================

# Inverter currently does not use Modbus commands,
# but we verify controller still updates it correctly.

def test_controller_updates_inverter_power():
    inverter = InverterSimulator(rated_kw=10.0)
    inverter.set_irradiance(1.0)

    modbus = ModbusSimulator(address="0.0.0.0", port=5021, unit_id=1)

    controller = DeviceController(
        device=inverter,
        protocols=[modbus],
        register_map=RegisterMap([]),
    )

    controller.step(dt=1.0)

    telemetry = inverter.get_telemetry()

    expected_power = 10000.0 * inverter.efficiency
    assert abs(telemetry["total_active_power"] - expected_power) < 1e-6


def test_controller_inverter_energy_accumulates():
    inverter = InverterSimulator(rated_kw=10.0)
    inverter.set_irradiance(1.0)

    modbus = ModbusSimulator(address="0.0.0.0", port=5021, unit_id=1)

    controller = DeviceController(
        device=inverter,
        protocols=[modbus],
        register_map=RegisterMap([]),
    )

    for _ in range(3600):
        controller.step(dt=1.0)

    expected_kwh = 10.0 * inverter.efficiency
    assert abs(inverter.today_energy_kwh - expected_kwh) < 0.01


# ============================================================
# ENERGY METER CONTROLLER TESTS
# ============================================================

def create_meter(load_kw, pv_w=0.0, bess_w=0.0):
    return EnergyMeterSimulator(
        base_load_supplier=lambda t: load_kw,
        pv_supplier=lambda: pv_w,
        bess_supplier=lambda: bess_w,
        grid_frequency_model=GridFrequencyModel(seed=1),
        seed=1,
    )


def test_controller_updates_energy_meter_import():
    meter = create_meter(load_kw=10.0)

    modbus = ModbusSimulator(address="0.0.0.0", port=5021, unit_id=1)

    controller = DeviceController(
        device=meter,
        protocols=[modbus],
        register_map=RegisterMap([]),
    )

    controller.step(dt=3600)  # 1 hour

    telemetry = meter.get_telemetry()

    assert telemetry["total_active_power"] == 10.0
    assert pytest.approx(telemetry["total_import_energy"], 0.001) == 10.0
    assert telemetry["total_export_energy"] == 0.0


def test_controller_updates_energy_meter_export():
    meter = create_meter(load_kw=5.0, pv_w=15000.0)

    modbus = ModbusSimulator(address="0.0.0.0", port=5021, unit_id=1)

    controller = DeviceController(
        device=meter,
        protocols=[modbus],
        register_map=RegisterMap([]),
    )

    controller.step(dt=3600)

    telemetry = meter.get_telemetry()

    assert telemetry["total_active_power"] == -10.0
    assert pytest.approx(telemetry["total_export_energy"], 0.001) == 10.0
    assert telemetry["total_import_energy"] == 0.0


def test_controller_meter_is_passive_to_commands():
    meter = create_meter(load_kw=5.0)

    modbus = ModbusSimulator(address="0.0.0.0", port=5021, unit_id=1)

    controller = DeviceController(
        device=meter,
        protocols=[modbus],
        register_map=RegisterMap([]),
    )

    controller.write_protocol_commands({"some_random_command": 123})
    controller.step(dt=1.0)

    # Meter ignores commands — still behaves normally
    telemetry = meter.get_telemetry()
    assert telemetry["total_active_power"] == 5.0
