import asyncio
from pathlib import Path

import pytest
from pymodbus.client import AsyncModbusTcpClient

from dertwin.controllers.site_controller import SiteController
from dertwin.core.registers import RegisterMap

TEST_CONFIG = {
    "site_name": "integration-test-site",
    "step": 0.1,
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

async def wait_until_server_ready(port: int, retries: int = 20):
    for _ in range(retries):
        try:
            client = AsyncModbusTcpClient("127.0.0.1", port=port)
            if await client.connect():
                client.close()
                return
        except Exception:
            pass
        await asyncio.sleep(0.05)
    raise RuntimeError(f"Modbus server on port {port} did not start")

@pytest.mark.asyncio
async def test_full_site_modbus_telemetry():

    site = SiteController(TEST_CONFIG)
    site.build()

    site_task = asyncio.create_task(site.start())

    try:
        await wait_until_server_ready(55001)
        await wait_until_server_ready(55002)
        await wait_until_server_ready(55003)

        # ------------------------------------------------------
        # ENERGY METER TELEMETRY TEST
        # ------------------------------------------------------

        em_client = AsyncModbusTcpClient("127.0.0.1", port=55002)
        await em_client.connect()

        response = await em_client.read_input_registers(
            address=0,
            count=4,  # total_active_power (2) + total_reactive_power (2)
        )

        assert not response.isError()
        assert len(response.registers) == 4

        em_client.close()

        # ------------------------------------------------------
        # PV INVERTER TELEMETRY TEST
        # ------------------------------------------------------

        pv_client = AsyncModbusTcpClient("127.0.0.1", port=55003)
        await pv_client.connect()

        response = await pv_client.read_input_registers(
            address=35,
            count=2,  # total_active_power
        )

        assert not response.isError()
        assert len(response.registers) == 2

        pv_client.close()

        # ------------------------------------------------------
        # BESS TELEMETRY TEST
        # ------------------------------------------------------

        bess_client = AsyncModbusTcpClient("127.0.0.1", port=55001)
        await bess_client.connect()

        response = await bess_client.read_input_registers(
            address=32000,
            count=3,  # service_voltage, current, soc
        )

        assert not response.isError()
        assert len(response.registers) == 3

        # ------------------------------------------------------
        # BESS WRITE COMMAND TEST (32-bit)
        # ------------------------------------------------------

        # Write on_grid_power_setpoint = +50 kW
        # scale 0.1 → raw = 500
        value = 500
        high = (value >> 16) & 0xFFFF
        low = value & 0xFFFF

        write_response = await bess_client.write_registers(
            address=10126,
            values=[high, low],
        )

        assert not write_response.isError()

        # Give engine one deterministic tick
        await asyncio.sleep(0.1)

        # Read back holding register
        read_back = await bess_client.read_holding_registers(
            address=10126,
            count=2,
        )

        assert not read_back.isError()
        assert read_back.registers == [high, low]


        assets = TEST_CONFIG["assets"]
        register_map_root = Path(TEST_CONFIG["register_map_root"])
        for asset in assets:
            for proto in asset["protocols"]:
                register_map = RegisterMap.from_yaml(register_map_root / Path(proto["register_map"]))
                for r in register_map.reads:
                    if asset.get("type") == "bess":
                        response = await bess_client.read_input_registers(address=r.address, count=r.count)
                        if r.count == 1:
                            register_data = response.registers[0]
                        else:
                            register_data = response.registers[1] + response.registers[0]
                        # divide simulated data on scale to get the int value similar to what we write to registers
                        simulated_data = int(site.controllers[0].device.get_telemetry().get(r.name) / r.scale)
                        if register_data != simulated_data:
                            print(f"Register data {register_data} != simulated data")
                        assert register_data == simulated_data

        bess_client.close()


    finally:
        await site.stop()
        site_task.cancel()
        try:
            await site_task
        except asyncio.CancelledError:
            pass
