import asyncio
import json
from pathlib import Path

from simple.ems import SimpleEMS
from protocol.modbus_client import SimpleModbusClient

REPO_ROOT = Path(__file__).resolve().parent.parent

async def main():
    config_path = Path(__file__).parent / "simple" / "config.json"

    with open(config_path) as f:
        config = json.load(f)

    asset = config["assets"][0]

    # Resolve register map path relative to config file location
    register_map_path = (REPO_ROOT / asset["register_map"]).resolve()

    client = SimpleModbusClient(
        host=asset["host"],
        port=asset["port"],
        unit_id=asset["unit_id"],
        register_map_path=str(register_map_path),
    )

    ems = SimpleEMS(client, poll_interval=config.get("poll_interval", 2))
    await ems.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[EMS] Shutdown.")