"""
Backward compatibility of the shipped site configs.

Every config in configs/ must keep building and running after model changes.
This is the contract behind "old keys keep working": the hard SOC stop and the
efficiency fix changed BESS behaviour, but no config needed editing.
"""

from pathlib import Path

import pytest

from dertwin.controllers.site_controller import SiteController
from dertwin.main import load_config


CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"
CONFIG_PATHS = sorted(CONFIG_DIR.glob("*.json"))


def test_configs_are_discovered():
    assert CONFIG_PATHS, f"no configs found under {CONFIG_DIR}"


@pytest.mark.parametrize("config_path", CONFIG_PATHS, ids=lambda p: p.name)
@pytest.mark.asyncio
async def test_config_builds_and_runs(config_path):
    config = load_config(config_path)
    config["real_time"] = False

    site = SiteController(config)
    site.build()

    assert site.controllers

    for _ in range(100):
        await site.engine.step_once()

    for controller in site.controllers:
        telemetry = controller.device.get_telemetry().to_dict()
        assert telemetry, f"{config_path.name}: empty telemetry"


@pytest.mark.parametrize("config_path", CONFIG_PATHS, ids=lambda p: p.name)
def test_config_declares_no_unknown_asset_keys(config_path):
    """Validation must not reject anything we ship."""
    config = load_config(config_path)
    site = SiteController(config)

    for asset in config.get("assets", []):
        site._validate_asset_spec(asset)


@pytest.mark.asyncio
async def test_legacy_base_load_w_still_drives_the_meter():
    """external_models.power.base_load_w predates any load model."""
    site = SiteController({
        "site_name": "legacy-load",
        "step": 1.0,
        "real_time": False,
        "external_models": {"power": {"base_load_w": 7000.0}},
        "assets": [{"type": "energy_meter"}],
    })
    site.build()

    await site.engine.step_once()

    meter = site._devices_by_type["energy_meter"][0]
    assert meter.get_telemetry().total_active_power == pytest.approx(7.0)


@pytest.mark.asyncio
async def test_bess_without_new_keys_uses_legacy_limit_2_as_hard_window():
    """
    A pre-0.2 config carries only soc_limits. lower_2/upper_2 become the hard
    floor and ceiling, so the pack stops where the old config said it would —
    it just stops dead instead of tapering into it.
    """
    site = SiteController({
        "site_name": "legacy-bess",
        "step": 1.0,
        "real_time": False,
        "assets": [{
            "type": "bess",
            "capacity_kwh": 10.0,
            "initial_soc": 89.0,
            "max_charge_kw": 20.0,
            "ramp_rate_kw_per_s": 1000.0,
            "soc_limits": {
                "lower_1": 25.0, "lower_2": 20.0,
                "upper_1": 85.0, "upper_2": 90.0,
            },
        }],
    })
    site.build()

    bess = site._devices_by_type["bess"][0]
    assert bess.battery.soc_min_percent == 20.0
    assert bess.battery.soc_max_percent == 90.0

    bess.apply_commands({"start_stop_standby": 1})
    bess.apply_commands({"active_power_setpoint": -20.0})

    for _ in range(300):
        await site.engine.step_once()

    assert bess.soc == pytest.approx(90.0, abs=1e-9)
    assert bess.get_telemetry().active_power == 0.0
