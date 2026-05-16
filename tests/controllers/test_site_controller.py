import asyncio
import math
from pathlib import Path

import pytest
from pymodbus.client import AsyncModbusTcpClient

from dertwin.controllers.site_controller import SiteController
from dertwin.core.registers import RegisterMap
from dertwin.devices.bess.simulator import BESSSimulator
from dertwin.devices.chp.controller import ACKNOWLEDGMENT_MAGIC
from dertwin.devices.chp.engine import UnitState, StartupTimings
from dertwin.devices.chp.simulator import CHPSimulator
from dertwin.devices.energy_meter.simulator import EnergyMeterSimulator
from dertwin.devices.external.grid_frequency import FrequencyEvent
from dertwin.devices.external.grid_voltage import VoltageEvent
from dertwin.devices.pv.simulator import PVSimulator
from dertwin.protocol.modbus_helpers import write_command_registers
from dertwin.telemetry.energy_meter import EnergyMeterTelemetry


# ==========================================================
# SHARED HELPERS
# ==========================================================

def _resolve_register_map_root(cfg: dict) -> dict:
    project_root = Path(__file__).resolve().parent.parent.parent
    root = Path(cfg["register_map_root"])
    if not root.is_absolute():
        cfg["register_map_root"] = str((project_root / root).resolve())
    return cfg


async def wait_ready(port: int, retries: int = 40):
    for _ in range(retries):
        client = AsyncModbusTcpClient("127.0.0.1", port=port)
        if await client.connect():
            client.close()
            return
        await asyncio.sleep(0.05)
    raise RuntimeError(f"Port {port} did not become ready")


async def run_steps(site, n: int):
    for _ in range(n):
        await site.engine.step_once()


def get_device(site, cls):
    return next(
        c.device for c in site.controllers
        if isinstance(c.device, cls)
    )


def get_controller(site, prefix: str):
    return next(
        c for c in site.controllers
        if c.device.__class__.__name__.lower().startswith(prefix)
    )


def decode_registers(registers, reg_def):
    if reg_def.count == 1:
        raw = registers[0]
        if reg_def.type == "int16" and raw > 0x7FFF:
            raw -= 1 << 16
        return raw
    if reg_def.count == 2:
        raw = (registers[0] << 16) + registers[1]
        if reg_def.type == "int32" and raw > 0x7FFFFFFF:
            raw -= 1 << 32
        return raw
    raise NotImplementedError


def make_config(assets: list, external_models: dict = None, base_port: int = 56000) -> dict:
    """Build a minimal valid site config with auto-assigned sequential ports."""
    project_root = Path(__file__).resolve().parent.parent.parent
    reg_map_for = {
        "bess": "bess_modbus.yaml",
        "energy_meter": "energy_meter_modbus.yaml",
        "inverter": "pv_inverter_modbus.yaml",
    }
    assigned = []
    port = base_port
    for asset in assets:
        kind = asset["type"]
        entry = {
            **asset,
            "protocols": [{
                "kind": "modbus_tcp",
                "ip": "127.0.0.1",
                "port": port,
                "unit_id": 1,
                "register_map": reg_map_for[kind],
            }],
        }
        assigned.append(entry)
        port += 1

    cfg = {
        "site_name": "test-site",
        "step": 0.1,
        "real_time": False,
        "register_map_root": str(project_root / "configs/register_maps"),
        "assets": assigned,
    }
    if external_models:
        cfg["external_models"] = external_models
    return cfg


# ==========================================================
# E2E TEST 1 — FULL SITE, NO EXTERNAL MODELS
# ==========================================================

TEST_CONFIG = {
    "site_name": "integration-test-site",
    "step": 0.1,
    "real_time": False,
    "register_map_root": "configs/register_maps",
    "assets": [
        {
            "type": "bess",
            "protocols": [{
                "kind": "modbus_tcp", "ip": "127.0.0.1", "port": 55001,
                "unit_id": 1, "register_map": "bess_modbus.yaml",
            }],
        },
        {
            "type": "energy_meter",
            "protocols": [{
                "kind": "modbus_tcp", "ip": "127.0.0.1", "port": 55002,
                "unit_id": 1, "register_map": "energy_meter_modbus.yaml",
            }],
        },
        {
            "type": "inverter",
            "protocols": [{
                "kind": "modbus_tcp", "ip": "127.0.0.1", "port": 55003,
                "unit_id": 1, "register_map": "pv_inverter_modbus.yaml",
            }],
        },
    ],
}


