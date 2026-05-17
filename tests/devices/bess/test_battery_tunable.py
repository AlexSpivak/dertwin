"""
Tests for:

1. Thermal + electrical parameters of BatteryModel are configurable
   (via constructor, BESSSimulator, and SiteController scenario JSON).
2. When not specified, they auto-scale with capacity_kwh so a default
   pack of any size has physically-reasonable thermal behaviour
   (~constant steady-state ΔT at 1C, independent of pack size).
"""

import pytest

from dertwin.controllers.site_controller import SiteController
from dertwin.devices.bess.battery import BatteryLimits, BatteryModel
from dertwin.devices.bess.simulator import BESSSimulator


# ---------------------------------------------------------
# Auto-scaling defaults
# ---------------------------------------------------------


def test_default_thermals_scale_linearly_with_capacity():
    """A 10× bigger pack should have 10× the thermal capacity and 10× the
    cooling conductance, and 1/10× the internal resistance."""
    small = BatteryModel(capacity_kwh=10.0)
    medium = BatteryModel(capacity_kwh=100.0)
    large = BatteryModel(capacity_kwh=1000.0)

    assert medium.thermal_capacity_j_per_k == pytest.approx(10 * small.thermal_capacity_j_per_k)
    assert large.thermal_capacity_j_per_k == pytest.approx(100 * small.thermal_capacity_j_per_k)

    assert medium.thermal_conductance_w_per_k == pytest.approx(10 * small.thermal_conductance_w_per_k)
    assert large.thermal_conductance_w_per_k == pytest.approx(100 * small.thermal_conductance_w_per_k)

    assert medium.internal_resistance == pytest.approx(small.internal_resistance / 10)
    assert large.internal_resistance == pytest.approx(small.internal_resistance / 100)


def test_default_steady_state_delta_t_at_1c_is_independent_of_pack_size():
    """Regardless of pack size, sustained 1C charge converges to roughly the
    same modest ΔT over ambient — well below any derating band."""

    def steady_state_delta(capacity_kwh: float) -> float:
        battery = BatteryModel(capacity_kwh=capacity_kwh)
        battery.step(-capacity_kwh, 3600.0)  # 1C for 1 hour
        for _ in range(10):
            battery.step(-capacity_kwh, 600.0)
        return battery.temperature_c - battery.ambient_temp_c

    delta_small = steady_state_delta(10.0)
    delta_medium = steady_state_delta(100.0)
    delta_large = steady_state_delta(1000.0)

    for delta in (delta_small, delta_medium, delta_large):
        assert 0.0 < delta < 5.0, f"unexpected steady-state ΔT: {delta} K"

    largest = max(delta_small, delta_medium, delta_large)
    smallest = min(delta_small, delta_medium, delta_large)
    assert largest / smallest < 2.0


def test_default_battery_does_not_thermally_derate_at_1c():
    """Sanity check: at default thermals, sustained 1C charging should NEVER
    push the simulated pack into the >40 °C derating band."""
    for capacity_kwh in (10.0, 100.0, 1000.0):
        battery = BatteryModel(capacity_kwh=capacity_kwh)
        for _ in range(120):  # 2 hours of 1-minute steps
            battery.step(-capacity_kwh, 60.0)
        assert battery.temperature_c <= 40.0, (
            f"Pack of {capacity_kwh} kWh reached {battery.temperature_c} °C at 1C "
            "charge — default thermals are too aggressive."
        )


# ---------------------------------------------------------
# Explicit overrides still work
# ---------------------------------------------------------


def test_explicit_thermals_override_auto_scaling():
    battery = BatteryModel(
        capacity_kwh=100.0,
        internal_resistance=0.05,
        thermal_capacity_j_per_k=5000.0,
        thermal_conductance_w_per_k=0.5,
    )

    assert battery.internal_resistance == 0.05
    assert battery.thermal_capacity_j_per_k == 5000.0
    assert battery.thermal_conductance_w_per_k == 0.5


