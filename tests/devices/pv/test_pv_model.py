import pytest
from dertwin.devices.pv.inverter import PVInverterModel
from dertwin.devices.pv.panel import PVArrayModel
from dertwin.devices.pv.pv import PVModel


def build_system(area_m2=20, rated_ac_power_w=5000.0):
    panel = PVArrayModel(area_m2=area_m2)
    inverter = PVInverterModel(rated_ac_power_w=rated_ac_power_w)
    return PVModel(panel, inverter)


def test_energy_accumulates():
    pv = build_system()
    pv.panel.set_irradiance(1000)
    for _ in range(3600):
        pv.step(1.0)
    assert pv.today_energy_kwh > 0
    assert pv.lifetime_energy_kwh > 0


def test_no_irradiance_no_energy():
    pv = build_system()
    pv.panel.set_irradiance(0)
    for _ in range(3600):
        pv.step(1.0)
    assert pv.today_energy_kwh == 0


def test_telemetry_structure():
    pv = build_system()
    pv.panel.set_irradiance(1000)
    pv.step(1.0)
    telemetry = pv.get_telemetry().to_dict()
    assert "total_active_power" in telemetry
    assert "today_output_energy" in telemetry
    assert "temp_inverter" in telemetry


def test_today_and_lifetime_energy_track_together():
    pv = build_system()
    pv.panel.set_irradiance(1000)
    for _ in range(3600):
        pv.step(1.0)
    assert pytest.approx(pv.today_energy_kwh, rel=1e-6) == pv.lifetime_energy_kwh


def test_energy_never_decreases():
    pv = build_system()
    pv.panel.set_irradiance(1000)
    energies = []
    for _ in range(100):
        pv.step(1.0)
        energies.append(pv.today_energy_kwh)
    assert all(energies[i] <= energies[i + 1] for i in range(len(energies) - 1))


def test_ac_output_does_not_exceed_dc_input():
    """Both fields are kW — inverter cannot create energy."""
    pv = build_system(area_m2=20, rated_ac_power_w=10000)
    pv.panel.set_irradiance(1000)
    telemetry = pv.step(1.0)
    assert telemetry.total_active_power <= telemetry.total_input_power


def test_active_power_does_not_exceed_rated():
    """total_active_power (kW) must not exceed rated AC power (kW)."""
    rated_kw = 5.0
    pv = build_system(area_m2=20, rated_ac_power_w=rated_kw * 1000)
    pv.panel.set_irradiance(5000)
    telemetry = pv.step(1.0)
    assert telemetry.total_active_power <= rated_kw


def test_inverter_status_active_under_load():
    pv = build_system()
    pv.panel.set_irradiance(1000)
    telemetry = pv.step(1.0)
    assert telemetry.inverter_status == 1


def test_inverter_status_inactive_at_night():
    pv = build_system()
    pv.panel.set_irradiance(0)
    for _ in range(5):
        telemetry = pv.step(1.0)
    assert telemetry.inverter_status == 0


def test_telemetry_grid_frequency_reflects_inverter():
    pv = build_system()
    pv.inverter.grid_frequency = 60.0
    pv.panel.set_irradiance(1000)
    telemetry = pv.step(1.0)
    assert telemetry.grid_frequency == 60.0


def test_fault_code_zero_under_normal_conditions():
    pv = build_system()
    pv.panel.set_irradiance(1000)
    telemetry = pv.step(1.0)
    assert telemetry.fault_code == 0


def test_total_active_power_in_kw_not_watts():
    """Regression guard: total_active_power must be kW not W.
    At 1000 W/m² with 20m² panel and 5kW inverter, output must be ~4-5 kW."""
    pv = build_system(area_m2=20, rated_ac_power_w=5000)
    pv.panel.set_irradiance(1000)
    telemetry = pv.step(1.0)
    # If accidentally in watts this would be 4000+, not ~4.0
    assert 0.0 < telemetry.total_active_power < 100.0