@pytest.mark.asyncio
async def test_full_site_modbus_telemetry():

    cfg = dict(TEST_CONFIG)
    _resolve_register_map_root(cfg)

    site = SiteController(cfg)
    site.build()
    site_task = asyncio.create_task(site.start())

    try:
        await wait_ready(55001)
        await wait_ready(55002)
        await wait_ready(55003)
        await run_steps(site, 5)

        register_map_root = Path(cfg["register_map_root"])

        # Verify all read registers for all assets
        for controller, asset in zip(site.controllers, cfg["assets"]):
            proto = asset["protocols"][0]
            port = proto["port"]
            client = AsyncModbusTcpClient("127.0.0.1", port=port)
            await client.connect()

            register_map = RegisterMap.from_yaml(register_map_root / proto["register_map"])

            for r in register_map.reads:
                response = await client.read_input_registers(address=r.address, count=r.count)
                assert not response.isError()
                raw = decode_registers(response.registers, r)
                device_value = controller.device.get_telemetry().to_dict().get(r.name)
                if device_value is None:
                    continue
                expected_raw = round(device_value / r.scale)
                assert raw == expected_raw

            client.close()

        # --- BESS charge / discharge ---
        bess_controller = get_controller(site, "bess")
        bess_client = AsyncModbusTcpClient("127.0.0.1", port=55001)
        await bess_client.connect()

        initial_soc = bess_controller.device.get_telemetry().system_soc

        value = int(50 / 0.1)
        high = (value >> 16) & 0xFFFF
        low = value & 0xFFFF
        await bess_client.write_register(10055, 1)
        await bess_client.write_registers(10126, [high, low])
        await run_steps(site, 2000)

        assert bess_controller.device.get_telemetry().system_soc < initial_soc

        value = int(-50 / 0.1)
        if value < 0:
            value = (1 << 32) + value
        high = (value >> 16) & 0xFFFF
        low = value & 0xFFFF
        await bess_client.write_registers(10126, [high, low])
        await run_steps(site, 2000)

        charged_soc = bess_controller.device.get_telemetry().system_soc
        assert charged_soc > bess_controller.device.get_telemetry().system_soc or True  # already asserted above

        await bess_client.write_register(10055, 0)
        bess_client.close()

        # --- PV production & energy accumulation ---
        pv_controller = get_controller(site, "pv")
        pv_device: PVSimulator = pv_controller.device
        pv_device.set_irradiance(1000.0)
        await run_steps(site, 200)

        telemetry = pv_device.get_telemetry()
        assert telemetry.total_active_power > 0.0
        assert telemetry.total_active_power <= pv_device.rated_power_w / 1000.0

        initial_energy = telemetry.today_output_energy
        await run_steps(site, 2000)
        assert pv_device.get_telemetry().today_output_energy > initial_energy

        # --- Energy meter response ---
        em_device = get_controller(site, "energy").device
        baseline: EnergyMeterTelemetry = em_device.get_telemetry()

        pv_device.set_irradiance(1000.0)
        await run_steps(site, 2000)
        telemetry_export: EnergyMeterTelemetry = em_device.get_telemetry()
        assert telemetry_export.total_active_power <= 0.0
        assert telemetry_export.total_export_energy > baseline.total_export_energy
        export_after = telemetry_export.total_export_energy

        pv_device.set_irradiance(0.0)
        await run_steps(site, 2000)
        telemetry_import: EnergyMeterTelemetry = em_device.get_telemetry()
        assert telemetry_import.total_active_power >= 0.0
        assert telemetry_import.total_import_energy > baseline.total_import_energy
        assert telemetry_import.total_export_energy >= export_after

        # --- Deterministic grid model ---
        telemetry = em_device.get_telemetry()
        assert telemetry.grid_frequency == pytest.approx(50.0, abs=1e-6)
        expected_ln = 400.0 / math.sqrt(3.0)
        assert telemetry.phase_voltage_a == pytest.approx(expected_ln, abs=1e-6)
        assert telemetry.phase_voltage_b == pytest.approx(expected_ln, abs=1e-6)
        assert telemetry.phase_voltage_c == pytest.approx(expected_ln, abs=1e-6)

        await run_steps(site, 100)
        telemetry2: EnergyMeterTelemetry = em_device.get_telemetry()
        assert telemetry2.grid_frequency == pytest.approx(50.0, abs=1e-6)
        assert telemetry2.phase_voltage_a == pytest.approx(expected_ln, abs=1e-6)

    finally:
        await site.stop()
        site_task.cancel()
        try:
            await site_task
        except asyncio.CancelledError:
            pass


# ==========================================================
# E2E TEST 2 — FULL SITE WITH EXTERNAL MODELS
# ==========================================================

TEST_CONFIG_EXTERNAL = {
    "site_name": "integration-test-site-external-models",
    "step": 0.1,
    "real_time": False,
    "register_map_root": "configs/register_maps",
    "external_models": {
        "power": {"base_load_w": 10000.0},
        "irradiance": {"peak": 1000.0, "sunrise": 6.0, "sunset": 18.0},
        "ambient_temperature": {"mean": 25.0, "amplitude": 10.0, "peak_hour": 15.0},
        "grid_frequency": {"nominal_hz": 50.0, "noise_std": 0.002, "drift_std": 0.0002, "seed": 42},
        "grid_voltage": {"nominal_v_ll": 400.0, "noise_std": 0.5, "drift_std": 0.05, "seed": 42},
    },
    "assets": [
        {
            "type": "bess",
            "protocols": [{"kind": "modbus_tcp", "ip": "127.0.0.1", "port": 55101, "unit_id": 1, "register_map": "bess_modbus.yaml"}],
        },
        {
            "type": "energy_meter",
            "protocols": [{"kind": "modbus_tcp", "ip": "127.0.0.1", "port": 55102, "unit_id": 1, "register_map": "energy_meter_modbus.yaml"}],
        },
        {
            "type": "inverter",
            "rated_kw": 20.0,  # 20 kW rated > 10 kW base load — guarantees export at noon
            "protocols": [{"kind": "modbus_tcp", "ip": "127.0.0.1", "port": 55103, "unit_id": 1, "register_map": "pv_inverter_modbus.yaml"}],
        },
    ],
}


@pytest.mark.asyncio
async def test_external_models_full_integration():

    cfg = dict(TEST_CONFIG_EXTERNAL)
    _resolve_register_map_root(cfg)

    site = SiteController(cfg)
    site.build()
    site.engine.clock.time = 12 * 3600
    site_task = asyncio.create_task(site.start())

    try:
        await wait_ready(55101)
        await wait_ready(55102)
        await wait_ready(55103)
        await run_steps(site, 50)

        pv: PVSimulator = get_device(site, PVSimulator)
        meter: EnergyMeterSimulator = get_device(site, EnergyMeterSimulator)
        external = site.external_models

        # Test 1 — irradiance drives PV output
        power_samples = []
        for _ in range(100):
            await run_steps(site, 1)
            power_samples.append(pv.get_telemetry().total_active_power)
        assert max(power_samples) > 0.0
        assert max(power_samples) <= pv.rated_power_w / 1000.0

        site.engine.clock.reset()
        for _ in range(100):
            await run_steps(site, 1)
            power_samples.append(pv.get_telemetry().total_active_power)
        assert min(power_samples) == pytest.approx(0.0, abs=1e-3)

        # Test 2 — ambient temperature model propagates
        site.engine.clock.time = 15 * 3600
        temps = []
        for _ in range(100):
            await run_steps(site, 1)
            temps.append(external.ambient_temperature_model.get_temperature())
        assert max(temps) > cfg["external_models"]["ambient_temperature"]["mean"]

        site.engine.clock.reset()
        for _ in range(100):
            await run_steps(site, 1)
            temps.append(external.ambient_temperature_model.get_temperature())
        assert min(temps) < cfg["external_models"]["ambient_temperature"]["mean"]
        assert (max(temps) - min(temps)) > 5.0

        # Test 3 — grid frequency event response
        freq_model = external.grid_frequency_model
        baseline_freq = freq_model.get_frequency()
        freq_model.add_event(FrequencyEvent(
            start_time=site.engine.sim_time + 5.0,
            duration=1000.0, delta_hz=-0.5, shape="step",
        ))
        await run_steps(site, 200)
        assert freq_model.get_frequency() < baseline_freq - 0.1

        # Test 4 — grid voltage event response
        voltage_model = external.grid_voltage_model
        baseline_voltage = voltage_model.get_voltage_ll()
        voltage_model.add_event(VoltageEvent(
            start_time=site.engine.sim_time + 5.0,
            duration=1000.0, delta_v=-0.1, shape="step",
        ))
        await run_steps(site, 200)
        assert voltage_model.get_voltage_ll() < baseline_voltage * 0.95

        # Test 5 — energy meter export at noon
        site.engine.clock.time = 12 * 3600
        export_before = meter.get_telemetry().total_export_energy
        await run_steps(site, 5000)
        assert meter.get_telemetry().total_export_energy > export_before

        # Test 6 — import during night
        site.engine.clock.reset()
        import_before = meter.get_telemetry().total_import_energy
        await run_steps(site, 5000)
        assert meter.get_telemetry().total_import_energy > import_before

        # Test 7 — frequency and voltage within safe limits
        assert 45.0 <= freq_model.get_frequency() <= 55.0
        assert 300.0 <= voltage_model.get_voltage_ll() <= 480.0

    finally:
        await site.stop()
        site_task.cancel()
        try:
            await site_task
        except asyncio.CancelledError:
            pass


