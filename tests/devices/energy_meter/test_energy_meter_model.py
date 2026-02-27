import math
import pytest

from dertwin.devices.energy_meter.model import EnergyMeterModel


# ============================================================
# Basic measurement correctness
# ============================================================

def test_measurement_active_power_passthrough():
    model = EnergyMeterModel(seed=1)

    telemetry = model.measure(
        grid_power_kw=10.0,
        import_energy_kwh=5.0,
        export_energy_kwh=0.0,
        grid_frequency=50.0,
        voltage_ll=400,
    )

    expected_voltage = 400 / math.sqrt(3.0)

    assert telemetry["total_active_power"] == 10.0
    assert telemetry["total_import_energy"] == 5.0
    assert telemetry["total_export_energy"] == 0.0
    assert telemetry["grid_frequency"] == 50.0
    assert pytest.approx(telemetry["phase_voltage_a"], 1e-6) == expected_voltage


# ============================================================
# Reactive power consistent with PF
# ============================================================

def test_reactive_power_matches_power_factor():
    model = EnergyMeterModel(seed=1)

    telemetry = model.measure(
        grid_power_kw=10.0,
        import_energy_kwh=0.0,
        export_energy_kwh=0.0,
        grid_frequency=50.0,
        voltage_ll=400,
    )

    pf = telemetry["total_power_factor"]
    expected_q = 10.0 * math.tan(math.acos(pf))

    assert pytest.approx(telemetry["total_reactive_power"], 1e-6) == expected_q


# ============================================================
# Three-phase split consistency
# ============================================================

def test_phase_power_splits_equally():
    model = EnergyMeterModel(seed=1)

    telemetry = model.measure(
        grid_power_kw=9.0,
        import_energy_kwh=0.0,
        export_energy_kwh=0.0,
        grid_frequency=50.0,
        voltage_ll=400,
    )
    expected_voltage = 400 / math.sqrt(3.0)

    assert telemetry["phase_active_power_a"] == 3.0
    assert telemetry["phase_active_power_b"] == 3.0
    assert telemetry["phase_active_power_c"] == 3.0

    assert pytest.approx(telemetry["phase_voltage_a"], 1e-6) == expected_voltage
    assert pytest.approx(telemetry["phase_voltage_b"], 1e-6) == expected_voltage
    assert pytest.approx(telemetry["phase_voltage_c"], 1e-6) == expected_voltage