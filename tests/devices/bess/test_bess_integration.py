import pytest
from dertwin.devices.bess.battery import BatteryModel
from dertwin.devices.bess.inverter import InverterModel
from dertwin.devices.bess.bess import BESSModel
from dertwin.telemetry.bess import BESSTelemetry


def make_bess(
    capacity_kwh=100,
    initial_soc=50,
    max_charge_kw=50,
    max_discharge_kw=50,
    dc_voltage=1000,
):
    battery = BatteryModel(capacity_kwh, initial_soc=initial_soc)
    inverter = InverterModel(max_charge_kw, max_discharge_kw, dc_voltage)
    return BESSModel(battery, inverter)


# ------------------------------------------------------------
# SOC integration correctness
# ------------------------------------------------------------

def test_bess_discharge_reduces_soc_correctly():
    bess = make_bess(initial_soc=50)

    bess.set_power_command(10)  # discharge 10 kW
    result: BESSTelemetry = bess.step(3600)    # 1 hour

    expected_energy = 10 * bess.battery.discharge_eff  # kWh
    expected_soc = 50 - (expected_energy / bess.battery.capacity_kwh) * 100

    assert pytest.approx(result.system_soc, rel=1e-6) == expected_soc


def test_bess_charge_increases_soc_correctly():
    bess = make_bess(initial_soc=50)

    bess.set_power_command(-10)  # charge 10 kW
    result: BESSTelemetry = bess.step(3600)

    expected_energy = 10 * bess.battery.charge_eff
    expected_soc = 50 + (expected_energy / bess.battery.capacity_kwh) * 100

    assert pytest.approx(result.system_soc, rel=1e-6) == expected_soc


# ------------------------------------------------------------
# Power limits enforced
# ------------------------------------------------------------

def test_bess_respects_max_discharge_power():
    bess = make_bess(initial_soc=50, max_discharge_kw=50)

    bess.set_power_command(100)  # request beyond limit
    result: BESSTelemetry = bess.step(3600)

    # inverter should clamp
    assert result.system_soc <= 50


def test_bess_respects_max_charge_power():
    bess = make_bess(initial_soc=50, max_charge_kw=50)

    bess.set_power_command(-100)
    result: BESSTelemetry = bess.step(3600)

    assert result.active_power >= -50


# ------------------------------------------------------------
# SOC limits enforced
# ------------------------------------------------------------

def test_bess_cannot_discharge_below_zero():
    bess = make_bess(initial_soc=1)

    bess.set_power_command(50)
    result: BESSTelemetry = bess.step(3600)

    assert result.system_soc >= 0


def test_bess_cannot_charge_above_100():
    bess = make_bess(initial_soc=99)

    bess.set_power_command(-50)
    result: BESSTelemetry = bess.step(3600)

    assert result.system_soc <= 100


# ------------------------------------------------------------
# Zero command behavior
# ------------------------------------------------------------

def test_bess_zero_command_keeps_soc_constant():
    bess = make_bess(initial_soc=50)

    bess.set_power_command(0)
    result: BESSTelemetry = bess.step(3600)

    assert pytest.approx(result.system_soc, rel=1e-9) == 50


# ------------------------------------------------------------
# Inverter ramp behavior (if ramp rate enforced)
# ------------------------------------------------------------

def test_bess_power_moves_toward_target():
    bess = make_bess()

    bess.set_power_command(50)

    result1: BESSTelemetry = bess.step(1)
    result2: BESSTelemetry = bess.step(1)

    # should move toward target, not away
    assert abs(result2.active_power) >= abs(result1.active_power)


# ------------------------------------------------------------
# Telemetry completeness
# ------------------------------------------------------------

def test_bess_telemetry_contains_expected_keys():
    bess = make_bess()

    result: BESSTelemetry = bess.step(1).to_dict()

    required = [
        "service_voltage",
        "service_current",
        "system_soc",
        "battery_temperature",
        "active_power",
        "reactive_power",
        "apparent_power",
        "grid_frequency",
    ]

    for key in required:
        assert key in result


# ------------------------------------------------------------
# Telemetry physical consistency
# ------------------------------------------------------------

def test_apparent_power_consistency():
    bess = make_bess()

    bess.set_power_command(20)
    result: BESSTelemetry = bess.step(1)

    P = result.active_power
    Q = result.reactive_power
    S = result.apparent_power

    assert pytest.approx(S, rel=1e-6) == (P**2 + Q**2) ** 0.5


def test_power_relationships():
    bess = make_bess()

    bess.set_power_command(20)
    result: BESSTelemetry = bess.step(1)

    P = result.active_power
    Q = result.reactive_power
    S = result.apparent_power

    # Power triangle must hold
    assert pytest.approx(S, rel=1e-6) == (P**2 + Q**2) ** 0.5

    # PF must be valid
    if S > 0:
        pf = abs(P) / S
        assert 0 <= pf <= 1.0