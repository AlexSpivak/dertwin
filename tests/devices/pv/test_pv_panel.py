import pytest
from dertwin.devices.pv.panel import PVArrayModel


def test_zero_irradiance_produces_zero_power():
    panel = PVArrayModel(area_m2=10)
    panel.set_irradiance(0)
    assert panel.dc_power_w() == 0.0


def test_basic_power_calculation():
    panel = PVArrayModel(area_m2=10, module_efficiency=0.2)
    panel.set_irradiance(1000)

    # At STC (25°C ambient), NOCT model raises cell temp:
    #   delta_T = (45 - 20) / 800 * 1000 = 31.25°C → cell = 56.25°C
    #   temp_factor = 1 + (-0.004 * 31.25) = 0.875
    #   power = 1000 * 10 * 0.2 * 0.875 = 1750 W
    assert panel.dc_power_w() == 1750


def test_temperature_derating():
    panel = PVArrayModel(area_m2=10, module_efficiency=0.2)
    panel.set_irradiance(1000)

    panel.set_ambient_temperature(45)
    hot_power = panel.dc_power_w()

    panel.set_ambient_temperature(10)
    cold_power = panel.dc_power_w()

    assert cold_power > hot_power


def test_power_scales_linearly_with_area():
    small = PVArrayModel(area_m2=10, module_efficiency=0.2)
    large = PVArrayModel(area_m2=20, module_efficiency=0.2)

    for p in (small, large):
        p.set_irradiance(1000)

    assert pytest.approx(large.dc_power_w(), rel=1e-6) == 2 * small.dc_power_w()


def test_power_scales_with_irradiance():
    panel = PVArrayModel(area_m2=10, module_efficiency=0.2)

    panel.set_irradiance(500)
    p500 = panel.dc_power_w()

    panel.set_irradiance(1000)
    p1000 = panel.dc_power_w()

    assert p1000 > p500


def test_negative_irradiance_clamped_to_zero():
    panel = PVArrayModel(area_m2=10)
    panel.set_irradiance(-100)
    assert panel.dc_power_w() == 0.0


def test_cell_temperature_rises_with_irradiance():
    panel = PVArrayModel(area_m2=10, ambient_temp_c=25.0)

    panel.set_irradiance(0)
    panel.dc_power_w()
    temp_dark = panel.cell_temperature_c

    panel.set_irradiance(1000)
    panel.dc_power_w()
    temp_sun = panel.cell_temperature_c

    assert temp_sun > temp_dark


def test_higher_efficiency_produces_more_power():
    low = PVArrayModel(area_m2=10, module_efficiency=0.15)
    high = PVArrayModel(area_m2=10, module_efficiency=0.22)

    for p in (low, high):
        p.set_irradiance(1000)

    assert high.dc_power_w() > low.dc_power_w()