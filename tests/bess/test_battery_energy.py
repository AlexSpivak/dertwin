import pytest
from dertwin.devices.bess.battery import BatteryModel


def test_charge_energy_increases_soc_correctly():
    battery = BatteryModel(capacity_kwh=100, initial_soc=50)

    dt = 3600  # 1 hour
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

    expected_cycles = total_energy / battery.capacity_kwh

    assert pytest.approx(battery.cycles, rel=1e-6) == expected_cycles