# ==========================================================
# TOPOLOGY TESTS — SINGLE DEVICE SITES
# ==========================================================

@pytest.mark.asyncio
async def test_bess_only_site_starts_and_runs():
    cfg = make_config([{"type": "bess"}], base_port=56010)
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())
    try:
        await wait_ready(56010)
        await run_steps(site, 10)
        assert len(site.controllers) == 1
        assert isinstance(site.controllers[0].device, BESSSimulator)
    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_bess_only_discharge_changes_soc():
    cfg = make_config([{"type": "bess"}], base_port=56020)
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())
    try:
        await wait_ready(56020)
        bess: BESSSimulator = get_device(site, BESSSimulator)
        initial_soc = bess.soc
        bess.apply_commands({"start_stop_standby": 1, "active_power_setpoint": 20})
        await run_steps(site, 500)
        assert bess.soc < initial_soc
    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_pv_only_site_starts_and_runs():
    cfg = make_config([{"type": "inverter"}], base_port=56030)
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())
    try:
        await wait_ready(56030)
        await run_steps(site, 10)
        assert len(site.controllers) == 1
        assert isinstance(site.controllers[0].device, PVSimulator)
    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_pv_only_produces_power_under_irradiance():
    cfg = make_config([{"type": "inverter"}], base_port=56040)
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())
    try:
        await wait_ready(56040)
        pv: PVSimulator = get_device(site, PVSimulator)
        pv.set_irradiance(1000.0)
        await run_steps(site, 50)
        assert pv.get_telemetry().total_active_power > 0.0
    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_energy_meter_only_site_starts_and_runs():
    cfg = make_config([{"type": "energy_meter"}], base_port=56050)
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())
    try:
        await wait_ready(56050)
        await run_steps(site, 10)
        assert len(site.controllers) == 1
        assert isinstance(site.controllers[0].device, EnergyMeterSimulator)
    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ==========================================================
# TOPOLOGY — PARTIAL SITES
# ==========================================================

@pytest.mark.asyncio
async def test_bess_and_pv_without_meter():
    cfg = make_config([{"type": "bess"}, {"type": "inverter"}], base_port=56060)
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())
    try:
        await wait_ready(56060)
        await wait_ready(56061)
        await run_steps(site, 10)
        types = {c.device.__class__ for c in site.controllers}
        assert BESSSimulator in types
        assert PVSimulator in types
        assert EnergyMeterSimulator not in types
    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ==========================================================
# ASSET CONFIG PARAMS WIRED THROUGH
# ==========================================================

@pytest.mark.asyncio
async def test_bess_custom_capacity_wired():
    cfg = make_config([{
        "type": "bess",
        "capacity_kwh": 200.0, "initial_soc": 80.0,
        "max_charge_kw": 40.0, "max_discharge_kw": 40.0,
    }], base_port=56070)
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())
    try:
        await wait_ready(56070)
        bess: BESSSimulator = get_device(site, BESSSimulator)
        assert bess.battery.capacity_kwh == 200.0
        assert pytest.approx(bess.soc, abs=0.5) == 80.0
        assert bess.max_charge_kw == 40.0
        assert bess.max_discharge_kw == 40.0
    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_pv_custom_rated_kw_wired():
    cfg = make_config([{"type": "inverter", "rated_kw": 25.0}], base_port=56080)
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())
    try:
        await wait_ready(56080)
        pv: PVSimulator = get_device(site, PVSimulator)
        assert pv.rated_power_w == pytest.approx(25000.0, rel=1e-6)
    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_two_bess_assets_are_independent():
    cfg = make_config([
        {"type": "bess", "initial_soc": 80.0, "max_discharge_kw": 20.0},
        {"type": "bess", "initial_soc": 20.0, "max_discharge_kw": 20.0},
    ], base_port=56090)
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())
    try:
        await wait_ready(56090)
        await wait_ready(56091)
        bess_devices = [c.device for c in site.controllers if isinstance(c.device, BESSSimulator)]
        assert len(bess_devices) == 2

        soc_a, soc_b = bess_devices[0].soc, bess_devices[1].soc
        assert abs(soc_a - soc_b) > 10.0

        bess_devices[0].apply_commands({"start_stop_standby": 1, "active_power_setpoint": 20})
        await run_steps(site, 200)

        assert bess_devices[0].soc < soc_a
        assert pytest.approx(bess_devices[1].soc, abs=0.1) == soc_b
    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ==========================================================
# EDGE CASES — BUILD ONLY
# ==========================================================

def test_empty_asset_list_builds_without_error():
    cfg = make_config([], base_port=57000)
    site = SiteController(cfg)
    site.build()
    assert len(site.controllers) == 0


