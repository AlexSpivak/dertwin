import asyncio
import math
from pathlib import Path

import pytest
from pymodbus.client import AsyncModbusTcpClient

from dertwin.controllers.site_controller import SiteController
from dertwin.core.registers import RegisterMap
from dertwin.devices.bess.simulator import BESSSimulator
from dertwin.devices.energy_meter.simulator import EnergyMeterSimulator
from dertwin.devices.external.grid_frequency import FrequencyEvent
from dertwin.devices.external.grid_voltage import VoltageEvent
from dertwin.devices.pv.simulator import PVSimulator
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
                expected_raw = int(device_value / r.scale)
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

        new_soc = bess_controller.device.get_telemetry().system_soc

        # SOC should decrease during discharge
        assert new_soc < initial_soc

        # Now charge
        value = int(-50 / 0.1)
        if value < 0:
            value = (1 << 32) + value
        high = (value >> 16) & 0xFFFF
        low = value & 0xFFFF
        await bess_client.write_registers(10126, [high, low])
        await run_steps(site, 2000)

        charged_soc = bess_controller.device.get_telemetry().system_soc

        assert charged_soc > new_soc

        # turn off bess
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