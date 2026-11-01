"""
Print a day from a site config as an ASCII table and sparkline.

Standard library only — no matplotlib — so the seasonal difference between two
locations is visible straight after a clone. Runs the real engine in
deterministic mode, so nothing binds a port and nothing sleeps.

    python examples/seasonal/plot_day.py stockholm
    python examples/seasonal/plot_day.py kyiv --date 2025-06-21
    python examples/seasonal/plot_day.py stockholm --clear --interval 30

`--clear` disables the cloud model, which is what you want when comparing two
locations: with clouds on you are comparing two climates and two random
draws, not two latitudes.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# Importing dertwin.main configures logging and announces it, and the engine
# logs a tick every 100 steps. Both would bury the table, so silence INFO
# before the import rather than after it.
logging.disable(logging.INFO)

from dertwin.controllers.site_controller import SiteController  # noqa: E402
from dertwin.main import load_config  # noqa: E402

SITES = {
    "stockholm": "configs/stockholm_site_config.json",
    "kyiv": "configs/kyiv_site_config.json",
}

BLOCKS = " ▁▂▃▄▅▆▇█"


def sparkline(values, width=48):
    """One character per bucket, scaled to the series maximum."""
    if not values:
        return ""

    bucket = max(1, len(values) // width)
    buckets = [
        values[i:i + bucket] for i in range(0, len(values), bucket)
    ][:width]
    peaks = [max(b) for b in buckets]

    top = max(peaks)
    if top <= 0:
        return BLOCKS[0] * len(peaks)

    return "".join(
        BLOCKS[min(len(BLOCKS) - 1, int(round(p / top * (len(BLOCKS) - 1))))]
        for p in peaks
    )


def build_site(site_key, date, clear, step_s):
    config = load_config(REPO_ROOT / SITES[site_key])

    config["real_time"] = False
    config["step"] = step_s
    config["sim_start_utc"] = f"{date}T00:00:00Z"

    if clear:
        config["external_models"]["irradiance"]["cloud"] = {"enabled": False}

    # Device-only: drop the Modbus endpoints so nothing binds a port, and the
    # BESS state files so a demo run never overwrites a saved SOC.
    for asset in config["assets"]:
        asset.pop("protocols", None)
        asset.pop("state_file", None)

    site = SiteController(config)
    site.build()
    return site


async def collect(site, step_s):
    irradiance = site.external_models.irradiance_model
    load_model = site.external_models.load_model
    pv = site._devices_by_type["inverter"][0]
    bess = site._devices_by_type["bess"][0]
    meter = site._devices_by_type["energy_meter"][0]

    rows = []
    for _ in range(int(24 * 3600 / step_s)):
        await site.engine.step_once()
        components = irradiance.get_components()

        rows.append({
            "utc": site.engine.utc_time,
            "elevation": components.position.elevation_deg,
            "ghi": components.ghi,
            "poa": pv.panel.irradiance_w_per_m2,
            "pv_kw": pv.get_telemetry().total_active_power,
            "load_kw": load_model.get_power_kw(),
            "soc": bess.get_telemetry().system_soc,
            "grid_kw": meter.get_telemetry().total_active_power,
        })

    return rows


def report(site_key, rows, site, interval_min, step_s):
    irradiance = site.external_models.irradiance_model
    lit = [row for row in rows if row["elevation"] > 0.0]
    daylight_h = len(lit) * step_s / 3600.0

    print(f"\n  {site.config['site_name']}  ({site_key})")
    print(
        f"  {irradiance.latitude:.4f} N, {irradiance.longitude:.4f} E"
        f"  |  {irradiance.timezone_name or 'UTC'}"
        f"  |  {rows[0]['utc']:%Y-%m-%d} UTC"
    )
    print(f"  daylight {daylight_h:.2f} h, all times UTC\n")

    every = max(1, int(interval_min * 60 / step_s))
    header = (
        f"  {'UTC':>5} {'elev':>6} {'GHI':>7} {'POA':>7} "
        f"{'PV':>7} {'load':>7} {'grid':>8} {'SOC':>6}"
    )
    print(header)
    print(f"  {'':>5} {'deg':>6} {'W/m2':>7} {'W/m2':>7} "
          f"{'kW':>7} {'kW':>7} {'kW':>8} {'%':>6}")
    print("  " + "-" * (len(header) - 2))

    for row in rows[::every]:
        print(
            f"  {row['utc']:%H:%M} {row['elevation']:6.1f} {row['ghi']:7.1f} "
            f"{row['poa']:7.1f} {row['pv_kw']:7.2f} {row['load_kw']:7.2f} "
            f"{row['grid_kw']:8.2f} {row['soc']:6.1f}"
        )

    print()
    for label, key in (("GHI ", "ghi"), ("PV  ", "pv_kw"), ("load", "load_kw")):
        series = [row[key] for row in rows]
        print(f"  {label} {sparkline(series)}  peak {max(series):.1f}")

    pv_kwh = sum(row["pv_kw"] for row in rows) * step_s / 3600.0
    load_kwh = sum(row["load_kw"] for row in rows) * step_s / 3600.0
    print(
        f"\n  day totals:  PV {pv_kwh:.1f} kWh"
        f"  |  load {load_kwh:.1f} kWh"
        f"  |  self-sufficiency {100.0 * min(pv_kwh, load_kwh) / load_kwh:.0f}%"
    )


async def main_async(args):
    site = build_site(args.site, args.date, args.clear, args.step)
    rows = await collect(site, args.step)
    report(args.site, rows, site, args.interval, args.step)


def main():
    parser = argparse.ArgumentParser(
        description="Print one simulated day from a DER Twin site config."
    )
    parser.add_argument("site", choices=sorted(SITES), help="which example site")
    parser.add_argument(
        "--date", default="2025-10-31", help="UTC date, YYYY-MM-DD (default: 2025-10-31)"
    )
    parser.add_argument(
        "--clear", action="store_true",
        help="disable the cloud model, for a clean comparison between locations",
    )
    parser.add_argument(
        "--interval", type=float, default=60.0,
        help="minutes between printed rows (default: 60)",
    )
    parser.add_argument(
        "--step", type=float, default=60.0,
        help="simulation timestep in seconds (default: 60)",
    )

    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