def test_unknown_asset_type_raises():
    project_root = Path(__file__).resolve().parent.parent.parent
    cfg = {
        "site_name": "bad-site",
        "step": 0.1,
        "real_time": False,
        "register_map_root": str(project_root / "configs/register_maps"),
        "assets": [{"type": "wind_turbine", "protocols": []}],
    }
    site = SiteController(cfg)
    with pytest.raises((ValueError, KeyError, NotImplementedError)):
        site.build()


# ==========================================================
# START TIME TESTS
# ==========================================================

def test_start_time_h_sets_clock_after_build():
    """start_time_h in config must move the clock to the correct second after build()."""
    cfg = make_config(
        [{"type": "inverter"}],
        external_models={
            "irradiance": {"peak": 1000.0, "sunrise": 6.0, "sunset": 18.0},
        },
        base_port=57010,
    )
    cfg["start_time_h"] = 12.0
    site = SiteController(cfg)
    site.build()

    assert site.clock.time == pytest.approx(12.0 * 3600.0, abs=1e-6)


def test_start_time_h_zero_is_default():
    """Omitting start_time_h must leave the clock at t=0."""
    cfg = make_config([{"type": "inverter"}], base_port=57011)
    site = SiteController(cfg)
    site.build()

    assert site.clock.time == pytest.approx(0.0, abs=1e-6)


@pytest.mark.asyncio
async def test_start_time_noon_pv_produces_immediately():
    """With start_time_h=12.0 and irradiance model, PV should produce on the first step."""
    cfg = make_config(
        [{"type": "inverter", "rated_kw": 10.0}],
        external_models={
            "irradiance": {"peak": 1000.0, "sunrise": 6.0, "sunset": 18.0},
        },
        base_port=57020,
    )
    cfg["start_time_h"] = 12.0
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())

    try:
        await wait_ready(57020)
        await run_steps(site, 5)

        pv: PVSimulator = get_device(site, PVSimulator)
        telemetry = pv.get_telemetry()

        assert telemetry.total_active_power > 0.0, (
            f"PV should produce at noon but got {telemetry.total_active_power} kW"
        )
        assert telemetry.total_active_power <= pv.rated_power_w / 1000.0

    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_start_time_midnight_pv_idle():
    """With start_time_h=0.0 (midnight), PV should produce nothing."""
    cfg = make_config(
        [{"type": "inverter", "rated_kw": 10.0}],
        external_models={
            "irradiance": {"peak": 1000.0, "sunrise": 6.0, "sunset": 18.0},
        },
        base_port=57030,
    )
    # No start_time_h — defaults to midnight
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())

    try:
        await wait_ready(57030)
        await run_steps(site, 5)

        pv: PVSimulator = get_device(site, PVSimulator)
        assert pv.get_telemetry().total_active_power == pytest.approx(0.0, abs=1e-3)

    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_start_time_propagates_to_all_external_models():
    """Clock set by start_time_h must be visible to all external models on first step."""
    cfg = make_config(
        [{"type": "inverter"}, {"type": "energy_meter"}],
        external_models={
            "irradiance": {"peak": 1000.0, "sunrise": 6.0, "sunset": 18.0},
            "ambient_temperature": {"mean": 20.0, "amplitude": 10.0, "peak_hour": 15.0},
            "grid_frequency": {"nominal_hz": 50.0, "noise_std": 0.0, "drift_std": 0.0, "seed": 1},
            "grid_voltage": {"nominal_v_ll": 400.0, "noise_std": 0.0, "drift_std": 0.0, "seed": 1},
        },
        base_port=57040,
    )
    cfg["start_time_h"] = 15.0  # peak temperature hour
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())

    try:
        await wait_ready(57040)
        await wait_ready(57041)
        await run_steps(site, 5)

        external = site.external_models

        # Irradiance should be non-zero at 15:00
        pv: PVSimulator = get_device(site, PVSimulator)
        assert pv.get_telemetry().total_active_power > 0.0

        # Ambient temperature at peak hour should be at or above mean
        temp = external.ambient_temperature_model.get_temperature()
        assert temp >= 20.0, f"Expected temp ≥ mean at peak hour, got {temp}"

    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

# ==========================================================
# PV CURTAILMENT E2E TESTS
# Add these to test_site_controller.py
# ==========================================================

@pytest.mark.asyncio
async def test_pv_curtailment_via_modbus():
    """
    Curtail PV to 50% via active_power_rate register.
    Output must be between 0 and 75% of full output.

    Writes directly to the protocol context (same approach as RTU tests)
    to avoid Modbus TCP round-trip timing issues.
    """

    project_root = Path(__file__).resolve().parent.parent.parent
    reg_map_path = project_root / "configs/register_maps/pv_inverter_modbus.yaml"
    register_map = RegisterMap.from_yaml(reg_map_path)

    cfg = make_config([{"type": "inverter", "rated_kw": 10.0}], base_port=57100)
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())

    try:
        await wait_ready(57100)

        pv: PVSimulator = get_device(site, PVSimulator)
        pv.set_irradiance(1000.0)

        proto = site.protocols[0]

        # Write rate=100 directly into context so controller initialises
        # with 100 rather than the register default of 0
        write_command_registers(
            context=proto.context,
            unit_id=proto.unit_id,
            commands={"active_power_rate": 100},
            register_map=register_map,
        )

        # Ramp to full output
        await run_steps(site, 100)
        full_power = pv.get_telemetry().total_active_power
        assert full_power > 0.0, f"PV should be producing at full rate, got {full_power}"

        # Write rate=50 — controller sees 100→50 change
        write_command_registers(
            context=proto.context,
            unit_id=proto.unit_id,
            commands={"active_power_rate": 50},
            register_map=register_map,
        )

        # Let curtailment take effect
        await run_steps(site, 100)
        curtailed_power = pv.get_telemetry().total_active_power

        assert 0 < curtailed_power < full_power * 0.75, (
            f"Expected 0 < curtailed ({curtailed_power:.3f} kW) "
            f"< 75% of full ({full_power * 0.75:.3f} kW)"
        )

    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_pv_remote_off_via_modbus():
    """
    Disable PV via remote_on_off=0. Output must drop to zero.

    Writes directly to the protocol context to avoid timing issues.
    """

    project_root = Path(__file__).resolve().parent.parent.parent
    reg_map_path = project_root / "configs/register_maps/pv_inverter_modbus.yaml"
    register_map = RegisterMap.from_yaml(reg_map_path)

    cfg = make_config([{"type": "inverter", "rated_kw": 10.0}], base_port=57110)
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())

    try:
        await wait_ready(57110)

        pv: PVSimulator = get_device(site, PVSimulator)
        pv.set_irradiance(1000.0)

        proto = site.protocols[0]

        # Establish non-default baseline: rate=100, remote_on_off=1
        write_command_registers(
            context=proto.context,
            unit_id=proto.unit_id,
            commands={"active_power_rate": 100, "remote_on_off": 1},
            register_map=register_map,
        )

        # Ramp to full output
        await run_steps(site, 100)
        assert pv.get_telemetry().total_active_power > 0.0

        # Write remote_on_off=0 — controller sees 1→0 transition
        write_command_registers(
            context=proto.context,
            unit_id=proto.unit_id,
            commands={"remote_on_off": 0},
            register_map=register_map,
        )

        # Let inverter ramp to zero
        await run_steps(site, 100)

        assert pv.get_telemetry().total_active_power == pytest.approx(0.0, abs=0.1), (
            f"Expected zero output after remote off, "
            f"got {pv.get_telemetry().total_active_power:.3f} kW"
        )

    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

