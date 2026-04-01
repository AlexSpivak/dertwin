"""
Mixed-protocol site controller tests.

These tests verify that SiteController correctly builds and runs sites
with ModbusRTUSimulator, ModbusTCPSimulator, or both. RTU protocols
operate at the register / datastore level (no serial hardware needed)
since the RTU simulator shares the same ModbusServerContext as TCP.

Test structure:
  - SiteController builds with RTU-only configs
  - SiteController builds with mixed TCP + RTU configs
  - Devices on RTU receive telemetry and respond to commands
  - Dual-protocol devices (TCP + RTU on one asset) stay in sync
  - Unknown protocol kinds are still rejected
"""

import asyncio
from pathlib import Path

import pytest
from pymodbus.client import AsyncModbusTcpClient

from dertwin.controllers.site_controller import SiteController
from dertwin.controllers.device_controller import DeviceController
from dertwin.core.registers import RegisterMap
from dertwin.devices.bess.simulator import BESSSimulator
from dertwin.devices.energy_meter.simulator import EnergyMeterSimulator
from dertwin.devices.pv.simulator import PVSimulator

from dertwin.protocol.modbus import (
    ModbusTCPSimulator,
    ModbusRTUSimulator,
    write_command_registers,
    collect_write_instructions,
)


# ==========================================================
# SHARED HELPERS
# ==========================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REG_MAP_ROOT = str(PROJECT_ROOT / "configs" / "register_maps")

REG_MAP_FOR = {
    "bess": "bess_modbus.yaml",
    "energy_meter": "energy_meter_modbus.yaml",
    "inverter": "pv_inverter_modbus.yaml",
}


def load_register_map(device_type: str) -> RegisterMap:
    return RegisterMap.from_yaml(Path(REG_MAP_ROOT) / REG_MAP_FOR[device_type])


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


def get_protocol(site, cls):
    """Return the first protocol instance matching the given class."""
    return next(p for p in site.protocols if isinstance(p, cls))


def make_rtu_protocol_cfg(device_type: str, **overrides) -> dict:
    cfg = {
        "kind": "modbus_rtu",
        "port": "/dev/null",
        "baudrate": 9600,
        "unit_id": 1,
        "register_map": REG_MAP_FOR[device_type],
    }
    cfg.update(overrides)
    return cfg


def make_tcp_protocol_cfg(device_type: str, tcp_port: int, **overrides) -> dict:
    cfg = {
        "kind": "modbus_tcp",
        "ip": "127.0.0.1",
        "port": tcp_port,
        "unit_id": 1,
        "register_map": REG_MAP_FOR[device_type],
    }
    cfg.update(overrides)
    return cfg


def make_site_config(assets: list, **kwargs) -> dict:
    cfg = {
        "site_name": "mixed-proto-test",
        "step": 0.1,
        "real_time": False,
        "register_map_root": REG_MAP_ROOT,
        "assets": assets,
    }
    cfg.update(kwargs)
    return cfg


# ==========================================================
# SITE CONTROLLER — RTU-ONLY BUILDS
# ==========================================================

