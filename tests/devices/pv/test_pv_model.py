from dertwin.devices.pv.inverter import PVInverterModel
from dertwin.devices.pv.panel import PVArrayModel
from dertwin.devices.pv.pv import PVModel
from dertwin.telemetry.pv import PVTelemetry


def build_system():
    panel = PVArrayModel(area_m2=20)
    inverter = PVInverterModel(rated_ac_power_w=5000)
    return PVModel(panel, inverter)


def test_energy_accumulates():
    pv = build_system()

    pv.panel.set_irradiance(1000)

    for _ in range(3600):  # 1 hour simulation
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