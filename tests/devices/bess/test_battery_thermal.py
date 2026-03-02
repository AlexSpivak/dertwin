import pytest
from dertwin.devices.bess.battery import BatteryModel


def test_temperature_increases_under_load():
    """
    Validate electro-thermal coupling for a 1-hour discharge.

    Model order:
        1) Energy update
        2) Current from updated SOC (via open-circuit voltage)
        3) Joule heating (I²R)
        4) Cooling to ambient
        5) Thermal capacity scaling
    """

    battery = BatteryModel(100, initial_soc=50)

    battery.temperature_c = 30.0
    initial_temp = 30.0
    ambient = battery.ambient_temp_c

    dt = 3600.0  # 1 hour
    power_kw = 20.0

    # ---------------------------------------------------------
    # ENERGY UPDATE
    # ---------------------------------------------------------
    dt_h = dt / 3600.0
    delta_kwh = -(power_kw * battery.discharge_eff * dt_h)

    new_energy = max(
        0.0,
        min(battery.capacity_kwh, battery.energy_kwh + delta_kwh),
    )

    original_energy = battery.energy_kwh
    battery.energy_kwh = new_energy

    # ---------------------------------------------------------
    # CURRENT CALCULATION (matches model)
    # I = P / Voc
    # ---------------------------------------------------------
    voc = battery.open_circuit_voltage()
    I = abs(power_kw * 1000.0 / voc)

    # ---------------------------------------------------------
    # JOULE HEATING
    # ---------------------------------------------------------
    joule = I * I * battery.internal_resistance * dt

    # ---------------------------------------------------------
    # COOLING
    # ---------------------------------------------------------
    Tdiff = max(0.0, initial_temp - ambient)
    cooling = battery.thermal_conductance_w_per_k * Tdiff * dt

    # ---------------------------------------------------------
    # TEMPERATURE UPDATE
    # ---------------------------------------------------------
    delta_T = (joule - cooling) / battery.thermal_capacity_j_per_k
    expected_temp = initial_temp + delta_T

    # Restore energy before actual step
    battery.energy_kwh = original_energy

    # ---- ACTUAL MODEL STEP ----
    battery.step(power_kw, dt)

    assert battery.temperature_c == pytest.approx(expected_temp, rel=1e-6)


def test_temperature_cools_intermediate():
    """
    Validate partial cooling over 20 minutes.

    We analytically compute:
        Q_cooling = k * (T - T_ambient) * dt
        ΔT = -Q_cooling / C_th
    since power = 0 → no Joule heating.
    """

    battery = BatteryModel(100, initial_soc=50)

    battery.temperature_c = 60.0
    initial_temp = 60.0
    ambient = battery.ambient_temp_c

    dt = 20 * 60  # 20 minutes

    # ---------------------------------------------------------
    # No load → I = 0 → no Joule heating
    # ---------------------------------------------------------
    joule_energy = 0.0

    # ---------------------------------------------------------
    # Cooling energy removed
    # ---------------------------------------------------------
    Tdiff = max(0.0, initial_temp - ambient)
    cooling_energy = battery.thermal_conductance_w_per_k * Tdiff * dt

    # ---------------------------------------------------------
    # Temperature change from cooling only
    # ---------------------------------------------------------
    delta_T = (
        joule_energy - cooling_energy
    ) / battery.thermal_capacity_j_per_k

    expected_temp = initial_temp + delta_T
    expected_temp = max(ambient, min(80.0, expected_temp))

    battery.step(0.0, dt)

    assert battery.temperature_c < initial_temp
    assert battery.temperature_c > ambient
    assert pytest.approx(battery.temperature_c, rel=1e-6) == expected_temp


def test_temperature_cools_to_ambient_long_term():
    """
    Validate asymptotic cooling.

    Over long idle time temperature should:
        - decrease
        - never go below ambient
        - approach ambient closely
    """

    battery = BatteryModel(100, initial_soc=50)

    battery.temperature_c = 60.0
    ambient = battery.ambient_temp_c

    battery.step(0.0, 3600 * 5)  # 5 hours idle

    assert battery.temperature_c < 60.0
    assert battery.temperature_c >= ambient

    # Should be extremely close to ambient
    assert pytest.approx(battery.temperature_c, rel=1e-3) == ambient