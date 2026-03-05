import math
import pytest

from dertwin.devices.energy_meter.model import EnergyMeterModel
from dertwin.telemetry.energy_meter import EnergyMeterTelemetry


# ============================================================
# Basic measurement correctness
# ============================================================

def test_measurement_active_power_passthrough():
    model = EnergyMeterModel(seed=1)

    telemetry: EnergyMeterTelemetry = model.measure(
        grid_power_kw=10.0,
        import_energy_kwh=5.0,
        export_energy_kwh=0.0,
        grid_frequency=50.0,
        voltage_ll=400,
    )

    expected_voltage = 400 / math.sqrt(3.0)

    assert telemetry.total_active_power == 10.0
    assert telemetry.total_import_energy == 5.0
    assert telemetry.total_export_energy == 0.0
    assert telemetry.grid_frequency == 50.0
    assert pytest.approx(telemetry.phase_voltage_a, 1e-6) == expected_voltage


def test_export_scenario_passthrough():
    model = EnergyMeterModel(seed=1)

    telemetry = model.measure(
        grid_power_kw=-5.0,
        import_energy_kwh=0.0,
        export_energy_kwh=3.0,
        grid_frequency=50.0,
        voltage_ll=400,
    )

    assert telemetry.total_active_power == -5.0
    assert telemetry.total_export_energy == 3.0
    assert telemetry.total_import_energy == 0.0


def test_zero_power_measurement():
    model = EnergyMeterModel(seed=1)

    telemetry = model.measure(
        grid_power_kw=0.0,
        import_energy_kwh=0.0,
        export_energy_kwh=0.0,
        grid_frequency=50.0,
        voltage_ll=400,
    )

    assert telemetry.total_active_power == 0.0
    assert telemetry.total_reactive_power == pytest.approx(0.0, abs=1e-9)
    assert telemetry.phase_active_power_a == 0.0
    assert telemetry.phase_active_power_b == 0.0
    assert telemetry.phase_active_power_c == 0.0


# ============================================================
# Reactive power consistent with PF
# ============================================================

def test_reactive_power_matches_power_factor():
    model = EnergyMeterModel(seed=1)

    telemetry: EnergyMeterTelemetry = model.measure(
        grid_power_kw=10.0,
        import_energy_kwh=0.0,
        export_energy_kwh=0.0,
        grid_frequency=50.0,
        voltage_ll=400,
    )

    pf = telemetry.total_power_factor
    expected_q = 10.0 * math.tan(math.acos(pf))

    assert pytest.approx(telemetry.total_reactive_power, 1e-6) == expected_q


# ============================================================
# Three-phase split consistency
# ============================================================

def test_phase_power_splits_equally():
    model = EnergyMeterModel(seed=1)

    telemetry: EnergyMeterTelemetry = model.measure(
        grid_power_kw=9.0,
        import_energy_kwh=0.0,
        export_energy_kwh=0.0,
        grid_frequency=50.0,
        voltage_ll=400,
    )
    expected_voltage = 400 / math.sqrt(3.0)

    assert telemetry.phase_active_power_a == 3.0
    assert telemetry.phase_active_power_b == 3.0
    assert telemetry.phase_active_power_c == 3.0

    assert pytest.approx(telemetry.phase_voltage_a, 1e-6) == expected_voltage
    assert pytest.approx(telemetry.phase_voltage_b, 1e-6) == expected_voltage
    assert pytest.approx(telemetry.phase_voltage_c, 1e-6) == expected_voltage


def test_phase_powers_sum_to_total():
    model = EnergyMeterModel(seed=1)

    telemetry = model.measure(
        grid_power_kw=12.0,
        import_energy_kwh=0.0,
        export_energy_kwh=0.0,
        grid_frequency=50.0,
        voltage_ll=400,
    )

    phase_sum = (
        telemetry.phase_active_power_a
        + telemetry.phase_active_power_b
        + telemetry.phase_active_power_c
    )

    assert pytest.approx(phase_sum, rel=1e-6) == telemetry.total_active_power


# ============================================================
# Power factor bounds
# ============================================================

def test_power_factor_within_valid_range():
    model = EnergyMeterModel(seed=1)

    for _ in range(50):
        telemetry = model.measure(
            grid_power_kw=10.0,
            import_energy_kwh=0.0,
            export_energy_kwh=0.0,
            grid_frequency=50.0,
            voltage_ll=400,
        )
        assert 0.0 <= telemetry.total_power_factor <= 1.0


def test_power_factor_drifts_over_time():
    """PF should not be identical across many measurements — drift is expected."""
    model = EnergyMeterModel(seed=42)

    readings = [
        model.measure(
            grid_power_kw=10.0,
            import_energy_kwh=0.0,
            export_energy_kwh=0.0,
            grid_frequency=50.0,
            voltage_ll=400,
        ).total_power_factor
        for _ in range(100)
    ]

    assert max(readings) != min(readings)


def test_power_factor_is_deterministic_with_same_seed():
    """Same seed must produce identical sequence."""
    def run(seed):
        model = EnergyMeterModel(seed=seed)
        return [
            model.measure(10.0, 0.0, 0.0, 50.0, 400).total_power_factor
            for _ in range(20)
        ]

    assert run(7) == run(7)


def test_power_factor_differs_across_seeds():
    def run(seed):
        model = EnergyMeterModel(seed=seed)
        return [
            model.measure(10.0, 0.0, 0.0, 50.0, 400).total_power_factor
            for _ in range(20)
        ]

    assert run(1) != run(2)


# ============================================================
# Voltage scaling
# ============================================================

def test_voltage_ln_scales_with_ll():
    model = EnergyMeterModel(seed=1)

    for voltage_ll in (380.0, 400.0, 420.0):
        telemetry = model.measure(
            grid_power_kw=5.0,
            import_energy_kwh=0.0,
            export_energy_kwh=0.0,
            grid_frequency=50.0,
            voltage_ll=voltage_ll,
        )
        expected_ln = voltage_ll / math.sqrt(3.0)
        assert pytest.approx(telemetry.phase_voltage_a, rel=1e-6) == expected_ln
        assert pytest.approx(telemetry.phase_voltage_b, rel=1e-6) == expected_ln
        assert pytest.approx(telemetry.phase_voltage_c, rel=1e-6) == expected_ln


# ============================================================
# Telemetry structure
# ============================================================

def test_telemetry_contains_all_fields():
    model = EnergyMeterModel(seed=1)

    telemetry = model.measure(
        grid_power_kw=5.0,
        import_energy_kwh=1.0,
        export_energy_kwh=2.0,
        grid_frequency=50.0,
        voltage_ll=400,
    )

    d = telemetry.to_dict()

    expected_fields = {
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

    assert expected_fields.issubset(d.keys())