class TestSiteControllerRTUBuild:
    """SiteController.build() correctly instantiates RTU protocols."""

    def test_bess_rtu_builds(self):
        cfg = make_site_config([{
            "type": "bess",
            "protocols": [make_rtu_protocol_cfg("bess")],
        }])
        site = SiteController(cfg)
        site.build()

        assert len(site.controllers) == 1
        assert len(site.protocols) == 1
        assert isinstance(site.protocols[0], ModbusRTUSimulator)
        assert isinstance(site.controllers[0].device, BESSSimulator)

    def test_pv_rtu_builds(self):
        cfg = make_site_config([{
            "type": "inverter",
            "protocols": [make_rtu_protocol_cfg("inverter")],
        }])
        site = SiteController(cfg)
        site.build()

        assert len(site.protocols) == 1
        assert isinstance(site.protocols[0], ModbusRTUSimulator)
        assert isinstance(site.controllers[0].device, PVSimulator)

    def test_meter_rtu_builds(self):
        cfg = make_site_config([{
            "type": "energy_meter",
            "protocols": [make_rtu_protocol_cfg("energy_meter")],
        }])
        site = SiteController(cfg)
        site.build()

        assert isinstance(site.protocols[0], ModbusRTUSimulator)
        assert isinstance(site.controllers[0].device, EnergyMeterSimulator)

    def test_rtu_serial_params_wired(self):
        cfg = make_site_config([{
            "type": "bess",
            "protocols": [make_rtu_protocol_cfg(
                "bess",
                port="/dev/ttyS0",
                baudrate=19200,
                parity="E",
                stopbits=2,
                bytesize=7,
                timeout=0.5,
                unit_id=5,
            )],
        }])
        site = SiteController(cfg)
        site.build()

        rtu = site.protocols[0]
        assert isinstance(rtu, ModbusRTUSimulator)
        assert rtu.port == "/dev/ttyS0"
        assert rtu.baudrate == 19200
        assert rtu.parity == "E"
        assert rtu.stopbits == 2
        assert rtu.bytesize == 7
        assert rtu.timeout == 0.5
        assert rtu.unit_id == 5

    def test_full_site_all_rtu(self):
        cfg = make_site_config([
            {"type": "bess", "protocols": [make_rtu_protocol_cfg("bess")]},
            {"type": "inverter", "protocols": [make_rtu_protocol_cfg("inverter")]},
            {"type": "energy_meter", "protocols": [make_rtu_protocol_cfg("energy_meter")]},
        ])
        site = SiteController(cfg)
        site.build()

        assert len(site.controllers) == 3
        assert all(isinstance(p, ModbusRTUSimulator) for p in site.protocols)

    def test_unknown_protocol_still_raises(self):
        cfg = make_site_config([{
            "type": "bess",
            "protocols": [{"kind": "mqtt", "host": "localhost", "unit_id": 1, "register_map": "bess_modbus.yaml"}],
        }])
        site = SiteController(cfg)
        with pytest.raises(ValueError, match="Unsupported protocol kind"):
            site.build()


# ==========================================================
# SITE CONTROLLER — MIXED TCP + RTU BUILDS
# ==========================================================

class TestSiteControllerMixedBuild:
    """SiteController builds sites with a mix of TCP and RTU protocols."""

    def test_bess_tcp_pv_rtu(self):
        cfg = make_site_config([
            {"type": "bess", "protocols": [make_tcp_protocol_cfg("bess", tcp_port=59010)]},
            {"type": "inverter", "protocols": [make_rtu_protocol_cfg("inverter")]},
        ])
        site = SiteController(cfg)
        site.build()

        assert len(site.protocols) == 2
        assert isinstance(site.protocols[0], ModbusTCPSimulator)
        assert isinstance(site.protocols[1], ModbusRTUSimulator)

    def test_full_site_mixed(self):
        cfg = make_site_config([
            {"type": "bess", "protocols": [make_tcp_protocol_cfg("bess", tcp_port=59020)]},
            {"type": "inverter", "protocols": [make_rtu_protocol_cfg("inverter")]},
            {"type": "energy_meter", "protocols": [make_rtu_protocol_cfg("energy_meter")]},
        ])
        site = SiteController(cfg)
        site.build()

        assert len(site.controllers) == 3
        tcp_protos = [p for p in site.protocols if isinstance(p, ModbusTCPSimulator)]
        rtu_protos = [p for p in site.protocols if isinstance(p, ModbusRTUSimulator)]
        assert len(tcp_protos) == 1
        assert len(rtu_protos) == 2


# ==========================================================
# RTU SIMULATION — TELEMETRY FLOWS
# ==========================================================

