import asyncio
import pytest
from pymodbus.client import AsyncModbusTcpClient

from dertwin.controllers.site_controller import SiteController


TEST_CONFIG = {
    "site_name": "integration-test-site",
    "step": 0.1,
    "assets": [
        {
            "type": "bess",
            "port": 55001,
            "unit_id": 1,
        },
        {
            "type": "energy_meter",
            "port": 55002,
            "unit_id": 1,
        },
    ],
}

# TODO improve this test!
@pytest.mark.asyncio
async def test_full_site_modbus_telemetry():

    site = SiteController(TEST_CONFIG)
    site.build()

    site_task = asyncio.create_task(site.start())

    try:
        await asyncio.sleep(0.5)

        client = AsyncModbusTcpClient("127.0.0.1", port=55002)
        await client.connect()

        response = await client.read_holding_registers(
            address=0,
            count=2,
        )

        assert not response.isError()
        assert response.registers is not None
        assert len(response.registers) == 2

        client.close()

    finally:
        site.stop()
        site_task.cancel()
        try:
            await site_task
        except asyncio.CancelledError:
            pass