def test_pre_auto_scale_thermals_still_reach_derating_band():
    """Passing the pre-auto-scale hardcoded values explicitly reproduces the
    old behaviour — useful for tests that need the thermal-tiny pack."""
    battery = BatteryModel(
        capacity_kwh=100.0,
        internal_resistance=0.05,
        thermal_capacity_j_per_k=5000.0,
        thermal_conductance_w_per_k=0.5,
    )
    for _ in range(300):  # 5 minutes of 1C charge
        battery.step(-100.0, 1.0)
    assert battery.temperature_c > 40.0


# ---------------------------------------------------------
# BESSSimulator pass-through
# ---------------------------------------------------------


def test_bess_simulator_propagates_thermal_overrides():
    sim = BESSSimulator(
        capacity_kwh=100.0,
        internal_resistance=0.01,
        thermal_capacity_j_per_k=750_000.0,
        thermal_conductance_w_per_k=300.0,
    )

    assert sim.battery.internal_resistance == 0.01
    assert sim.battery.thermal_capacity_j_per_k == 750_000.0
    assert sim.battery.thermal_conductance_w_per_k == 300.0


def test_bess_simulator_propagates_battery_limits():
    custom = BatteryLimits(
        soc_lower_limit_1=15.0,
        soc_lower_limit_2=10.0,
        soc_upper_limit_1=95.0,
        soc_upper_limit_2=99.0,
    )
    sim = BESSSimulator(capacity_kwh=100.0, limits=custom)

    assert sim.battery.limits is custom


def test_bess_simulator_auto_scales_when_not_overridden():
    sim_small = BESSSimulator(capacity_kwh=10.0)
    sim_large = BESSSimulator(capacity_kwh=1000.0)

    assert sim_large.battery.thermal_capacity_j_per_k == pytest.approx(
        100 * sim_small.battery.thermal_capacity_j_per_k
    )


# ---------------------------------------------------------
# SiteController scenario JSON
# ---------------------------------------------------------


_BASE_CONFIG = {
    "site_name": "tunable-test",
    "step": 0.1,
    "real_time": False,
    "register_map_root": "register_maps",
    "external_models": {},
    "assets": [],
}


def _bess_asset_cfg(**overrides):
    base = {"type": "bess", "capacity_kwh": 100.0, "protocols": []}
    base.update(overrides)
    return base


def test_site_controller_reads_thermal_and_soc_limit_fields():
    config = {
        **_BASE_CONFIG,
        "assets": [
            _bess_asset_cfg(
                round_trip_eff=0.80,
                internal_resistance=0.02,
                thermal_capacity_j_per_k=600_000.0,
                thermal_conductance_w_per_k=150.0,
                ambient_temp_c=12.0,
                soc_limits={
                    "lower_1": 30.0,
                    "lower_2": 25.0,
                    "upper_1": 80.0,
                    "upper_2": 88.0,
                },
            ),
        ],
    }
    site = SiteController(config)
    site.build()
    device = site._create_device(config["assets"][0])

    assert device.battery.round_trip_eff == 0.80
    assert device.battery.internal_resistance == 0.02
    assert device.battery.thermal_capacity_j_per_k == 600_000.0
    assert device.battery.thermal_conductance_w_per_k == 150.0
    assert device.battery.ambient_temp_c == 12.0
    assert device.battery.limits.soc_lower_limit_1 == 30.0
    assert device.battery.limits.soc_lower_limit_2 == 25.0
    assert device.battery.limits.soc_upper_limit_1 == 80.0
    assert device.battery.limits.soc_upper_limit_2 == 88.0


def test_site_controller_omitted_thermal_fields_use_auto_scaling():
    """With no thermal/electrical params in the scenario JSON, the resulting
    BatteryModel matches what BatteryModel(capacity_kwh=...) produces directly."""
    config = {**_BASE_CONFIG, "assets": [_bess_asset_cfg(capacity_kwh=500.0)]}
    site = SiteController(config)
    site.build()
    device = site._create_device(config["assets"][0])

    expected = BatteryModel(capacity_kwh=500.0)

    assert device.battery.internal_resistance == pytest.approx(expected.internal_resistance)
    assert device.battery.thermal_capacity_j_per_k == pytest.approx(
        expected.thermal_capacity_j_per_k
    )
    assert device.battery.thermal_conductance_w_per_k == pytest.approx(
        expected.thermal_conductance_w_per_k
    )