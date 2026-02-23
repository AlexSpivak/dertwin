from dertwin.devices.pv.panel import PVArrayModel


def test_zero_irradiance_produces_zero_power():
    panel = PVArrayModel(area_m2=10)
    panel.set_irradiance(0)

    power = panel.dc_power_w()

    assert power == 0.0


def test_basic_power_calculation():
    panel = PVArrayModel(area_m2=10, module_efficiency=0.2)
    panel.set_irradiance(1000)

    power = panel.dc_power_w()

    # ------------------------------------------------------------------
    # Explanation:
    #
    # At STC (25°C cell temperature):
    #   1000 W/m² × 10 m² × 0.2 = 2000 W
    #
    # HOWEVER:
    # This model includes a NOCT-based thermal model.
    #
    # With default NOCT=45°C and ambient=25°C:
    #
    #   delta_T = (NOCT - 20) / 800 × irradiance
    #           = (45 - 20) / 800 × 1000
    #           ≈ 31.25°C
    #
    #   Cell temperature ≈ 25 + 31.25 = 56.25°C
    #
    # With temp coefficient = -0.004 / °C:
    #
    #   temp_factor = 1 + (-0.004 × 31.25)
    #               ≈ 0.875
    #
    #   Expected power ≈ 2000 × 0.875 = 1750 W
    #
    # So we validate realistic operating conditions,
    # NOT ideal STC lab conditions.
    # ------------------------------------------------------------------

    assert  power == 1750


def test_temperature_derating():
    panel = PVArrayModel(area_m2=10, module_efficiency=0.2)
    panel.set_irradiance(1000)
    panel.set_ambient_temperature(45)

    hot_power = panel.dc_power_w()

    panel.set_ambient_temperature(10)
    cold_power = panel.dc_power_w()

    assert cold_power > hot_power