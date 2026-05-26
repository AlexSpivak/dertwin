"""
Regression tests for the socket leak fixed in DERTwin 0.1.11.

Before the fix, ModbusTCPSimulator.shutdown() cancelled the server task
but did not close the underlying asyncio.Server's listening socket. The
result: after remove_asset(), the port stayed bound for tens of seconds,
and a subsequent add_asset() on the same port would fail to bind, then
clients would connect to the zombie server instead of the new one.

These tests cover the exact add → remove → add cycle that broke in the
demo runner.
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from dertwin.controllers.site_controller import SiteController


def _make_site_config(*, base_port: int) -> dict:
    return {
        "site_name": "test-socket-leak",
        "step": 0.1,
        "real_time": True,
        "register_map_root": "configs/register_maps",
        "assets": [],
    }


def _port_is_bound(host: str, port: int) -> bool:
    """Quick check: is anything listening on (host, port) right now?"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    try:
        s.connect((host, port))
        return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False
    finally:
        s.close()


async def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    """Poll `predicate()` until it returns True or timeout elapses."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


@pytest.mark.asyncio
async def test_remove_asset_releases_listening_socket():
    """After remove_asset(), the TCP port must be free for re-binding."""
    site = SiteController(_make_site_config(base_port=60080))
    site.build()
    site_task = asyncio.create_task(site.start())

    try:
        await site.add_asset({
            "asset_id": "bess-x",
            "type": "bess",
            "ip": "127.0.0.1",
            "port": 60080,
            "unit_id": 1,
        })

        ok = await _wait_until(lambda: _port_is_bound("127.0.0.1", 60080))
        assert ok, "Server never bound to port 60080"

        await site.remove_asset("bess-x")

        # Port must be released. We allow up to 2s for the close to propagate
        # through asyncio's machinery — but it should be well under that.
        released = await _wait_until(
            lambda: not _port_is_bound("127.0.0.1", 60080),
            timeout=2.0,
        )
        assert released, "Listening socket on 60080 was not released after remove_asset"
    finally:
        await site.stop()
        site_task.cancel()
        try:
            await site_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_add_remove_add_cycle_same_port():
    """Full add → remove → add cycle on the same port must work without
    'address already in use' errors and must serve a fresh device, not
    a zombie one."""
    site = SiteController(_make_site_config(base_port=60081))
    site.build()
    site_task = asyncio.create_task(site.start())

    try:
        # Add
        await site.add_asset({
            "asset_id": "bess-x",
            "type": "bess",
            "ip": "127.0.0.1",
            "port": 60081,
            "unit_id": 1,
            "initial_soc": 30.0,
        })
        await _wait_until(lambda: _port_is_bound("127.0.0.1", 60081))

        # Remove
        await site.remove_asset("bess-x")
        await _wait_until(
            lambda: not _port_is_bound("127.0.0.1", 60081),
            timeout=2.0,
        )

        # Add again with different physics
        await site.add_asset({
            "asset_id": "bess-x",
            "type": "bess",
            "ip": "127.0.0.1",
            "port": 60081,
            "unit_id": 1,
            "initial_soc": 70.0,
        })
        rebound = await _wait_until(lambda: _port_is_bound("127.0.0.1", 60081))
        assert rebound, "Re-added asset never bound its port"

        # The new device must have the new initial_soc (70%), not the old (30%).
        # We don't read the registers here (would require a modbus client); the
        # port-rebind check is the structural assertion. A higher-level
        # integration test in the EMS repo verifies SOC end-to-end.
        controller = site._controllers_by_id["bess-x"]
        assert controller.device.soc == pytest.approx(70.0, abs=0.5), (
            f"Re-added BESS has wrong SOC: expected ~70%, got {controller.device.soc}"
        )
    finally:
        await site.stop()
        site_task.cancel()
        try:
            await site_task
        except asyncio.CancelledError:
            pass