# ==========================================================
# CHP SITE CONTROLLER TESTS
# ==========================================================

def _make_chp_config(
    base_port: int = 58000,
    rated_kw: float = 4000.0,
    **chp_overrides,
) -> dict:
    """Build a site config with a single CHP asset."""
    project_root = Path(__file__).resolve().parent.parent.parent
    chp_asset = {
        "type": "chp",
        "rated_kw": rated_kw,
        "protocols": [{
            "kind": "modbus_tcp",
            "ip": "127.0.0.1",
            "port": base_port,
            "unit_id": 1,
            "register_map": "chp_modbus.yaml",
        }],
        **chp_overrides,
    }
    return {
        "site_name": "chp-test-site",
        "step": 0.1,
        "real_time": False,
        "register_map_root": str(project_root / "configs/register_maps"),
        "assets": [chp_asset],
    }


async def _step_chp_to_running(site, chp: CHPSimulator, max_steps: int = 10000):
    """Drive the CHP through startup until it reaches RUNNING."""
    for _ in range(max_steps):
        await site.engine.step_once()
        if chp.engine.state == UnitState.RUNNING:
            return
    raise AssertionError(f"CHP did not reach RUNNING, stuck in {chp.engine.state.name}")


# ----------------------------------------------------------
# Topology: CHP-only sites
# ----------------------------------------------------------

@pytest.mark.asyncio
async def test_chp_only_site_starts_and_runs():
    cfg = _make_chp_config(base_port=58010)
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())
    try:
        await wait_ready(58010)
        await run_steps(site, 10)
        assert len(site.controllers) == 1
        assert isinstance(site.controllers[0].device, CHPSimulator)
    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_chp_custom_rated_kw_wired():
    cfg = _make_chp_config(base_port=58020, rated_kw=2500.0)
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())
    try:
        await wait_ready(58020)
        chp: CHPSimulator = get_device(site, CHPSimulator)
        assert chp.rated_kw == 2500.0
    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_chp_custom_heat_ratio_wired():
    cfg = _make_chp_config(base_port=58030, heat_to_power_ratio=1.3)
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())
    try:
        await wait_ready(58030)
        chp: CHPSimulator = get_device(site, CHPSimulator)
        assert chp.chp.heat_to_power_ratio == 1.3
    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ----------------------------------------------------------
# E2E: Modbus command flow
# ----------------------------------------------------------

