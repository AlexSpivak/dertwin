"""
Asset config validation.

A mistyped asset key used to be silently ignored, which is a bad way to find
out your battery never got the SOC window you configured.
"""

import pytest

from dertwin.controllers.site_controller import SiteController


def build(asset: dict) -> SiteController:
    site = SiteController({
        "site_name": "validation",
        "step": 1.0,
        "real_time": False,
        "assets": [asset],
    })
    site.build()
    return site


# ------------------------------------------------------------
# Rejection
# ------------------------------------------------------------

def test_unknown_bess_key_raises():
    with pytest.raises(ValueError, match="Unknown config key"):
        build({"type": "bess", "capacity_kwh": 10.0, "nonsense": 1})


def test_typo_gets_a_did_you_mean_hint():
    with pytest.raises(ValueError, match="did you mean 'capacity_kwh'"):
        build({"type": "bess", "capcity_kwh": 10.0})


def test_typo_in_soc_window_is_caught():
    with pytest.raises(ValueError, match="did you mean 'soc_max_percent'"):
        build({"type": "bess", "soc_mac_percent": 95.0})


def test_unknown_asset_type_raises():
    with pytest.raises(ValueError, match="Unknown asset type"):
        build({"type": "flywheel"})


def test_unknown_pv_key_raises():
    with pytest.raises(ValueError, match="Unknown config key"):
        build({"type": "inverter", "rated_kilowatts": 30.0})


# ------------------------------------------------------------
# Acceptance
# ------------------------------------------------------------

@pytest.mark.parametrize("asset", [
    {"type": "bess", "capacity_kwh": 10.0, "initial_soc": 40.0,
     "max_charge_kw": 5.0, "max_discharge_kw": 5.0, "ramp_rate_kw_per_s": 1.0,
     "soc_limits": {"lower_1": 25.0, "lower_2": 20.0,
                    "upper_1": 85.0, "upper_2": 90.0},
     "limit_mode": "taper", "soc_min_percent": 10.0, "soc_max_percent": 95.0,
     "charge_efficiency": 0.95, "discharge_efficiency": 0.95,
     "self_discharge_percent_per_day": 0.1, "round_trip_eff": 0.9,
     "state_file": "bess.json", "state_save_interval_s": 30.0,
     "ambient_temp_c": 20.0, "internal_resistance": 0.01,
     "thermal_capacity_j_per_k": 1000.0, "thermal_conductance_w_per_k": 10.0},
    {"type": "inverter", "rated_kw": 30.0, "module_efficiency": 0.2,
     "area_m2": 150.0},
    {"type": "chp", "rated_kw": 4000.0, "heat_to_power_ratio": 1.0,
     "min_load_percent": 30.0, "max_load_percent": 110.0},
    {"type": "energy_meter"},
])
def test_documented_keys_are_accepted(asset):
    build(dict(asset))


def test_structural_and_metadata_keys_are_accepted():
    build({
        "type": "bess",
        "name": "BESS-1",
        "asset_id": "bess-0",
        "comment": "primary storage",
        "description": "free text",
        "capacity_kwh": 10.0,
    })


def test_legacy_protocols_shape_is_accepted(tmp_path):
    register_map = (
        "protocol: modbus_tcp\n"
        "device_type: bess\n"
        "registers:\n"
        "  - name: system_soc\n"
        "    internal_name: system_soc\n"
        "    address: 32002\n"
        "    func: 0x04\n"
        "    direction: read\n"
        "    type: uint16\n"
        "    count: 1\n"
        "    scale: 0.1\n"
    )
    (tmp_path / "bess.yaml").write_text(register_map)

    site = SiteController({
        "site_name": "validation",
        "step": 1.0,
        "real_time": False,
        "register_map_root": str(tmp_path),
        "assets": [{
            "type": "bess",
            "capacity_kwh": 10.0,
            "protocols": [{
                "kind": "modbus_tcp",
                "ip": "0.0.0.0",
                "port": 0,
                "unit_id": 1,
                "register_map": "bess.yaml",
            }],
        }],
    })
    site.build()

    assert len(site.controllers) == 1


# ------------------------------------------------------------
# Runtime add_asset goes through the same gate
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_asset_validates_too():
    site = SiteController({
        "site_name": "validation",
        "step": 1.0,
        "real_time": False,
        "assets": [],
    })
    site.build()

    with pytest.raises(ValueError, match="Unknown config key"):
        await site.add_asset({"type": "bess", "capcity_kwh": 10.0})
