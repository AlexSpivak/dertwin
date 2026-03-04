import asyncio
import math
from pathlib import Path

import pytest
from pymodbus.client import AsyncModbusTcpClient

from dertwin.controllers.site_controller import SiteController
from dertwin.core.registers import RegisterMap
from dertwin.devices.energy_meter.simulator import EnergyMeterSimulator
from dertwin.devices.external.grid_frequency import FrequencyEvent
from dertwin.devices.external.grid_voltage import VoltageEvent
from dertwin.devices.pv.simulator import PVSimulator
from dertwin.telemetry.energy_meter import EnergyMeterTelemetry

TEST_CONFIG = {
    "site_name": "integration-test-site",
    "step": 0.1,
    "real_time": False,
    "register_map_root": "configs/register_maps",
    "assets": [
        {
            "type": "bess",
            "protocols": [
                {
                    "kind": "modbus_tcp",
                    "ip": "127.0.0.1",
                    "port": 55001,
                    "unit_id": 1,
                    "register_map": "bess_modbus.yaml",
                }
            ],
        },
        {
            "type": "energy_meter",
            "protocols": [
                {
                    "kind": "modbus_tcp",
                    "ip": "127.0.0.1",
                    "port": 55002,
                    "unit_id": 1,
                    "register_map": "energy_meter_modbus.yaml",
                }
            ],
        },
        {
            "type": "inverter",
            "protocols": [
                {
                    "kind": "modbus_tcp",
                    "ip": "127.0.0.1",
                    "port": 55003,
                    "unit_id": 1,
                    "register_map": "pv_inverter_modbus.yaml",
                }
            ],
        },
    ],
}


# ==========================================================
# HELPERS
# ==========================================================


async def wait_until_server_ready(port: int):
    for _ in range(30):
        client = AsyncModbusTcpClient("127.0.0.1", port=port)
        if await client.connect():
            client.close()
            return
        await asyncio.sleep(0.05)
    raise RuntimeError(f"Server on port {port} did not start")


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


async def run_steps(site, steps: int):
    for _ in range(steps):
        await site.engine.step_once()

def get_controller(site, prefix: str):
    return next(
        c for c in site.controllers
        if c.device.__class__.__name__.lower().startswith(prefix)
    )


# ==========================================================
# FULL INTEGRATION TEST WITH DISABLED EXTERNAL MODELS
# ==========================================================