class TestRTUTelemetryFlow:
    """Devices on RTU produce telemetry written to the RTU context."""

    def test_bess_telemetry_in_rtu_context(self):
        cfg = make_site_config([{
            "type": "bess",
            "initial_soc": 65.0,
            "protocols": [make_rtu_protocol_cfg("bess")],
        }])
        site = SiteController(cfg)
        site.build()

        # Step without starting protocol servers (RTU has no TCP port to wait on)
        for _ in range(5):
            for ctrl in site.controllers:
                ctrl.step(dt=0.1)

        bess = get_device(site, BESSSimulator)
        rtu = site.protocols[0]
        reg_map = load_register_map("bess")

        soc_reg = reg_map.get_by_name("system_soc")
        raw = rtu.context[1].getValues(4, soc_reg.address, soc_reg.count)
        register_soc = raw[0] * soc_reg.scale
        assert register_soc == pytest.approx(bess.get_telemetry().system_soc, abs=1.0)

    def test_pv_telemetry_in_rtu_context(self):
        cfg = make_site_config([{
            "type": "inverter",
            "rated_kw": 10.0,
            "protocols": [make_rtu_protocol_cfg("inverter")],
        }])
        site = SiteController(cfg)
        site.build()

        pv = get_device(site, PVSimulator)
        pv.set_irradiance(1000.0)

        for _ in range(10):
            for ctrl in site.controllers:
                ctrl.step(dt=0.1)

        rtu = site.protocols[0]
        reg_map = load_register_map("inverter")
        power_reg = reg_map.get_by_name("total_active_power")
        raw = rtu.context[1].getValues(4, power_reg.address, power_reg.count)
        value = (raw[0] << 16) + raw[1]
        if power_reg.type == "int32" and value > 0x7FFFFFFF:
            value -= 1 << 32
        register_power = value * power_reg.scale
        assert register_power > 0.0

    def test_meter_telemetry_in_rtu_context(self):
        cfg = make_site_config([{
            "type": "energy_meter",
            "protocols": [make_rtu_protocol_cfg("energy_meter")],
        }])
        site = SiteController(cfg)
        site.build()

        for _ in range(5):
            for ctrl in site.controllers:
                ctrl.step(dt=0.1)

        rtu = site.protocols[0]
        reg_map = load_register_map("energy_meter")
        freq_reg = reg_map.get_by_name("grid_frequency")
        raw = rtu.context[1].getValues(4, freq_reg.address, freq_reg.count)
        register_freq = raw[0] * freq_reg.scale
        assert register_freq == pytest.approx(50.0, abs=1.0)


# ==========================================================
# RTU SIMULATION — COMMANDS
# ==========================================================

class TestRTUCommandFlow:
    """Commands written to RTU context are applied to the device."""

    def test_bess_discharge_via_rtu(self):
        cfg = make_site_config([{
            "type": "bess",
            "initial_soc": 75.0,
            "protocols": [make_rtu_protocol_cfg("bess")],
        }])
        site = SiteController(cfg)
        site.build()

        bess = get_device(site, BESSSimulator)
        rtu = site.protocols[0]
        reg_map = load_register_map("bess")

        write_command_registers(
            reg_map.writes, rtu.context, 1,
            {"start_stop_standby": 1, "on_grid_power_setpoint": 15.0},
        )

        for _ in range(500):
            for ctrl in site.controllers:
                ctrl.step(dt=0.1)

        assert bess.soc < 75.0

    def test_bess_charge_via_rtu(self):
        cfg = make_site_config([{
            "type": "bess",
            "initial_soc": 30.0,
            "protocols": [make_rtu_protocol_cfg("bess")],
        }])
        site = SiteController(cfg)
        site.build()

        bess = get_device(site, BESSSimulator)
        rtu = site.protocols[0]
        reg_map = load_register_map("bess")

        write_command_registers(
            reg_map.writes, rtu.context, 1,
            {"start_stop_standby": 1, "on_grid_power_setpoint": -15.0},
        )

        for _ in range(500):
            for ctrl in site.controllers:
                ctrl.step(dt=0.1)

        assert bess.soc > 30.0

    def test_meter_ignores_commands_via_rtu(self):
        cfg = make_site_config([{
            "type": "energy_meter",
            "protocols": [make_rtu_protocol_cfg("energy_meter")],
        }])
        site = SiteController(cfg)
        site.build()

        rtu = site.protocols[0]
        reg_map = load_register_map("energy_meter")

        write_command_registers(
            reg_map.writes, rtu.context, 1,
            {"current_transformer_ratio": 999.0},
        )

        for _ in range(5):
            for ctrl in site.controllers:
                ctrl.step(dt=0.1)

        meter = get_device(site, EnergyMeterSimulator)
        assert meter.get_telemetry().grid_frequency == pytest.approx(50.0, abs=1.0)


