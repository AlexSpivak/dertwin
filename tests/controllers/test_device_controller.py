import pytest

from dertwin.controllers.device_controller import DeviceController
from dertwin.core.registers import RegisterMap, RegisterDefinition, RegisterDirection, RegisterEndian
from dertwin.devices.bess.simulator import BESSSimulator
from dertwin.devices.external.grid_voltage import GridVoltageModel
from dertwin.devices.external.power_flow import SitePowerModel
from dertwin.devices.pv.simulator import PVSimulator
from dertwin.devices.energy_meter.simulator import EnergyMeterSimulator
from dertwin.devices.external.grid_frequency import GridFrequencyModel
from dertwin.protocol.modbus import ModbusTCPSimulator
from dertwin.protocol.modbus_helpers import write_command_registers


# ============================================================
# SHARED REGISTER DEFINITIONS
# ============================================================

BESS_REGISTERS = [
    RegisterDefinition(
        name="start_stop_standby",
        internal_name="start_stop_standby",
        address=10055,
        func=0x06,
        direction=RegisterDirection.WRITE,
        type="uint16",
        count=1,
        scale=1.0,
    ),
    RegisterDefinition(
        name="on_grid_power_setpoint",
        internal_name="active_power_setpoint",
        address=10126,
        func=0x10,
        direction=RegisterDirection.WRITE,
        type="int32",
        count=2,
        scale=0.1,
    ),
]

BESS_MAP = RegisterMap(BESS_REGISTERS)


def write_commands(modbus: ModbusTCPSimulator, register_map: RegisterMap, commands: dict):
    """Helper — write commands directly into Modbus holding registers."""
    write_command_registers(
        context=modbus.context,
        unit_id=modbus.unit_id,
        commands=commands,
        register_map=register_map,
    )


# ============================================================
# BESS CONTROLLER TESTS
# ============================================================

def test_controller_forwards_commands_to_bess():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    modbus = ModbusTCPSimulator(address="0.0.0.0", port=5021, unit_id=1)
    controller = DeviceController(device=bess, protocols=[modbus], register_map=BESS_MAP)

    controller.step(dt=0.1)
    write_commands(modbus, BESS_MAP, {"start_stop_standby": 1})
    controller.step(dt=0.1)

    assert bess.mode == "run"


def test_controller_bess_ramp_flow():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    bess.max_discharge_kw = 50
    modbus = ModbusTCPSimulator(address="0.0.0.0", port=5021, unit_id=1)
    controller = DeviceController(device=bess, protocols=[modbus], register_map=BESS_MAP)

    controller.step(dt=0.1)
    write_commands(modbus, BESS_MAP, {"start_stop_standby": 1})
    write_commands(modbus, BESS_MAP, {"on_grid_power_setpoint": 50.0})

    for _ in range(100):
        controller.step(dt=0.1)

    assert abs(bess.commanded_power_kw - 50.0) < 1e-6


def test_controller_bess_applies_only_on_change():
    bess = BESSSimulator()
    modbus = ModbusTCPSimulator(address="0.0.0.0", port=5021, unit_id=1)
    controller = DeviceController(device=bess, protocols=[modbus], register_map=BESS_MAP)

    controller.step(dt=0.1)
    write_commands(modbus, BESS_MAP, {"start_stop_standby": 1})
    write_commands(modbus, BESS_MAP, {"on_grid_power_setpoint": 10})
    controller.step(dt=0.1)
    assert bess.commanded_power_kw == 10

    controller.step(dt=0.1)
    assert bess.commanded_power_kw == 10

    write_commands(modbus, BESS_MAP, {"on_grid_power_setpoint": 20})
    controller.step(dt=0.1)
    assert bess.commanded_power_kw == 20


