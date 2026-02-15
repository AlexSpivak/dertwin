import argparse
import asyncio
import json
from pathlib import Path

from dertwin.controllers.site_controller import SiteController
from dertwin.logging_config import setup_logging

setup_logging("INFO")
ROOT = Path(__file__).resolve().parent.parent

def load_config(path: Path) -> dict:
    if not path.is_absolute():
        path = ROOT / path

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)



async def run_site(config_path: Path):
    config = load_config(config_path)

    site = SiteController(config)
    site.build()

    try:
        await site.start()
    except KeyboardInterrupt:
        site.stop()


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
