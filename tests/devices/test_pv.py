import pytest
from dertwin.devices.pv.simulator import PVSimulator


# =========================================================
# Initial State
# =========================================================

def test_initial_state_reasonable():
    inv = PVSimulator(rated_kw=10.0)
    assert inv.rated_power_w == 10000.0
    assert inv.active_power_w == 0.0
    assert inv.today_energy_kwh == 0.0
    assert inv.lifetime_energy_kwh == 0.0
    assert inv.temperature_c >= inv.ambient_temp_c


def test_initial_fault_code_zero():
    inv = PVSimulator(rated_kw=10.0)
    assert inv.get_telemetry().fault_code == 0


# =========================================================
# Zero Irradiance
# =========================================================

def test_zero_irradiance_produces_zero_power():
    inv = PVSimulator(rated_kw=10.0)
    inv.set_irradiance(0.0)
    inv.update(dt=1.0)

    telemetry = inv.get_telemetry()
    assert telemetry.total_active_power == 0.0
    assert telemetry.inverter_status == 0


def test_zero_irradiance_no_energy_accumulation():
    inv = PVSimulator(rated_kw=10.0)
    inv.set_irradiance(0.0)
    for _ in range(3600):
        inv.update(dt=1.0)
    assert inv.today_energy_kwh == 0.0
    assert inv.lifetime_energy_kwh == 0.0


# =========================================================
# Full Irradiance Respects Rating
# =========================================================

def test_full_irradiance_respects_rating():
    inv = PVSimulator(rated_kw=10.0)
    inv.set_irradiance(1000.0)
    inv.update(dt=1.0)
    assert inv.active_power_w <= 10000.0


def test_total_active_power_in_kw_not_watts():
    """Regression: total_active_power must be kW. At 10 kW rated, value must be < 100."""
    inv = PVSimulator(rated_kw=10.0)
    inv.set_irradiance(1000.0)
    inv.update(dt=1.0)
    telemetry = inv.get_telemetry()
    # If accidentally in watts this would be ~8000+, not ~8.0
    assert 0.0 < telemetry.total_active_power < 100.0


def test_telemetry_and_active_power_w_are_consistent():
    """telemetry.total_active_power (kW) must equal active_power_w / 1000."""
    inv = PVSimulator(rated_kw=10.0)
    inv.set_irradiance(1000.0)
    inv.update(dt=1.0)
    assert inv.get_telemetry().total_active_power == pytest.approx(
        inv.active_power_w / 1000.0, rel=1e-6
    )


# =========================================================
# Energy Integration
# =========================================================

def test_energy_integrates_correctly():
    inv = PVSimulator(rated_kw=10.0)
    inv.set_irradiance(1000.0)

    for _ in range(3600):
        inv.update(1.0)

    assert inv.today_energy_kwh > 0.0
    assert inv.lifetime_energy_kwh > 0.0
    assert abs(inv.today_energy_kwh - inv.lifetime_energy_kwh) < 1e-6


def test_energy_never_decreases():
    inv = PVSimulator(rated_kw=10.0)
    inv.set_irradiance(800.0)

    values = []
    for _ in range(100):
        inv.update(dt=1.0)
        values.append(inv.today_energy_kwh)

    assert all(values[i] <= values[i + 1] for i in range(len(values) - 1))


# =========================================================
# Curtailment
# =========================================================

def test_active_power_rate_reduces_output():
    """active_power_rate curtailment must reduce AC output proportionally."""
    inv_full = PVSimulator(rated_kw=10.0)
    inv_curtailed = PVSimulator(rated_kw=10.0)

    inv_full.set_irradiance(1000.0)
    inv_curtailed.set_irradiance(1000.0)

    inv_curtailed.apply_commands({"active_power_rate": 50.0})

    # Run enough steps for both inverters to ramp to their respective limits
    for _ in range(20):
        inv_full.update(dt=1.0)
        inv_curtailed.update(dt=1.0)

    assert inv_curtailed.active_power_w < inv_full.active_power_w


