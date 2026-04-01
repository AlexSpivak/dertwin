"""
Mixed-protocol EMS example.

Demonstrates a site where:
  - BESS is controlled via Modbus TCP (active dispatch)
  - PV inverter is monitored via Modbus RTU (serial, read-only)
  - Energy meter is monitored via Modbus RTU (serial, read-only)

Usage:
  Terminal 1 — set up virtual serial pairs and start the simulator:

    # Create virtual serial port pairs (link simulator <-> EMS client)
    socat -d -d pty,raw,echo=0,link=/tmp/dertwin_pv pty,raw,echo=0,link=/tmp/dertwin_pv_client &
    socat -d -d pty,raw,echo=0,link=/tmp/dertwin_meter pty,raw,echo=0,link=/tmp/dertwin_meter_client &

    # Start the simulator (from repo root)
    dertwin -c configs/mixed_protocol_config.json

  Terminal 2 — run the EMS:

    cd examples
    python main_mixed.py

Expected output:
    [BESS-1] TCP connected
    [PV] RTU connected
    [METER] RTU connected
    [BESS-1] Starting in CHARGE mode

    [EMS] Mixed-protocol EMS running
      [BESS-1] RUN  | SOC= 42.3% | P= -30.00 kW | MODE=charge
      [PV]    P= 18.50 kW (producing)
      [METER] Grid= -8.50 kW (exporting) | Freq=50.002 Hz | Import=0.0 kWh | Export=2.1 kWh

If RTU serial ports are unavailable, the EMS will still run with
BESS-only control — PV and meter lines will show as unavailable.
"""

import asyncio
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))

from mixed.ems import BESSUnit, RTUDevice, MixedProtocolEMS
from protocol.modbus_client import SimpleModbusClient
from protocol.modbus_rtu_client import SimpleModbusRTUClient

REPO_ROOT = Path(__file__).resolve().parent.parent


def resolve_map(rel_path: str) -> str:
    return str((REPO_ROOT / rel_path).resolve())


async def main():
    config_path = Path(__file__).parent / "mixed" / "config.json"

    with open(config_path) as f:
        config = json.load(f)

    poll_interval = config.get("poll_interval", 2)
    assets = config["assets"]

    bess = None
    pv = None
    meter = None

    for asset in assets:
        kind = asset["type"]
        reg_map = resolve_map(asset["register_map"])

        if kind == "bess":
            # TCP client for active control
            tcp_client = SimpleModbusClient(
                host=asset["host"],
                port=asset["port"],
                unit_id=asset["unit_id"],
                register_map_path=reg_map,
            )
            bess = BESSUnit(
                client=tcp_client,
                name=asset.get("name", "BESS-1"),
                charge_kw=asset.get("max_charge_kw", 30.0),
                discharge_kw=asset.get("max_discharge_kw", 30.0),
            )

        elif kind == "inverter":
            # RTU client for read-only monitoring
            rtu_client = SimpleModbusRTUClient(
                serial_port=asset["serial_port"],
                unit_id=asset["unit_id"],
                register_map_path=reg_map,
                baudrate=asset.get("baudrate", 9600),
            )
            pv = RTUDevice(
                client=rtu_client,
                name="PV",
                telemetry_fields=["total_active_power"],
            )

        elif kind == "energy_meter":
            # RTU client for read-only monitoring
            rtu_client = SimpleModbusRTUClient(
                serial_port=asset["serial_port"],
                unit_id=asset["unit_id"],
                register_map_path=reg_map,
                baudrate=asset.get("baudrate", 9600),
            )
            meter = RTUDevice(
                client=rtu_client,
                name="METER",
                telemetry_fields=[
                    "total_active_power",
                    "grid_frequency",
                    "total_import_energy",
                    "total_export_energy",
                ],
            )

    if not bess:
        print("[EMS] No BESS found in config — aborting")
        return
    if not pv:
        print("[EMS] No PV found in config — aborting")
        return
    if not meter:
        print("[EMS] No meter found in config — aborting")
        return

    ems = MixedProtocolEMS(
        bess=bess,
        pv=pv,
        meter=meter,
        poll_interval=poll_interval,
    )
    await ems.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[EMS] Shutdown.")