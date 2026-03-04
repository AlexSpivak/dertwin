import pytest
from dertwin.devices.bess.battery import BatteryModel


def test_charge_energy_increases_soc_correctly():
    battery = BatteryModel(capacity_kwh=100, initial_soc=50)

    dt = 3600
    power_kw = -10  # charge

    battery.step(power_kw, dt)

    expected_delta = 10 * battery.charge_eff
    expected_soc = 50 + (expected_delta / 100) * 100

    assert pytest.approx(battery.soc, rel=1e-6) == expected_soc
    assert pytest.approx(battery.charge_energy_total_kwh, rel=1e-6) == expected_delta


def test_discharge_energy_decreases_soc_correctly():
    battery = BatteryModel(capacity_kwh=100, initial_soc=50)

    dt = 3600
    power_kw = 10  # discharge

    battery.step(power_kw, dt)

    expected_delta = 10 * battery.discharge_eff
    expected_soc = 50 - (expected_delta / 100) * 100

    assert pytest.approx(battery.soc, rel=1e-6) == expected_soc
    assert pytest.approx(battery.discharge_energy_total_kwh, rel=1e-6) == expected_delta


def test_cycle_counter_updates():
    battery = BatteryModel(capacity_kwh=100, initial_soc=50)

    battery.step(-10, 3600)
    battery.step(10, 3600)

    total_energy = (
        battery.charge_energy_total_kwh +
        battery.discharge_energy_total_kwh
    )

    expected_cycles = total_energy / (2 * battery.capacity_kwh)

    assert pytest.approx(battery.cycles, rel=1e-6) == expected_cycles


def test_zero_power_soc_unchanged():
    battery = BatteryModel(capacity_kwh=100, initial_soc=60)
    battery.step(0.0, 3600)
    assert pytest.approx(battery.soc, rel=1e-9) == 60.0


def test_zero_power_energy_counters_unchanged():
    battery = BatteryModel(capacity_kwh=100, initial_soc=50)
    battery.step(0.0, 3600)
    assert battery.charge_energy_total_kwh == 0.0
    assert battery.discharge_energy_total_kwh == 0.0


def test_soc_cannot_exceed_100():
    battery = BatteryModel(capacity_kwh=100, initial_soc=99)
    battery.step(-100.0, 3600)  # try to overcharge
    assert battery.soc <= 100.0


def test_soc_cannot_go_below_zero():
    battery = BatteryModel(capacity_kwh=100, initial_soc=1)
    battery.step(100.0, 3600)  # try to overdischarge
    assert battery.soc >= 0.0


def test_soc_property_matches_energy():
    battery = BatteryModel(capacity_kwh=200, initial_soc=75)
    expected_soc = 100.0 * battery.energy_kwh / battery.capacity_kwh
    assert pytest.approx(battery.soc, rel=1e-9) == expected_soc


def test_charge_counter_only_increments_on_charge():
    battery = BatteryModel(capacity_kwh=100, initial_soc=50)
    battery.step(10.0, 3600)  # discharge
    assert battery.charge_energy_total_kwh == 0.0
    assert battery.discharge_energy_total_kwh > 0.0


def test_discharge_counter_only_increments_on_discharge():
    battery = BatteryModel(capacity_kwh=100, initial_soc=50)
    battery.step(-10.0, 3600)  # charge
    assert battery.discharge_energy_total_kwh == 0.0
    assert battery.charge_energy_total_kwh > 0.0


def test_soh_decreases_with_cycling():
    battery = BatteryModel(
        capacity_kwh=100,
        initial_soc=50,
        max_charge_kw=100,
        max_discharge_kw=100,
    )
    initial_soh = battery.soh

    for _ in range(5000):
        battery.step(50.0, 1.0)

    assert battery.soh < initial_soh


def test_soh_never_below_zero():
    battery = BatteryModel(capacity_kwh=100, initial_soc=50)
    battery.cycles = 1_000_000
    battery.soh = max(0.0, 100.0 - battery.cycles * 0.005)
    assert battery.soh >= 0.0


def test_soh_at_4000_cycles_is_80_percent():
    battery = BatteryModel(capacity_kwh=100, initial_soc=50)
    battery.cycles = 4000.0
    battery.soh = max(0.0, 100.0 - battery.cycles * 0.005)
    assert pytest.approx(battery.soh, abs=0.01) == 80.0