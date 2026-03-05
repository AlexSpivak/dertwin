import asyncio
import json
from pathlib import Path

from full.ems import BESSUnit, FullSiteEMS
from protocol.modbus_client import SimpleModbusClient

REPO_ROOT = Path(__file__).resolve().parent.parent


def make_client(host: str, port: int, unit_id: int, register_map_path: str) -> SimpleModbusClient:
    return SimpleModbusClient(
        host=host,
        port=port,
        unit_id=unit_id,
        register_map_path=register_map_path,
    )


async def main():
    config_path = Path(__file__).parent / "full" / "config.json"

    with open(config_path) as f:
        config = json.load(f)

    poll_interval = config.get("poll_interval", 2)
    assets = config["assets"]

    def resolve_map(rel_path: str) -> str:
        return str((REPO_ROOT / rel_path).resolve())

    bess_units = []
    meter_client = None
    pv_client = None

    for asset in assets:
        kind    = asset["type"]
        host    = asset["host"]
        port    = asset["port"]
        unit_id = asset["unit_id"]
        reg_map = resolve_map(asset["register_map"])

        client = make_client(host, port, unit_id, reg_map)

        if kind == "bess":
            name = asset.get("name", f"BESS-{len(bess_units) + 1}")
            unit = BESSUnit(
                client=client,
                name=name,
                charge_kw=asset.get("max_charge_kw", 50.0),
                discharge_kw=asset.get("max_discharge_kw", 50.0),
            )
            bess_units.append(unit)

        elif kind == "energy_meter":
            meter_client = client

        elif kind == "inverter":
            pv_client = client

    if not bess_units:
        print("[EMS] No BESS units found in config — aborting")
        return
    if not meter_client:
        print("[EMS] No energy meter found in config — aborting")
        return
    if not pv_client:
        print("[EMS] No PV inverter found in config — aborting")
        return

    ems = FullSiteEMS(
        bess_units=bess_units,
        meter_client=meter_client,
        pv_client=pv_client,
        poll_interval=poll_interval,
    )
    await ems.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[EMS] Shutdown.")