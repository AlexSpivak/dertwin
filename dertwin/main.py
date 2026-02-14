import argparse
import asyncio
import json
from pathlib import Path
from typing import List

import yaml

from devices.bess import BESSSimulator
from devices.energy_meter import EnergyMeterSimulator
from devices.grid_frequency import GridFrequencyModel
from devices.inverter import InverterSimulator
from protocol.modbus import ModbusSimulator
from dertwin.site.site import SiteController

ROOT_PROJECT = Path(__file__).parent

def load_yaml(file_path: Path) -> List[dict]:
    with open(file_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        return data.get("telemetry", [])


def load_sites_config(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_registers(asset_type: str):
    base = ROOT_PROJECT.parent / "configs" / "modbus"

    read_path = base / asset_type / "read_registers.yaml"
    write_path = base / asset_type / "write_registers.yaml"

    with open(read_path) as f1:
        read_cfg = yaml.safe_load(f1).get("telemetry", [])

    with open(write_path) as f2:
        write_cfg = yaml.safe_load(f2).get("commands", [])

    return read_cfg + write_cfg

async def run_site(customer_id: int, site_id: int, asset_id: int, config_path: Path, deterministic: bool):
    sites_config = load_sites_config(config_path)

    # Prepare configs for each asset type
    configs_map = {
        "bess": load_registers("bess"),
        "inverter": load_registers("pv"),
        "energy_meter": load_registers("em")
    }

    tasks = []

    # Find the requested customer/site
    customer = next((c for c in sites_config if c["customer_id"] == customer_id), None)
    if not customer:
        raise ValueError(f"Customer ID {customer_id} not found in config")

    site = next((s for s in customer["sites"] if s["site_id"] == site_id), None)
    if not site:
        raise ValueError(f"Site ID {site_id} not found for customer {customer_id}")

    asset = next((a for a in site["assets"] if a["asset_id"] == asset_id), None)
    if asset:
        site["assets"] = [asset]


    site_controller = SiteController()
    assets = []

    # Create devices dynamically
    for asset_cfg in site["assets"]:
        dtype = asset_cfg["type"]
        port = asset_cfg["port"]

        if dtype == "bess":
            device = BESSSimulator(interval=0.1, deterministic=deterministic)
        elif dtype == "inverter":
            device = InverterSimulator()
        elif dtype == "energy_meter":
            device = EnergyMeterSimulator(
                pv_supplier=site_controller.sample_pv_w,
                bess_supplier=site_controller.sample_bess_kw,
                grid_frequency_model=GridFrequencyModel(auto_events=True)
            )
        else:
            print(f"[WARN] Unknown asset type: {dtype}")
            continue

        site_controller.register_device(device)
        assets.append((dtype, port, device))

    # Launch Modbus servers for this site's assets
    for dtype, port, device in assets:
        modsim = ModbusSimulator(
            port=port,
            unit_id=1,
            configs=configs_map[dtype],
            device_sim=device
        )
        tasks.append(asyncio.create_task(modsim.run_server()))

    await asyncio.gather(*tasks)


def main():
    parser = argparse.ArgumentParser(description="Run EMS site or single device simulator")

    # Site mode arguments
    parser.add_argument(
        "--site-id", type=int, help="Site ID to run this EMS instance for."
    )
    parser.add_argument(
        "--customer-id", type=int, help="Customer ID to run this EMS instance for."
    )
    parser.add_argument(
        "--asset-id", type=int, help="Asset ID to run this EMS instance for."
    )
    parser.add_argument(
        "-c", "--config",
        type=str,
        default=str(Path(__file__).parent.parent / "configs/local_sites_config.json"),
        help="Path to site configuration JSON (default: configs/local_sites_config.json)"
    )

    parser.add_argument(
        "-d", "--deterministic",
        type=bool,
        default=False,
        help="Run site deterministic simulation (default: False)"
    )
    args = parser.parse_args()

    if args.customer_id is not None and args.site_id is not None:
        # Site mode
        asyncio.run(run_site(args.customer_id, args.site_id, args.asset_id, Path(args.config), args.deterministic))
    else:
        parser.print_help()
        print("\nSpecify --customer-id, --site-id and --asset-id for single device mode.")


if __name__ == "__main__":
    main()