@pytest.mark.asyncio
async def test_full_site_modbus_telemetry():
    project_root = Path(__file__).resolve().parent.parent.parent
    if "register_map_root" in TEST_CONFIG:
        register_map_root = Path(TEST_CONFIG["register_map_root"])
        if not register_map_root.is_absolute():
            register_map_root = project_root / register_map_root
        TEST_CONFIG["register_map_root"] = str(register_map_root.resolve())

    site = SiteController(TEST_CONFIG)
    site.build()

    site_task = asyncio.create_task(site.start())

    try:
        await wait_until_server_ready(55001)
        await wait_until_server_ready(55002)
        await wait_until_server_ready(55003)

        # Let simulation settle
        await run_steps(site, 5)
        register_map_root = project_root / Path(TEST_CONFIG["register_map_root"])

        # ==========================================================
        # VERIFY ALL READ REGISTERS FOR ALL ASSETS
        # ==========================================================
        assets: list[dict] = TEST_CONFIG["assets"]
        for controller, asset in zip(site.controllers, assets):

            proto = asset["protocols"][0]
            port = proto["port"]

            client = AsyncModbusTcpClient("127.0.0.1", port=port)
            await client.connect()

            register_map = RegisterMap.from_yaml(
                register_map_root / proto["register_map"]
            )

            for r in register_map.reads:

                response = await client.read_input_registers(
                    address=r.address,
                    count=r.count,
                )

                assert not response.isError()

                raw = decode_registers(response.registers, r)

                device_value = controller.device.get_telemetry().to_dict().get(r.name)

                if device_value is None:
                    continue

                expected_raw = int(device_value / r.scale)
                assert raw == expected_raw

            client.close()

        # ==========================================================
        # DYNAMIC TEST — BESS CHARGE / DISCHARGE
        # ==========================================================

        bess_controller = next(
            c for c in site.controllers if c.device.__class__.__name__.lower().startswith("bess")
        )

        bess_client = AsyncModbusTcpClient("127.0.0.1", port=55001)
        await bess_client.connect()

        # Initial SOC
        initial_soc = bess_controller.device.get_telemetry().system_soc

        # Command discharge 50 kW
        value = int(50 / 0.1)  # scale 0.1
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

        # ==========================================================
        # DYNAMIC TEST — PV PRODUCTION & ENERGY ACCUMULATION
        # ==========================================================

        pv_controller = next(
            c for c in site.controllers
            if c.device.__class__.__name__.lower().startswith("pv")
        )

        pv_device: PVSimulator = pv_controller.device

        # Inject irradiance directly (site simulation driver responsibility)
        pv_device.set_irradiance(1000.0)

        # Run simulation for some time
        await run_steps(site, 200)

        telemetry = pv_device.get_telemetry()

        # PV should be producing
        assert telemetry.total_active_power > 0.0
        assert telemetry.total_active_power <= pv_device.rated_power_w

        initial_energy = telemetry.today_output_energy

        # Run longer to accumulate energy
        await run_steps(site, 2000)

        telemetry_after = pv_device.get_telemetry()
        new_energy = telemetry_after.today_output_energy

        # Energy must increase
        assert new_energy > initial_energy

        # ==========================================================
        # DYNAMIC TEST — ENERGY METER RESPONSE
        # ==========================================================

        em_controller = next(
            c for c in site.controllers
            if c.device.__class__.__name__.lower().startswith("energy")
        )

        em_device = em_controller.device

        baseline: EnergyMeterTelemetry = em_device.get_telemetry()
        baseline_import = baseline.total_import_energy
        baseline_export = baseline.total_export_energy

        # ----------------------------------------------------------
        # Strong PV production → expect export
        # ----------------------------------------------------------

        pv_device.set_irradiance(1000.0)

        await run_steps(site, 2000)

        telemetry_export: EnergyMeterTelemetry = em_device.get_telemetry()

        assert telemetry_export.total_active_power <= 0.0
        assert telemetry_export.total_export_energy > baseline_export

        export_after = telemetry_export.total_export_energy

        # ----------------------------------------------------------
        # Force import by stopping PV
        # ----------------------------------------------------------

        pv_device.set_irradiance(0.0)

        await run_steps(site, 2000)

        telemetry_import: EnergyMeterTelemetry = em_device.get_telemetry()

        assert telemetry_import.total_active_power >= 0.0
        assert telemetry_import.total_import_energy > baseline_import

        # Ensure export did not decrease (monotonic accumulation)
        assert telemetry_import.total_export_energy >= export_after

        # ==========================================================
        # DETERMINISTIC GRID MODEL TEST
        # ==========================================================

        em_controller = next(
            c for c in site.controllers
            if c.device.__class__.__name__.lower().startswith("energy")
        )

        em_device = em_controller.device

        telemetry: EnergyMeterTelemetry = em_device.get_telemetry()

        # Frequency must be deterministic
        assert telemetry.grid_frequency == pytest.approx(50.0, abs=1e-6)

        # Voltage must be deterministic
        expected_ln = 400.0 / math.sqrt(3.0)

        assert telemetry.phase_voltage_a == pytest.approx(expected_ln, abs=1e-6)
        assert telemetry.phase_voltage_b == pytest.approx(expected_ln, abs=1e-6)
        assert telemetry.phase_voltage_c == pytest.approx(expected_ln, abs=1e-6)

        # Verify stability across steps
        await run_steps(site, 100)

        telemetry2: EnergyMeterTelemetry = em_device.get_telemetry()

        assert telemetry2.grid_frequency == pytest.approx(50.0, abs=1e-6)
        assert telemetry2.phase_voltage_a == pytest.approx(expected_ln, abs=1e-6)
        assert telemetry2.phase_voltage_b == pytest.approx(expected_ln, abs=1e-6)
        assert telemetry2.phase_voltage_c == pytest.approx(expected_ln, abs=1e-6)

    finally:
        await site.stop()
        site_task.cancel()
        try:
            await site_task
        except asyncio.CancelledError:
            pass

# ==========================================================
# CONFIG WITH EXTERNAL MODELS ENABLED
# ==========================================================

TEST_CONFIG_EXTERNAL = {
    "site_name": "integration-test-site-external-models",
    "step": 0.1,
    "real_time": False,
    "register_map_root": "configs/register_maps",

    # external models config
    "external_models": {

        "power": {
            "base_load_w": 10000.0,
        },

        "irradiance": {
            "peak": 1000.0,
            "sunrise": 6.0,
            "sunset": 18.0,
        },

        "ambient_temperature": {
            "mean": 25.0,
            "amplitude": 10.0,
            "peak_hour": 15.0,
        },

        "grid_frequency": {
            "nominal_hz": 50.0,
            "noise_std": 0.002,
            "drift_std": 0.0002,
            "seed": 42,
        },

        "grid_voltage": {
            "nominal_v_ll": 400.0,
            "noise_std": 0.5,
            "drift_std": 0.05,
            "seed": 42,
        },
    },

    "assets": [
        {
            "type": "bess",
            "protocols": [
                {
                    "kind": "modbus_tcp",
                    "ip": "127.0.0.1",
                    "port": 55101,
                    "unit_id": 1,
                    "register_map": "bess_modbus.yaml",
                }
            ],
        },
        {
            "type": "energy_meter",
            "protocols": [
                {
                    "kind": "modbus_tcp",
                    "ip": "127.0.0.1",
                    "port": 55102,
                    "unit_id": 1,
                    "register_map": "energy_meter_modbus.yaml",
                }
            ],
        },
        {
            "type": "inverter",
            "protocols": [
                {
                    "kind": "modbus_tcp",
                    "ip": "127.0.0.1",
                    "port": 55103,
                    "unit_id": 1,
                    "register_map": "pv_inverter_modbus.yaml",
                }
            ],
        },
    ],
}

