from dertwin.devices.inverter import InverterSimulator


def test_initial_state_reasonable():
    inv = InverterSimulator(rated_kw=10.0)

    assert inv.rated_power_w == 10000.0
    assert inv.irradiance_factor == 0.0
    assert inv.active_power_w == 0.0
    assert inv.today_energy_kwh == 0.0
    assert inv.lifetime_energy_kwh == 0.0
    assert inv.temperature_c >= inv.ambient_temp_c


def test_zero_irradiance_produces_zero_power():
    inv = InverterSimulator(rated_kw=10.0)

    inv.set_irradiance(0.0)
    inv.update(dt=1.0)

    telemetry = inv.get_telemetry()

    assert telemetry["total_active_power"] == 0.0
    assert telemetry["inverter_status"] == 0


def test_full_irradiance_respects_rating_and_efficiency():
    inv = InverterSimulator(rated_kw=10.0)
    inv.set_irradiance(1.0)

    inv.update(dt=1.0)

    expected_output = 10000.0 * inv.efficiency
    assert abs(inv.active_power_w - expected_output) < 1e-6


def test_efficiency_loss_matches_heat_fraction():
    inv = InverterSimulator(rated_kw=10.0)
    inv.set_irradiance(1.0)

    inv.update(dt=1.0)

    input_power = 10000.0
    output_power = inv.active_power_w
    expected_output = input_power * inv.efficiency

    assert abs(output_power - expected_output) < 1e-6


def test_energy_integrates_correctly():
    inv = InverterSimulator(rated_kw=10.0)
    inv.set_irradiance(1.0)

    dt = 1.0
    steps = 3600  # 1 hour simulated

    for _ in range(steps):
        inv.update(dt)

    # expected kWh after 1 hour at full irradiance
    expected_kwh = 10.0 * inv.efficiency
    assert abs(inv.today_energy_kwh - expected_kwh) < 0.01
    assert abs(inv.lifetime_energy_kwh - expected_kwh) < 0.01


def test_temperature_rises_under_load():
    inv = InverterSimulator(rated_kw=10.0)
    inv.set_irradiance(1.0)

    initial_temp = inv.temperature_c

    for _ in range(100):
        inv.update(dt=1.0)

    assert inv.temperature_c > initial_temp


def test_temperature_cools_without_power():
    inv = InverterSimulator(rated_kw=10.0)

    # heat it up artificially
    inv.temperature_c = 60.0
    inv.set_irradiance(0.0)

    for _ in range(200):
        inv.update(dt=1.0)

    assert inv.temperature_c <= 60.0
    assert inv.temperature_c >= inv.ambient_temp_c


def test_grid_voltage_affects_current_calculation():
    inv = InverterSimulator(rated_kw=10.0)
    inv.set_irradiance(1.0)
    inv.set_grid_conditions(voltage=200.0, frequency=50.0)

    inv.update(dt=1.0)
    telemetry = inv.get_telemetry()

    expected_current = inv.active_power_w / 200.0
    assert abs(telemetry["phase_current_1"] - expected_current) < 1e-6


def test_partial_irradiance_linear_scaling():
    inv = InverterSimulator(rated_kw=10.0)

    inv.set_irradiance(0.5)
    inv.update(dt=1.0)

    expected_output = 10000.0 * 0.5 * inv.efficiency
    assert abs(inv.active_power_w - expected_output) < 1e-6


def test_multiple_update_steps_are_consistent():
    inv = InverterSimulator(rated_kw=10.0)
    inv.set_irradiance(0.8)

    inv.update(dt=1.0)
    first_power = inv.active_power_w

    inv.update(dt=1.0)
    second_power = inv.active_power_w

    # deterministic system → same irradiance gives same power
    assert abs(first_power - second_power) < 1e-9


def test_apply_commands_is_idempotent():
    inv = InverterSimulator()

    commands = {"dummy_param": 1}
    applied1 = inv.apply_commands(commands)
    applied2 = inv.apply_commands(commands)

    assert applied1 == applied2