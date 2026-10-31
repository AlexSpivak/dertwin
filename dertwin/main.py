import argparse
import asyncio
import json
from pathlib import Path

from dertwin.controllers.site_controller import SiteController
from dertwin.logging_config import setup_logging

setup_logging("INFO")

def load_config(path: Path) -> dict:
    if not path.is_absolute():
        path = Path.cwd() / path

    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # register_map_root resolves relative to cwd, not config file
    if "register_map_root" in config:
        register_map_root = Path(config["register_map_root"])
        if not register_map_root.is_absolute():
            register_map_root = path.parent / register_map_root
        config["register_map_root"] = str(register_map_root.resolve())

    # data_root follows the same rule, so CSV inputs (irradiance series and
    # friends) live next to the config that declared them rather than wherever
    # dertwin was launched.
    if "data_root" in config:
        data_root = Path(config["data_root"])
        if not data_root.is_absolute():
            data_root = path.parent / data_root
        config["data_root"] = str(data_root.resolve())

    return config

async def run_site(config_path: Path):
    config = load_config(config_path)

    site = SiteController(config)
    site.build()

    try:
        await site.start()
    except (KeyboardInterrupt, asyncio.CancelledError):
        await site.stop()

def main():
    parser = argparse.ArgumentParser(description="Run DER Twin Site Simulator")

    parser.add_argument(
        "-c",
        "--config",
        type=str,
        required=True,
        help="Path to site configuration JSON",
    )

    args = parser.parse_args()

    asyncio.run(run_site(Path(args.config)))


if __name__ == "__main__":
    main()