# ==========================================================
# MAIN TEST
# ==========================================================


@pytest.mark.asyncio
async def test_external_models_full_integration():

    project_root = Path(__file__).resolve().parent.parent.parent

    register_map_root = Path(TEST_CONFIG_EXTERNAL["register_map_root"])
    if not register_map_root.is_absolute():
        register_map_root = project_root / register_map_root

    TEST_CONFIG_EXTERNAL["register_map_root"] = str(register_map_root.resolve())

    site = SiteController(TEST_CONFIG_EXTERNAL)
    site.build()
    site.engine.clock.time = 12 * 3600  # set clock to noon

    site_task = asyncio.create_task(site.start())

    try:

        await wait_until_server_ready(55101)
        await wait_until_server_ready(55102)
        await wait_until_server_ready(55103)

        await run_steps(site, 50)

        pv: PVSimulator = get_controller(site, "pv").device
        meter: EnergyMeterSimulator = get_controller(site, "energy").device

        external = site.external_models

        # ==========================================================
        # TEST 1 — IRRADIANCE MODEL DRIVES PV OUTPUT
        # ==========================================================

        power_samples = []

        for _ in range(100):
            await run_steps(site, 1)
            power_samples.append(pv.get_telemetry().total_active_power)

        assert max(power_samples) > 0.0
        assert max(power_samples) <= pv.rated_power_w

        site.engine.clock.reset()
        for _ in range(100):
            await run_steps(site, 1)
            power_samples.append(pv.get_telemetry().total_active_power)

        # Should have both day and night values
        assert min(power_samples) == pytest.approx(0.0, abs=1e-3)

        # ==========================================================
        # TEST 2 — AMBIENT TEMPERATURE MODEL PROPAGATES
        # ==========================================================
        site.engine.clock.time = 15 * 3600  # set clock to expected day max temperature
        temps = []

        for _ in range(100):
            await run_steps(site, 1)
            temps.append(external.ambient_temperature_model.get_temperature())

        assert max(temps) > TEST_CONFIG_EXTERNAL["external_models"]["ambient_temperature"]["mean"]

        site.engine.clock.reset() # reset back to midnight
        for _ in range(100):
            await run_steps(site, 1)
            temps.append(external.ambient_temperature_model.get_temperature())
        assert min(temps) < TEST_CONFIG_EXTERNAL["external_models"]["ambient_temperature"]["mean"]

        # temperature must vary smoothly
        assert (max(temps) - min(temps)) > 5.0

        # ==========================================================
        # TEST 3 — GRID FREQUENCY EVENT RESPONSE
        # ==========================================================

        freq_model = external.grid_frequency_model

        baseline = freq_model.get_frequency()

        freq_model.add_event(
            FrequencyEvent(
                start_time=site.engine.sim_time + 5.0,
                duration=1000.0,
                delta_hz=-0.5,
                shape="step",
            )
        )

        await run_steps(site, 200)

        freq_after = freq_model.get_frequency()

        assert freq_after < baseline - 0.1

        # ==========================================================
        # TEST 4 — GRID VOLTAGE EVENT RESPONSE
        # ==========================================================

        voltage_model = external.grid_voltage_model

        baseline_voltage = voltage_model.get_voltage_ll()

        voltage_model.add_event(
            VoltageEvent(
                start_time=site.engine.sim_time + 5.0,
                duration=1000.0,
                delta_v=-0.1,
                shape="step",
            )
        )

        await run_steps(site, 200)

        sag_voltage = voltage_model.get_voltage_ll()

        assert sag_voltage < baseline_voltage * 0.95

        # ==========================================================
        # TEST 5 — ENERGY METER EXPORT DURING PV PEAK
        # ==========================================================
        site.engine.clock.time = 12 * 3600  # set clock to noon
        export_before = meter.get_telemetry().total_export_energy

        await run_steps(site, 5000)

        export_after = meter.get_telemetry().total_export_energy

        assert export_after > export_before

        site.clock.reset() # reset clock to midnight
        # ==========================================================
        # TEST 6 — IMPORT DURING LOW IRRADIANCE
        # ==========================================================

        import_before = meter.get_telemetry().total_import_energy

        # fast-forward to night
        await run_steps(site, 5000)

        import_after = meter.get_telemetry().total_import_energy

        assert import_after > import_before

        # ==========================================================
        # TEST 7 — VOLTAGE AND FREQUENCY REMAIN WITHIN SAFE LIMITS
        # ==========================================================

        freq = freq_model.get_frequency()
        voltage = voltage_model.get_voltage_ll()

        assert 45.0 <= freq <= 55.0
        assert 300.0 <= voltage <= 480.0

    finally:

        await site.stop()

        site_task.cancel()

        try:
            await site_task
        except asyncio.CancelledError:
            pass