def test_controller_bess_little_endian_setpoint():
    """
    BESS with little-endian power setpoint register.
    Verifies endian-aware decoding works through the full controller stack.
    """
    little_endian_registers = [
        RegisterDefinition(
            name="start_stop_standby",
            internal_name="start_stop_standby",
            address=10055,
            func=0x06,
            direction=RegisterDirection.WRITE,
            type="uint16",
            count=1,
            scale=1.0,
        ),
        RegisterDefinition(
            name="on_grid_power_setpoint",
            internal_name="active_power_setpoint",
            address=10126,
            func=0x10,
            direction=RegisterDirection.WRITE,
            type="int32",
            count=2,
            scale=0.1,
            endian=RegisterEndian.LITTLE,  # little endian — like Sungrow BESS
        ),
    ]
    little_map = RegisterMap(little_endian_registers)

    bess = BESSSimulator(ramp_rate_kw_per_s=100.0, max_charge_kw=50.0, max_discharge_kw=50.0)
    modbus = ModbusTCPSimulator(address="0.0.0.0", port=5021, unit_id=1)
    controller = DeviceController(device=bess, protocols=[modbus], register_map=little_map)

    controller.step(dt=0.1)
    write_commands(modbus, little_map, {"start_stop_standby": 1})
    write_commands(modbus, little_map, {"on_grid_power_setpoint": -50.0})

    for _ in range(20):
        controller.step(dt=0.1)

    assert bess.commanded_power_kw == pytest.approx(-50.0, abs=0.1)


# ============================================================
# INVERTER CONTROLLER TESTS
# ============================================================

def test_controller_updates_inverter_power():
    pv = PVSimulator(rated_kw=10.0)
    pv.set_irradiance(1000.0)
    modbus = ModbusTCPSimulator(address="0.0.0.0", port=5021, unit_id=1)
    controller = DeviceController(device=pv, protocols=[modbus], register_map=RegisterMap([]))

    controller.step(dt=1.0)

    telemetry = pv.get_telemetry()
    rated_kw = pv.rated_power_w / 1000.0

    assert telemetry.total_active_power > 0.0
    assert telemetry.total_active_power <= rated_kw


def test_controller_inverter_energy_accumulates():
    pv = PVSimulator(rated_kw=10.0)
    pv.set_irradiance(1000.0)
    modbus = ModbusTCPSimulator(address="0.0.0.0", port=5021, unit_id=1)
    controller = DeviceController(device=pv, protocols=[modbus], register_map=RegisterMap([]))

    for _ in range(3600):
        controller.step(dt=1.0)

    assert pv.today_energy_kwh > 0.0
    assert pv.today_energy_kwh <= 10.0


# ============================================================
# ENERGY METER CONTROLLER TESTS
# ============================================================

def create_meter(load_kw, pv_kw=0.0, bess_kw=0.0):
    """All suppliers in kW — SitePowerModel no longer divides internally."""
    power_model = SitePowerModel(
        base_load_supplier=lambda t: load_kw,
        pv_supplier=lambda: pv_kw,
        bess_supplier=lambda: bess_kw,
    )
    grid_model = GridFrequencyModel(seed=1)
    voltage_model = GridVoltageModel(seed=1)
    meter = EnergyMeterSimulator(
        power_model=power_model,
        grid_model=grid_model,
        grid_voltage_model=voltage_model,
        seed=1,
    )
    return meter, power_model


def test_controller_updates_energy_meter_import():
    meter, power_model = create_meter(load_kw=10.0)
    modbus = ModbusTCPSimulator(address="0.0.0.0", port=5021, unit_id=1)
    controller = DeviceController(device=meter, protocols=[modbus], register_map=RegisterMap([]))

    power_model.update(dt=3600)
    controller.step(dt=3600)

    telemetry = meter.get_telemetry()
    assert telemetry.total_active_power == 10.0
    assert pytest.approx(telemetry.total_import_energy, 0.001) == 10.0
    assert telemetry.total_export_energy == 0.0


def test_controller_updates_energy_meter_export():
    meter, power_model = create_meter(load_kw=5.0, pv_kw=15.0)
    modbus = ModbusTCPSimulator(address="0.0.0.0", port=5021, unit_id=1)
    controller = DeviceController(device=meter, protocols=[modbus], register_map=RegisterMap([]))

    power_model.update(dt=3600)
    controller.step(dt=3600)

    telemetry = meter.get_telemetry()
    assert telemetry.total_active_power == -10.0
    assert pytest.approx(telemetry.total_export_energy, 0.001) == 10.0
    assert telemetry.total_import_energy == 0.0


def test_controller_meter_is_passive_to_commands():
    meter, power_model = create_meter(load_kw=5.0)
    modbus = ModbusTCPSimulator(address="0.0.0.0", port=5021, unit_id=1)
    controller = DeviceController(device=meter, protocols=[modbus], register_map=RegisterMap([]))

    power_model.update(dt=3600)
    write_commands(modbus, RegisterMap([]), {"some_random_command": 123})
    controller.step(dt=1.0)

    assert meter.get_telemetry().total_active_power == 5.0