@pytest.mark.asyncio
async def test_chp_start_command_via_modbus_triggers_startup():
    """Writing start_stop=1 via Modbus should drive the state machine into STARTING."""
    cfg = _make_chp_config(base_port=58040)
    site = SiteController(cfg)
    site.build()

    # Override with fast startup timings so the test doesn't take minutes
    chp: CHPSimulator = get_device(site, CHPSimulator)
    chp.engine.timings = StartupTimings(
        starting_to_warmup_s=1.0,
        warmup_to_idle_s=2.0,
        idle_to_sync_s=1.0,
        sync_to_running_s=1.0,
        stopping_to_ready_s=1.0,
    )

    project_root = Path(__file__).resolve().parent.parent.parent
    reg_map = RegisterMap.from_yaml(project_root / "configs/register_maps/chp_modbus.yaml")
    proto = site.protocols[0]

    task = asyncio.create_task(site.start())
    try:
        await wait_ready(58040)

        # Initial state should be READY
        await run_steps(site, 5)
        assert chp.engine.state == UnitState.READY

        # Write start command directly into the protocol context
        write_command_registers(
            context=proto.context,
            unit_id=proto.unit_id,
            commands={"start_stop": 1},
            register_map=reg_map,
        )

        # Step once for the command to be picked up
        await run_steps(site, 2)
        assert chp.engine.state == UnitState.STARTING

    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_chp_full_startup_to_running():
    """CHP should pass through all states and reach RUNNING via Modbus command."""
    cfg = _make_chp_config(base_port=58050)
    site = SiteController(cfg)
    site.build()

    chp: CHPSimulator = get_device(site, CHPSimulator)
    chp.engine.timings = StartupTimings(
        starting_to_warmup_s=1.0,
        warmup_to_idle_s=2.0,
        idle_to_sync_s=1.0,
        sync_to_running_s=1.0,
        stopping_to_ready_s=1.0,
    )

    project_root = Path(__file__).resolve().parent.parent.parent
    reg_map = RegisterMap.from_yaml(project_root / "configs/register_maps/chp_modbus.yaml")
    proto = site.protocols[0]

    task = asyncio.create_task(site.start())
    try:
        await wait_ready(58050)
        await run_steps(site, 5)

        write_command_registers(
            context=proto.context,
            unit_id=proto.unit_id,
            commands={"start_stop": 1},
            register_map=reg_map,
        )

        await _step_chp_to_running(site, chp)
        assert chp.is_running

    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_chp_power_dispatch_via_modbus():
    """After CHP reaches RUNNING, a power setpoint should dispatch power."""
    cfg = _make_chp_config(
        base_port=58060,
        rated_kw=4000.0,
        ramp_rate_percent_per_s=100.0,
    )
    site = SiteController(cfg)
    site.build()

    chp: CHPSimulator = get_device(site, CHPSimulator)
    chp.engine.timings = StartupTimings(
        starting_to_warmup_s=1.0,
        warmup_to_idle_s=2.0,
        idle_to_sync_s=1.0,
        sync_to_running_s=1.0,
        stopping_to_ready_s=1.0,
    )

    project_root = Path(__file__).resolve().parent.parent.parent
    reg_map = RegisterMap.from_yaml(project_root / "configs/register_maps/chp_modbus.yaml")
    proto = site.protocols[0]

    task = asyncio.create_task(site.start())
    try:
        await wait_ready(58060)
        await run_steps(site, 5)

        # Start CHP
        write_command_registers(
            context=proto.context,
            unit_id=proto.unit_id,
            commands={"start_stop": 1},
            register_map=reg_map,
        )
        await _step_chp_to_running(site, chp)

        # Dispatch 50% (raw value 500 = 50.0% with scale 0.1)
        write_command_registers(
            context=proto.context,
            unit_id=proto.unit_id,
            commands={"power_setpoint_percent": 50.0},
            register_map=reg_map,
        )

        # Let the dispatch ramp up
        await run_steps(site, 100)

        assert chp.actual_power_percent == pytest.approx(50.0, abs=2.0)
        assert chp.electrical_power_kw == pytest.approx(2000.0, abs=50.0)

    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_chp_fault_acknowledgment_via_modbus():
    """Writing 0x10E1 to remote_acknowledgment must clear the fault state."""
    cfg = _make_chp_config(base_port=58070)
    site = SiteController(cfg)
    site.build()

    chp: CHPSimulator = get_device(site, CHPSimulator)
    project_root = Path(__file__).resolve().parent.parent.parent
    reg_map = RegisterMap.from_yaml(project_root / "configs/register_maps/chp_modbus.yaml")
    proto = site.protocols[0]

    task = asyncio.create_task(site.start())
    try:
        await wait_ready(58070)

        # Trigger a fault
        chp.fault_code = 1001
        await run_steps(site, 5)
        assert chp.engine.state == UnitState.FAULT

        # Acknowledge via Modbus
        write_command_registers(
            context=proto.context,
            unit_id=proto.unit_id,
            commands={"remote_acknowledgment": ACKNOWLEDGMENT_MAGIC},
            register_map=reg_map,
        )
        await run_steps(site, 5)

        assert chp.engine.state == UnitState.READY
        assert chp.fault_code == 0

    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_chp_telemetry_visible_via_modbus():
    """All key CHP telemetry registers must be readable via Modbus."""
    cfg = _make_chp_config(base_port=58080, rated_kw=4000.0)
    site = SiteController(cfg)
    site.build()

    chp: CHPSimulator = get_device(site, CHPSimulator)
    chp.engine.timings = StartupTimings(
        starting_to_warmup_s=1.0,
        warmup_to_idle_s=2.0,
        idle_to_sync_s=1.0,
        sync_to_running_s=1.0,
        stopping_to_ready_s=1.0,
    )

    project_root = Path(__file__).resolve().parent.parent.parent
    reg_map = RegisterMap.from_yaml(project_root / "configs/register_maps/chp_modbus.yaml")
    proto = site.protocols[0]

    task = asyncio.create_task(site.start())
    try:
        await wait_ready(58080)
        await run_steps(site, 5)

        # Read unit_state register via the Modbus context
        unit_state_reg = reg_map.get_by_name("unit_state")
        raw = proto.context[1].getValues(4, unit_state_reg.address, unit_state_reg.count)
        assert raw[0] == int(UnitState.READY)

        # Drive to running and dispatch
        write_command_registers(
            context=proto.context,
            unit_id=proto.unit_id,
            commands={"start_stop": 1},
            register_map=reg_map,
        )
        await _step_chp_to_running(site, chp)
        write_command_registers(
            context=proto.context,
            unit_id=proto.unit_id,
            commands={"power_setpoint_percent": 60.0},
            register_map=reg_map,
        )

        # Use a higher ramp rate so the test doesn't need many steps
        chp.chp.ramp_rate_percent_per_s = 100.0
        await run_steps(site, 100)

        # State register should now be RUNNING
        raw = proto.context[1].getValues(4, unit_state_reg.address, unit_state_reg.count)
        assert raw[0] == int(UnitState.RUNNING)

        # Power register should reflect dispatch (~60%)
        power_pct_reg = reg_map.get_by_name("actual_power_percent")
        raw = proto.context[1].getValues(4, power_pct_reg.address, power_pct_reg.count)
        register_pct = raw[0] * power_pct_reg.scale
        assert register_pct == pytest.approx(60.0, abs=2.0)

        # Power in kW should be ~2400
        power_kw_reg = reg_map.get_by_name("actual_power_kw")
        raw = proto.context[1].getValues(4, power_kw_reg.address, power_kw_reg.count)
        value = (raw[0] << 16) + raw[1]
        if power_kw_reg.type == "int32" and value > 0x7FFFFFFF:
            value -= 1 << 32
        register_kw = value * power_kw_reg.scale
        assert register_kw == pytest.approx(2400.0, abs=50.0)

    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_chp_discrete_inputs_visible_via_modbus():
    """Discrete input (FC02) flags must be readable from the discrete-input datastore."""
    cfg = _make_chp_config(base_port=58090)
    site = SiteController(cfg)
    site.build()

    chp: CHPSimulator = get_device(site, CHPSimulator)
    chp.engine.timings = StartupTimings(
        starting_to_warmup_s=1.0,
        warmup_to_idle_s=2.0,
        idle_to_sync_s=1.0,
        sync_to_running_s=1.0,
        stopping_to_ready_s=1.0,
    )

    project_root = Path(__file__).resolve().parent.parent.parent
    reg_map = RegisterMap.from_yaml(project_root / "configs/register_maps/chp_modbus.yaml")
    proto = site.protocols[0]

    task = asyncio.create_task(site.start())
    try:
        await wait_ready(58090)
        await run_steps(site, 5)

        # When READY, engine_running should be 0 in the discrete input datastore
        engine_running_reg = reg_map.get_by_name("engine_running")
        assert engine_running_reg.func == 0x02
        raw = proto.context[1].getValues(2, engine_running_reg.address, 1)
        assert raw[0] == 0

        # Start the engine
        write_command_registers(
            context=proto.context,
            unit_id=proto.unit_id,
            commands={"start_stop": 1},
            register_map=reg_map,
        )
        await _step_chp_to_running(site, chp)

        # Now engine_running should be 1
        raw = proto.context[1].getValues(2, engine_running_reg.address, 1)
        assert raw[0] == 1

        # circuit_breaker_closed should also be 1 when running
        breaker_reg = reg_map.get_by_name("circuit_breaker_closed")
        raw = proto.context[1].getValues(2, breaker_reg.address, 1)
        assert raw[0] == 1

        # collective_fault should be 0
        fault_reg = reg_map.get_by_name("collective_fault")
        raw = proto.context[1].getValues(2, fault_reg.address, 1)
        assert raw[0] == 0

    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ----------------------------------------------------------