def test_zero_active_power_rate_stops_output():
    inv = PVSimulator(rated_kw=10.0)
    inv.set_irradiance(1000.0)
    inv.apply_commands({"active_power_rate": 0.0})
    inv.update(dt=1.0)
    assert inv.active_power_w == pytest.approx(0.0, abs=1e-6)


# =========================================================
# Thermal Behavior
# =========================================================

def test_temperature_rises_under_load():
    inv = PVSimulator(rated_kw=10.0)
    inv.set_irradiance(1000.0)
    initial_temp = inv.temperature_c

    for _ in range(100):
        inv.update(dt=1.0)

    assert inv.temperature_c > initial_temp


def test_temperature_cools_without_power():
    inv = PVSimulator(rated_kw=10.0)
    inv.inverter.temperature_c = 60.0
    inv.set_irradiance(0.0)

    for _ in range(200):
        inv.update(dt=1.0)

    assert inv.temperature_c <= 60.0
    assert inv.temperature_c >= inv.ambient_temp_c


# =========================================================
# Grid Fault Protection
# =========================================================

def test_grid_voltage_fault_stops_production():
    inv = PVSimulator(rated_kw=10.0)
    inv.set_irradiance(1000.0)
    inv.inverter.grid_voltage = 100.0
    inv.inverter.grid_frequency = 50.0
    inv.update(dt=1.0)

    telemetry = inv.get_telemetry()
    assert telemetry.total_active_power == 0.0
    assert telemetry.fault_code != 0


def test_grid_recovery_resumes_production():
    """After grid returns to normal, output must resume."""
    inv = PVSimulator(rated_kw=10.0)
    inv.set_irradiance(1000.0)

    inv.inverter.grid_voltage = 100.0
    inv.update(dt=1.0)
    assert inv.get_telemetry().fault_code != 0

    inv.inverter.grid_voltage = 230.0
    inv.update(dt=1.0)
    assert inv.get_telemetry().fault_code == 0
    assert inv.active_power_w > 0.0


# =========================================================
# Partial Irradiance Scaling
# =========================================================

def test_partial_irradiance_scaling():
    inv = PVSimulator(rated_kw=10.0)

    inv.set_irradiance(500.0)
    inv.update(dt=1.0)
    half_power = inv.active_power_w

    inv.set_irradiance(1000.0)
    inv.update(dt=1.0)
    full_power = inv.active_power_w

    assert full_power > half_power


# =========================================================
# Deterministic Output
# =========================================================

def test_output_stable_at_thermal_equilibrium():
    """
    After reaching thermal equilibrium, power should be stable.
    (Original test assumed no temperature drift, which fails because
    the inverter heats up slightly each step.)
    """
    inv = PVSimulator(rated_kw=10.0)
    inv.set_irradiance(800.0)

    # Run long enough to approach thermal steady state
    for _ in range(5000):
        inv.update(dt=1.0)

    inv.update(dt=1.0)
    p1 = inv.active_power_w
    inv.update(dt=1.0)
    p2 = inv.active_power_w

    assert abs(p1 - p2) < 0.01  # < 0.01 W drift at equilibrium


# =========================================================
# Command Idempotency
# =========================================================

def test_apply_commands_is_idempotent():
    inv = PVSimulator()
    commands = {"active_power_rate": 80.0}

    applied1 = inv.apply_commands(commands)
    applied2 = inv.apply_commands(commands)

    # First call applies the command and returns it
    assert applied1 == {"active_power_rate": 80.0}
    # Second call with same value is a no-op — nothing changed
    assert applied2 == {}
    # Inverter state is unchanged after second call
    assert inv.inverter.active_power_rate == 80.0

def test_apply_commands_returns_applied_subset():
    """apply_commands must return only commands the device accepted."""
    inv = PVSimulator()
    result = inv.apply_commands({"active_power_rate": 70.0, "unknown_register": 99})
    assert "active_power_rate" in result
    assert "unknown_register" not in result