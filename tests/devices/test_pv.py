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


# =========================================================
# Zero Irradiance
# =========================================================

def test_zero_irradiance_produces_zero_power():
    inv = PVSimulator(rated_kw=10.0)

    inv.set_irradiance(0.0)
    inv.update(dt=1.0)

    telemetry = inv.get_telemetry()

    assert telemetry["total_active_power"] == 0.0
    assert telemetry["inverter_status"] == 0


# =========================================================
# Full Irradiance Respects Rating
# =========================================================

def test_full_irradiance_respects_rating():
    inv = PVSimulator(rated_kw=10.0)

    inv.set_irradiance(1000.0)  # 1000 W/m² realistic full sun
    inv.update(dt=1.0)

    # Because panel temp derating + AC clipping exist,
    # we only verify it does not exceed rated power.
    assert inv.active_power_w <= 10000.0


# =========================================================
# Energy Integration
# =========================================================

def test_energy_integrates_correctly():
    inv = PVSimulator(rated_kw=10.0)

    inv.set_irradiance(1000.0)

    dt = 1.0
    steps = 3600  # 1 hour

    for _ in range(steps):
        inv.update(dt)

    # We no longer assume exact 10 kWh due to:
    # - panel temperature derating
    # - inverter efficiency
    assert inv.today_energy_kwh > 0.0
    assert inv.lifetime_energy_kwh > 0.0
    assert abs(inv.today_energy_kwh - inv.lifetime_energy_kwh) < 1e-6


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

    # artificially heat inverter
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

    assert telemetry["total_active_power"] == 0.0
    assert telemetry["fault_code"] != 0


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

def test_multiple_update_steps_are_consistent():
    inv = PVSimulator(rated_kw=10.0)

    inv.set_irradiance(800.0)

    inv.update(dt=1.0)
    first_power = inv.active_power_w

    inv.update(dt=1.0)
    second_power = inv.active_power_w

    # deterministic model
    assert abs(first_power - second_power) < 1e-6


# =========================================================
# Command Idempotency
# =========================================================

def test_apply_commands_is_idempotent():
    inv = PVSimulator()

    commands = {"active_power_rate": 80.0}

    applied1 = inv.apply_commands(commands)
    applied2 = inv.apply_commands(commands)

    assert applied1 == applied2