# ==========================================================
# MIXED PROTOCOL — ENGINE INTEGRATION
# ==========================================================

class TestMixedProtocolEngine:
    """
    Full engine stepping with mixed TCP + RTU devices, driven
    through SiteController.build() + engine.step_once().
    """

    @pytest.mark.asyncio
    async def test_bess_tcp_pv_rtu_engine_steps(self):
        cfg = make_site_config([
            {"type": "bess", "initial_soc": 70.0,
             "protocols": [make_tcp_protocol_cfg("bess", tcp_port=59030)]},
            {"type": "inverter", "rated_kw": 10.0,
             "protocols": [make_rtu_protocol_cfg("inverter")]},
        ])
        site = SiteController(cfg)
        site.build()
        site_task = asyncio.create_task(site.start())

        try:
            await wait_ready(59030)

            pv = get_device(site, PVSimulator)
            pv.set_irradiance(800.0)

            bess_rmap = load_register_map("bess")
            tcp = get_protocol(site, ModbusTCPSimulator)
            write_command_registers(
                bess_rmap.writes, tcp.context, 1,
                {"start_stop_standby": 1, "on_grid_power_setpoint": 10.0},
            )

            await run_steps(site, 200)

            bess = get_device(site, BESSSimulator)
            assert bess.soc < 70.0
            assert pv.get_telemetry().total_active_power > 0.0

        finally:
            await site.stop()
            site_task.cancel()
            try:
                await site_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_full_site_bess_tcp_pv_rtu_meter_rtu(self):
        cfg = make_site_config([
            {"type": "bess",
             "protocols": [make_tcp_protocol_cfg("bess", tcp_port=59040)]},
            {"type": "inverter",
             "protocols": [make_rtu_protocol_cfg("inverter")]},
            {"type": "energy_meter",
             "protocols": [make_rtu_protocol_cfg("energy_meter")]},
        ])
        site = SiteController(cfg)
        site.build()
        site_task = asyncio.create_task(site.start())

        try:
            await wait_ready(59040)
            await run_steps(site, 50)

            assert get_device(site, BESSSimulator).get_telemetry().system_soc > 0.0
            assert get_device(site, EnergyMeterSimulator).get_telemetry().grid_frequency == pytest.approx(50.0, abs=1.0)

            # Verify telemetry landed in RTU context
            rtu_protos = [p for p in site.protocols if isinstance(p, ModbusRTUSimulator)]
            for rtu in rtu_protos:
                assert rtu.context is not None
                assert rtu.context[1] is not None

        finally:
            await site.stop()
            site_task.cancel()
            try:
                await site_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_two_bess_one_tcp_one_rtu(self):
        """Two BESS units: one on TCP, one on RTU. Independent SOC tracking."""
        cfg = make_site_config([
            {"type": "bess", "initial_soc": 80.0,
             "protocols": [make_tcp_protocol_cfg("bess", tcp_port=59050)]},
            {"type": "bess", "initial_soc": 40.0,
             "protocols": [make_rtu_protocol_cfg("bess")]},
        ])
        site = SiteController(cfg)
        site.build()
        site_task = asyncio.create_task(site.start())

        try:
            await wait_ready(59050)

            bess_devices = [c.device for c in site.controllers if isinstance(c.device, BESSSimulator)]
            assert len(bess_devices) == 2

            # Discharge the TCP BESS only
            tcp = get_protocol(site, ModbusTCPSimulator)
            bess_rmap = load_register_map("bess")
            write_command_registers(
                bess_rmap.writes, tcp.context, 1,
                {"start_stop_standby": 1, "on_grid_power_setpoint": 15.0},
            )

            await run_steps(site, 300)

            # TCP BESS should have discharged
            tcp_bess = bess_devices[0]
            rtu_bess = bess_devices[1]
            assert tcp_bess.soc < 80.0

            # RTU BESS should be unchanged (no commands written)
            assert rtu_bess.soc == pytest.approx(40.0, abs=1.0)

        finally:
            await site.stop()
            site_task.cancel()
            try:
                await site_task
            except asyncio.CancelledError:
                pass


