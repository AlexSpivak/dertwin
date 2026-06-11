from dertwin.devices.bess.battery import BatteryModel
from dertwin.devices.bess.bess import BESSModel
from dertwin.devices.bess.controller import (
    BESSController,
    WORKING_MODE_OFF_GRID,
    WORKING_MODE_ON_GRID,
    WORKING_MODE_VSG,
)
from dertwin.devices.bess.inverter import InverterModel
from dertwin.devices.bess.simulator import BESSSimulator


def make_controller():
    battery = BatteryModel(
        capacity_kwh=100.0,
        initial_soc=50.0,
        max_charge_kw=50.0,
        max_discharge_kw=50.0,
    )
    inverter = InverterModel(
        max_charge_kw=50.0,
        max_discharge_kw=50.0,
        ramp_rate_kw_per_s=1000.0,
    )
    return BESSController(BESSModel(battery, inverter))


def test_default_working_mode_is_on_grid():
    controller = make_controller()
    assert controller.state.working_mode == WORKING_MODE_ON_GRID


def test_writing_off_grid_flips_state():
    controller = make_controller()
    controller.apply_command("working_mode", WORKING_MODE_OFF_GRID)
    assert controller.state.working_mode == WORKING_MODE_OFF_GRID


def test_writing_vsg_flips_state():
    controller = make_controller()
    controller.apply_command("working_mode", WORKING_MODE_VSG)
    assert controller.state.working_mode == WORKING_MODE_VSG


def test_off_grid_then_back_to_on_grid():
    controller = make_controller()
    controller.apply_command("working_mode", WORKING_MODE_OFF_GRID)
    controller.apply_command("working_mode", WORKING_MODE_ON_GRID)
    assert controller.state.working_mode == WORKING_MODE_ON_GRID


def test_invalid_encoding_is_ignored():
    """A holding register defaults to 0 before the bench driver has ever
    written it. The controller must preserve the default mode rather than
    corrupt state with a meaningless value."""
    controller = make_controller()
    controller.apply_command("working_mode", 0)
    assert controller.state.working_mode == WORKING_MODE_ON_GRID

    controller.apply_command("working_mode", 0x99)
    assert controller.state.working_mode == WORKING_MODE_ON_GRID


def test_telemetry_reflects_current_working_mode():
    sim = BESSSimulator()
    sim.controller.state.run_mode = 1  # required for step() to dispatch

    sim.update(0.1)
    assert sim.get_telemetry().working_mode == WORKING_MODE_ON_GRID

    sim.apply_commands({"working_mode": WORKING_MODE_OFF_GRID})
    sim.update(0.1)
    assert sim.get_telemetry().working_mode == WORKING_MODE_OFF_GRID

def test_invalid_encoding_preserves_current_not_default():
    controller = make_controller()
    controller.apply_command("working_mode", WORKING_MODE_OFF_GRID)
    controller.apply_command("working_mode", 0x99)  # garbage while off-grid
    assert controller.state.working_mode == WORKING_MODE_OFF_GRID  # held, not reset