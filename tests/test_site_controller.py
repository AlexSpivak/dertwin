import asyncio
from pathlib import Path

import pytest
from pymodbus.client import AsyncModbusTcpClient

from dertwin.controllers.site_controller import SiteController
from dertwin.core.registers import RegisterMap
from dertwin.devices.pv.simulator import PVSimulator

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



# ==========================================================
# FULL INTEGRATION TEST
# ==========================================================


@pytest.mark.asyncio
async def test_full_site_modbus_telemetry():
    project_root = Path(__file__).resolve().parent.parent
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

        for controller, asset in zip(site.controllers, TEST_CONFIG["assets"]):

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

                device_value = controller.device.get_telemetry().get(r.name)

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
        initial_soc = bess_controller.device.get_telemetry()["system_soc"]

        # Command discharge 50 kW
        value = int(50 / 0.1)  # scale 0.1
        high = (value >> 16) & 0xFFFF
        low = value & 0xFFFF

        await bess_client.write_register(10055, 1)
        await bess_client.write_registers(10126, [high, low])

        await run_steps(site, 2000)

        new_soc = bess_controller.device.get_telemetry()["system_soc"]

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

        charged_soc = bess_controller.device.get_telemetry()["system_soc"]

        assert charged_soc > new_soc

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
        assert telemetry["total_active_power"] > 0.0
        assert telemetry["total_active_power"] <= pv_device.rated_power_w

        initial_energy = telemetry["today_output_energy"]

        # Run longer to accumulate energy
        await run_steps(site, 2000)

        telemetry_after = pv_device.get_telemetry()
        new_energy = telemetry_after["today_output_energy"]

        # Energy must increase
        assert new_energy > initial_energy

    finally:
        await site.stop()
        site_task.cancel()
        try:
            await site_task
        except asyncio.CancelledError:
            pass