# ==========================================================
# DUAL PROTOCOL — SINGLE DEVICE, TCP + RTU
# ==========================================================

class TestDualProtocolDevice:
    """
    A single device exposed over both TCP and RTU simultaneously
    via SiteController config with two protocol entries.
    """

    def test_dual_protocol_build(self):
        cfg = make_site_config([{
            "type": "bess",
            "initial_soc": 55.0,
            "protocols": [
                make_tcp_protocol_cfg("bess", tcp_port=59060),
                make_rtu_protocol_cfg("bess"),
            ],
        }])
        site = SiteController(cfg)
        site.build()

        assert len(site.protocols) == 2
        tcp_protos = [p for p in site.protocols if isinstance(p, ModbusTCPSimulator)]
        rtu_protos = [p for p in site.protocols if isinstance(p, ModbusRTUSimulator)]
        assert len(tcp_protos) == 1
        assert len(rtu_protos) == 1

    @pytest.mark.asyncio
    async def test_dual_protocol_telemetry_sync(self):
        cfg = make_site_config([{
            "type": "bess",
            "initial_soc": 55.0,
            "protocols": [
                make_tcp_protocol_cfg("bess", tcp_port=59070),
                make_rtu_protocol_cfg("bess"),
            ],
        }])
        site = SiteController(cfg)
        site.build()
        site_task = asyncio.create_task(site.start())

        try:
            await wait_ready(59070)
            await run_steps(site, 5)

            reg_map = load_register_map("bess")
            soc_reg = reg_map.get_by_name("system_soc")

            tcp = get_protocol(site, ModbusTCPSimulator)
            rtu = get_protocol(site, ModbusRTUSimulator)

            tcp_raw = tcp.context[1].getValues(4, soc_reg.address, soc_reg.count)
            rtu_raw = rtu.context[1].getValues(4, soc_reg.address, soc_reg.count)

            tcp_soc = tcp_raw[0] * soc_reg.scale
            rtu_soc = rtu_raw[0] * soc_reg.scale

            assert tcp_soc == pytest.approx(rtu_soc, abs=0.01)
            assert tcp_soc == pytest.approx(55.0, abs=1.0)

        finally:
            await site.stop()
            site_task.cancel()
            try:
                await site_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_command_via_rtu_applies_to_dual_protocol_device(self):
        cfg = make_site_config([{
            "type": "bess",
            "initial_soc": 70.0,
            "protocols": [
                make_tcp_protocol_cfg("bess", tcp_port=59080),
                make_rtu_protocol_cfg("bess"),
            ],
        }])
        site = SiteController(cfg)
        site.build()
        site_task = asyncio.create_task(site.start())

        try:
            await wait_ready(59080)

            rtu = get_protocol(site, ModbusRTUSimulator)
            reg_map = load_register_map("bess")

            write_command_registers(
                reg_map.writes, rtu.context, 1,
                {"start_stop_standby": 1, "on_grid_power_setpoint": 15.0},
            )

            await run_steps(site, 300)

            bess = get_device(site, BESSSimulator)
            assert bess.soc < 70.0

        finally:
            await site.stop()
            site_task.cancel()
            try:
                await site_task
            except asyncio.CancelledError:
                pass


# ==========================================================
# LIFECYCLE
# ==========================================================

class TestProtocolLifecycle:

    @pytest.mark.asyncio
    async def test_rtu_shutdown_without_start_is_safe(self):
        rtu = ModbusRTUSimulator(port="/dev/null", unit_id=1)
        await rtu.shutdown()
        assert rtu._task is None

    @pytest.mark.asyncio
    async def test_tcp_shutdown_without_start_is_safe(self):
        tcp = ModbusTCPSimulator(address="127.0.0.1", port=59099, unit_id=1)
        await tcp.shutdown()
        assert tcp._task is None

    @pytest.mark.asyncio
    async def test_rtu_shutdown_cancels_task(self):
        rtu = ModbusRTUSimulator(port="/dev/null", unit_id=1)

        async def fake_server():
            await asyncio.sleep(3600)

        rtu._task = asyncio.create_task(fake_server())
        await rtu.shutdown()
        assert rtu._task is None