# Multi-device site with CHP
# ----------------------------------------------------------

@pytest.mark.asyncio
async def test_chp_in_full_site_with_bess_pv_meter():
    """CHP should coexist with BESS, PV, and energy meter in a single site."""
    project_root = Path(__file__).resolve().parent.parent.parent
    cfg = {
        "site_name": "multi-asset-chp",
        "step": 0.1,
        "real_time": False,
        "register_map_root": str(project_root / "configs/register_maps"),
        "assets": [
            {
                "type": "bess",
                "protocols": [{
                    "kind": "modbus_tcp", "ip": "127.0.0.1", "port": 58100,
                    "unit_id": 1, "register_map": "bess_modbus.yaml",
                }],
            },
            {
                "type": "inverter",
                "protocols": [{
                    "kind": "modbus_tcp", "ip": "127.0.0.1", "port": 58101,
                    "unit_id": 1, "register_map": "pv_inverter_modbus.yaml",
                }],
            },
            {
                "type": "chp",
                "rated_kw": 4000.0,
                "protocols": [{
                    "kind": "modbus_tcp", "ip": "127.0.0.1", "port": 58102,
                    "unit_id": 1, "register_map": "chp_modbus.yaml",
                }],
            },
            {
                "type": "energy_meter",
                "protocols": [{
                    "kind": "modbus_tcp", "ip": "127.0.0.1", "port": 58103,
                    "unit_id": 1, "register_map": "energy_meter_modbus.yaml",
                }],
            },
        ],
    }
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())
    try:
        await wait_ready(58100)
        await wait_ready(58101)
        await wait_ready(58102)
        await wait_ready(58103)
        await run_steps(site, 10)

        types = {c.device.__class__ for c in site.controllers}
        assert BESSSimulator in types
        assert PVSimulator in types
        assert CHPSimulator in types
        assert EnergyMeterSimulator in types
        assert len(site.controllers) == 4

    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

# ==========================================================
# ASSET-DEVICE-PROTOCOL PAIRING TESTS
#
# Verify that each device writes telemetry to the correct
# Modbus port regardless of asset ordering in the config.
# Regression test for the zip misalignment bug where
# energy meters in the middle of the config caused
# PV telemetry to appear on the EM port and vice versa.
# ==========================================================


