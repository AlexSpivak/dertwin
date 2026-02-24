import pytest

from dertwin.devices.external.power_flow import SitePowerModel
from dertwin.devices.energy_meter.simulator import EnergyMeterSimulator
from dertwin.devices.external.grid_frequency import GridFrequencyModel


def create_meter(load_kw, pv_w=0.0, bess_w=0.0):
    power_model = SitePowerModel(
        base_load_supplier=lambda t: load_kw,
        pv_supplier=lambda: pv_w,
        bess_supplier=lambda: bess_w,
    )

    grid_model = GridFrequencyModel(seed=1)

    return EnergyMeterSimulator(
        power_model=power_model,
        grid_model=grid_model,
        seed=1,
    )


# --------------------------------------------------
# Import scenario
# --------------------------------------------------

def test_import_energy_accumulates():
    meter = create_meter(load_kw=10.0)

    meter.update(3600)  # 1 hour

    telemetry = meter.get_telemetry()

    assert telemetry["total_active_power"] == 10.0
    assert pytest.approx(telemetry["total_import_energy"], 0.001) == 10.0
    assert telemetry["total_export_energy"] == 0.0


# --------------------------------------------------
# Export scenario
# --------------------------------------------------

def test_export_energy_accumulates():
    # 5 kW load, 15 kW PV → -10 kW export
    meter = create_meter(load_kw=5.0, pv_w=15000.0)

    meter.update(3600)

    telemetry = meter.get_telemetry()

    assert telemetry["total_active_power"] == -10.0
    assert pytest.approx(telemetry["total_export_energy"], 0.001) == 10.0
    assert telemetry["total_import_energy"] == 0.0


# --------------------------------------------------
# Balanced scenario
# --------------------------------------------------

def test_zero_grid_flow():
    meter = create_meter(load_kw=5.0, pv_w=5000.0)

    meter.update(3600)

    telemetry = meter.get_telemetry()

    assert pytest.approx(telemetry["total_active_power"], 0.001) == 0.0
    assert telemetry["total_import_energy"] == 0.0
    assert telemetry["total_export_energy"] == 0.0


# --------------------------------------------------
# Accumulation over multiple steps
# --------------------------------------------------

def test_energy_accumulates_over_multiple_updates():
    meter = create_meter(load_kw=2.0)

    meter.update(1800)  # 0.5 h
    meter.update(1800)  # 0.5 h

    telemetry = meter.get_telemetry()

    assert pytest.approx(telemetry["total_import_energy"], 0.001) == 2.0


# --------------------------------------------------
# Sign convention check
# --------------------------------------------------

def test_sign_convention_export_negative():
    meter = create_meter(load_kw=3.0, pv_w=8000.0)

    meter.update(1)

    telemetry = meter.get_telemetry()

    assert telemetry["total_active_power"] < 0
