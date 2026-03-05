import math
import pytest

from dertwin.devices.external.grid_voltage import GridVoltageModel, ConstantGridVoltageModel
from dertwin.devices.external.power_flow import SitePowerModel
from dertwin.devices.energy_meter.simulator import EnergyMeterSimulator
from dertwin.devices.external.grid_frequency import GridFrequencyModel, ConstantGridFrequencyModel


def create_meter(load_kw, pv_kw=0.0, bess_kw=0.0, seed=1):
    power_model = SitePowerModel(
        base_load_supplier=lambda t: load_kw,
        pv_supplier=lambda: pv_kw,
        bess_supplier=lambda: bess_kw,
    )
    grid_model = GridFrequencyModel(seed=seed)
    voltage_model = GridVoltageModel(seed=seed)
    meter = EnergyMeterSimulator(
        power_model=power_model,
        grid_model=grid_model,
        grid_voltage_model=voltage_model,
        seed=seed,
    )
    return meter, power_model


def create_constant_meter(load_kw, pv_kw=0.0, bess_kw=0.0):
    power_model = SitePowerModel(
        base_load_supplier=lambda t: load_kw,
        pv_supplier=lambda: pv_kw,
        bess_supplier=lambda: bess_kw,
    )
    meter = EnergyMeterSimulator(
        power_model=power_model,
        grid_model=ConstantGridFrequencyModel(50.0),
        grid_voltage_model=ConstantGridVoltageModel(400.0),
        seed=1,
    )
    return meter, power_model


# --------------------------------------------------
# Import scenario
# --------------------------------------------------

def test_import_energy_accumulates():
    meter, power_model = create_meter(load_kw=10.0)

    power_model.update(3600)
    meter.update(3600)

    telemetry = meter.get_telemetry()

    assert telemetry.total_active_power == 10.0
    assert pytest.approx(telemetry.total_import_energy, 0.001) == 10.0
    assert telemetry.total_export_energy == 0.0


# --------------------------------------------------
# Export scenario
# --------------------------------------------------

def test_export_energy_accumulates():
    meter, power_model = create_meter(load_kw=5.0, pv_kw=15.0)

    power_model.update(3600)
    meter.update(3600)

    telemetry = meter.get_telemetry()

    assert telemetry.total_active_power == -10.0
    assert pytest.approx(telemetry.total_export_energy, 0.001) == 10.0
    assert telemetry.total_import_energy == 0.0


# --------------------------------------------------
# Balanced scenario
# --------------------------------------------------

def test_zero_grid_flow():
    meter, power_model = create_meter(load_kw=5.0, pv_kw=5.0)

    power_model.update(3600)
    meter.update(3600)

    telemetry = meter.get_telemetry()

    assert pytest.approx(telemetry.total_active_power, 0.001) == 0.0
    assert telemetry.total_import_energy == 0.0
    assert telemetry.total_export_energy == 0.0


# --------------------------------------------------
# Accumulation over multiple steps
# --------------------------------------------------

def test_energy_accumulates_over_multiple_updates():
    meter, power_model = create_meter(load_kw=2.0)

    power_model.update(3600)
    meter.update(1800)
    meter.update(1800)

    telemetry = meter.get_telemetry()

    assert pytest.approx(telemetry.total_import_energy, 0.001) == 2.0


# --------------------------------------------------
# Sign convention
# --------------------------------------------------

def test_sign_convention_export_negative():
    meter, power_model = create_meter(load_kw=3.0, pv_kw=8.0)

    power_model.update(1)
    meter.update(1)

    assert meter.get_telemetry().total_active_power < 0


# --------------------------------------------------
# Energy monotonicity
# --------------------------------------------------

def test_import_energy_never_decreases():
    meter, power_model = create_meter(load_kw=10.0)

    values = []
    for _ in range(10):
        power_model.update(360)
        meter.update(360)
        values.append(meter.get_telemetry().total_import_energy)

    assert all(values[i] <= values[i + 1] for i in range(len(values) - 1))


def test_export_energy_never_decreases():
    meter, power_model = create_meter(load_kw=2.0, pv_kw=10.0)

    values = []
    for _ in range(10):
        power_model.update(360)
        meter.update(360)
        values.append(meter.get_telemetry().total_export_energy)

    assert all(values[i] <= values[i + 1] for i in range(len(values) - 1))


# --------------------------------------------------
# BESS contribution reduces import
# --------------------------------------------------

def test_bess_discharge_reduces_import():
    meter_no_bess, pm_no_bess = create_constant_meter(load_kw=10.0)
    meter_with_bess, pm_with_bess = create_constant_meter(load_kw=10.0, bess_kw=5.0)

    for pm in (pm_no_bess, pm_with_bess):
        pm.update(3600)

    meter_no_bess.update(3600)
    meter_with_bess.update(3600)

    import_no_bess = meter_no_bess.get_telemetry().total_import_energy
    import_with_bess = meter_with_bess.get_telemetry().total_import_energy

    assert import_with_bess < import_no_bess


# --------------------------------------------------
# Frequency and voltage passthrough
# --------------------------------------------------

def test_frequency_reflected_in_telemetry():
    meter, power_model = create_constant_meter(load_kw=5.0)

    power_model.update(1)
    meter.update(1)

    assert meter.get_telemetry().grid_frequency == pytest.approx(50.0, abs=1e-6)


def test_voltage_ln_reflected_in_telemetry():
    meter, power_model = create_constant_meter(load_kw=5.0)

    power_model.update(1)
    meter.update(1)

    expected_ln = 400.0 / math.sqrt(3.0)
    t = meter.get_telemetry()

    assert t.phase_voltage_a == pytest.approx(expected_ln, rel=1e-6)
    assert t.phase_voltage_b == pytest.approx(expected_ln, rel=1e-6)
    assert t.phase_voltage_c == pytest.approx(expected_ln, rel=1e-6)


# --------------------------------------------------
# Passive — ignores commands
# --------------------------------------------------

def test_meter_ignores_commands():
    meter, power_model = create_meter(load_kw=7.0)

    power_model.update(3600)
    meter.apply_commands({"active_power_setpoint": 999, "start_stop_standby": 1})
    meter.update(3600)

    assert meter.get_telemetry().total_active_power == pytest.approx(7.0, abs=0.01)


def test_init_applied_commands_returns_empty():
    meter, _ = create_meter(load_kw=5.0)
    result = meter.init_applied_commands({"any_command": 1})
    assert result == {}


# --------------------------------------------------
# Telemetry structure
# --------------------------------------------------

def test_telemetry_contains_all_fields():
    meter, power_model = create_meter(load_kw=5.0)

    power_model.update(1)
    meter.update(1)

    d = meter.get_telemetry().to_dict()

    expected = {
        "total_active_power",
        "total_reactive_power",
        "total_power_factor",
        "grid_frequency",
        "phase_voltage_a",
        "phase_voltage_b",
        "phase_voltage_c",
        "phase_active_power_a",
        "phase_active_power_b",
        "phase_active_power_c",
        "total_import_energy",
        "total_export_energy",
    }

    assert expected.issubset(d.keys())


def test_zero_telemetry_before_first_update():
    """get_telemetry() before any update must return safe zero state."""
    meter, _ = create_meter(load_kw=10.0)
    t = meter.get_telemetry()
    assert t.total_active_power == 0.0
    assert t.total_import_energy == 0.0
    assert t.total_export_energy == 0.0