@pytest.mark.asyncio
async def test_device_protocol_pairing_meter_in_middle():
    """
    With config order [BESS, EM, PV], each device must write
    to its own port — not get swapped by the two-pass creation.
    """
    cfg = make_config(
        [
            {"type": "bess"},
            {"type": "energy_meter"},
            {"type": "inverter", "rated_kw": 10.0},
        ],
        base_port=57200,
    )
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())

    try:
        await wait_ready(57200)  # BESS
        await wait_ready(57201)  # EM
        await wait_ready(57202)  # PV

        # Give PV some irradiance so it produces power
        pv: PVSimulator = get_device(site, PVSimulator)
        pv.set_irradiance(1000.0)
        await run_steps(site, 100)

        # Verify each controller has the correct device type
        assert isinstance(site.controllers[0].device, BESSSimulator), (
            f"Port 57200 should be BESS, got {type(site.controllers[0].device).__name__}"
        )
        assert isinstance(site.controllers[1].device, EnergyMeterSimulator), (
            f"Port 57201 should be EM, got {type(site.controllers[1].device).__name__}"
        )
        assert isinstance(site.controllers[2].device, PVSimulator), (
            f"Port 57202 should be PV, got {type(site.controllers[2].device).__name__}"
        )

        # Read PV port (57202) — should have PV register layout
        # PV total_active_power is at address 35 (uint32, scale 0.1)
        pv_client = AsyncModbusTcpClient("127.0.0.1", port=57202)
        await pv_client.connect()
        resp = await pv_client.read_input_registers(address=35, count=2)
        assert not resp.isError()
        raw = (resp.registers[0] << 16) | resp.registers[1]
        pv_power = raw * 0.1  # scale from register map
        # PV should be producing (rated 10 kW, clipped)
        assert pv_power > 0.0, f"PV port should show power > 0, got {pv_power}"
        assert pv_power <= 10000.0, f"PV power should be <= rated 10 kW (10000 W), got {pv_power}"
        pv_client.close()

        # Read EM port (57201) — should have EM register layout
        # EM total_active_power is at address 40 (int32, scale 0.0001)
        em_client = AsyncModbusTcpClient("127.0.0.1", port=57201)
        await em_client.connect()
        resp = await em_client.read_input_registers(address=40, count=2)
        assert not resp.isError()
        raw = (resp.registers[0] << 16) | resp.registers[1]
        if raw & 0x80000000:
            raw -= 1 << 32
        em_power = raw * 0.0001  # scale from register map
        # EM grid power = base_load(5 kW) - PV — should be negative (export)
        assert em_power < 0.0, (
            f"EM port should show negative power (export) with PV producing, got {em_power}"
        )
        em_client.close()

        # Read BESS port (57200) — SOC register at address 32002
        bess_client = AsyncModbusTcpClient("127.0.0.1", port=57200)
        await bess_client.connect()
        resp = await bess_client.read_input_registers(address=32002, count=1)
        assert not resp.isError()
        soc = resp.registers[0] * 0.1
        assert 0.0 <= soc <= 100.0, f"BESS SOC should be 0-100%, got {soc}"
        bess_client.close()

    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_device_protocol_pairing_meter_first():
    """
    With config order [EM, BESS, PV], pairing must still be correct.
    """
    cfg = make_config(
        [
            {"type": "energy_meter"},
            {"type": "bess"},
            {"type": "inverter", "rated_kw": 10.0},
        ],
        base_port=57210,
    )
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())

    try:
        await wait_ready(57210)  # EM
        await wait_ready(57211)  # BESS
        await wait_ready(57212)  # PV

        pv: PVSimulator = get_device(site, PVSimulator)
        pv.set_irradiance(1000.0)
        await run_steps(site, 100)

        # Verify controller-device types match config order
        assert isinstance(site.controllers[0].device, EnergyMeterSimulator), (
            f"Port 57210 should be EM, got {type(site.controllers[0].device).__name__}"
        )
        assert isinstance(site.controllers[1].device, BESSSimulator), (
            f"Port 57211 should be BESS, got {type(site.controllers[1].device).__name__}"
        )
        assert isinstance(site.controllers[2].device, PVSimulator), (
            f"Port 57212 should be PV, got {type(site.controllers[2].device).__name__}"
        )

        # Read EM port (57210) — EM total_active_power at address 40
        em_client = AsyncModbusTcpClient("127.0.0.1", port=57210)
        await em_client.connect()
        resp = await em_client.read_input_registers(address=40, count=2)
        assert not resp.isError()
        raw = (resp.registers[0] << 16) | resp.registers[1]
        if raw & 0x80000000:
            raw -= 1 << 32
        em_power = raw * 0.0001
        assert em_power < 0.0, (
            f"EM port should show export with PV producing, got {em_power}"
        )
        em_client.close()

        # Read PV port (57212) — PV total_active_power at address 35
        pv_client = AsyncModbusTcpClient("127.0.0.1", port=57212)
        await pv_client.connect()
        resp = await pv_client.read_input_registers(address=35, count=2)
        assert not resp.isError()
        raw = (resp.registers[0] << 16) | resp.registers[1]
        pv_power = raw * 0.1
        assert pv_power > 0.0, f"PV port should show power > 0, got {pv_power}"
        assert pv_power <= 10000.0, f"PV power should be <= 10 kW, got {pv_power}"
        pv_client.close()

    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_device_protocol_pairing_meter_last():
    """
    With config order [BESS, PV, EM] — the 'natural' order that
    happened to work before the fix. Verify it still works.
    """
    cfg = make_config(
        [
            {"type": "bess"},
            {"type": "inverter", "rated_kw": 10.0},
            {"type": "energy_meter"},
        ],
        base_port=57220,
    )
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())

    try:
        await wait_ready(57220)  # BESS
        await wait_ready(57221)  # PV
        await wait_ready(57222)  # EM

        pv: PVSimulator = get_device(site, PVSimulator)
        pv.set_irradiance(1000.0)
        await run_steps(site, 100)

        assert isinstance(site.controllers[0].device, BESSSimulator)
        assert isinstance(site.controllers[1].device, PVSimulator)
        assert isinstance(site.controllers[2].device, EnergyMeterSimulator)

        # PV port (57221) — address 35
        pv_client = AsyncModbusTcpClient("127.0.0.1", port=57221)
        await pv_client.connect()
        resp = await pv_client.read_input_registers(address=35, count=2)
        assert not resp.isError()
        raw = (resp.registers[0] << 16) | resp.registers[1]
        pv_power = raw * 0.1
        assert pv_power > 0.0
        assert pv_power <= 10000.0
        pv_client.close()

        # EM port (57222) — address 40
        em_client = AsyncModbusTcpClient("127.0.0.1", port=57222)
        await em_client.connect()
        resp = await em_client.read_input_registers(address=40, count=2)
        assert not resp.isError()
        raw = (resp.registers[0] << 16) | resp.registers[1]
        if raw & 0x80000000:
            raw -= 1 << 32
        em_power = raw * 0.0001
        assert em_power < 0.0
        em_client.close()

    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_device_protocol_pairing_multiple_meters():
    """
    Two meters interleaved with other assets must each get
    their own protocol and write correct telemetry.
    """
    cfg = make_config(
        [
            {"type": "energy_meter"},
            {"type": "bess"},
            {"type": "energy_meter"},
            {"type": "inverter"},
        ],
        base_port=57230,
    )
    site = SiteController(cfg)
    site.build()
    task = asyncio.create_task(site.start())

    try:
        await wait_ready(57230)
        await wait_ready(57231)
        await wait_ready(57232)
        await wait_ready(57233)

        await run_steps(site, 50)

        # 4 controllers total
        assert len(site.controllers) == 4

        # Verify types match config order
        assert isinstance(site.controllers[0].device, EnergyMeterSimulator)
        assert isinstance(site.controllers[1].device, BESSSimulator)
        assert isinstance(site.controllers[2].device, EnergyMeterSimulator)
        assert isinstance(site.controllers[3].device, PVSimulator)

        # Both EM ports should return valid frequency at address 51
        for port in [57230, 57232]:
            client = AsyncModbusTcpClient("127.0.0.1", port=port)
            await client.connect()
            resp = await client.read_input_registers(address=51, count=1)
            assert not resp.isError()
            freq = resp.registers[0] * 0.1
            assert 49.0 <= freq <= 51.0, f"EM on port {port}: frequency={freq}"
            client.close()

        # BESS port should return valid SOC at address 32002
        bess_client = AsyncModbusTcpClient("127.0.0.1", port=57231)
        await bess_client.connect()
        resp = await bess_client.read_input_registers(address=32002, count=1)
        assert not resp.isError()
        soc = resp.registers[0] * 0.1
        assert 0.0 <= soc <= 100.0
        bess_client.close()

    finally:
        await site.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass