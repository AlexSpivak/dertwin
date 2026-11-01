"""
Plane-of-array transposition and DC sizing on the PV array.

Reference location: Stockholm, 59.3293 N / 18.0686 E, 44 m.
"""

import logging
from datetime import datetime, timedelta, timezone

import pytest

from dertwin.devices.external import solar
from dertwin.devices.external.irradiance import (
    ClearSkyIrradianceModel,
    IrradianceModel,
)
from dertwin.devices.pv.panel import PVArrayModel
from dertwin.devices.pv.simulator import PVSimulator


STOCKHOLM_LAT = 59.3293
STOCKHOLM_LON = 18.0686
STOCKHOLM_ALT_M = 44.0

# Stockholm solar noon on 2025-10-31
OCTOBER_NOON = datetime(2025, 10, 31, 10, 31, tzinfo=timezone.utc)
JUNE_NOON = datetime(2025, 6, 21, 10, 49, tzinfo=timezone.utc)


def stockholm_model(**overrides):
    kwargs = dict(
        latitude=STOCKHOLM_LAT,
        longitude=STOCKHOLM_LON,
        altitude_m=STOCKHOLM_ALT_M,
    )
    kwargs.update(overrides)
    return ClearSkyIrradianceModel(**kwargs)


def components_at(moment):
    model = stockholm_model()
    model.advance(moment, 0.0, 60.0)
    return model.get_components()


# ------------------------------------------------------------
# Transposition on the array
# ------------------------------------------------------------

def test_no_tilt_means_irradiance_is_taken_as_in_plane():
    """The pre-0.2 assumption, kept for configs that set no tilt."""
    panel = PVArrayModel(dc_capacity_kw=10.0)
    components = components_at(OCTOBER_NOON)

    panel.set_irradiance_components(components)

    assert panel.irradiance_w_per_m2 == pytest.approx(components.ghi)


def test_tilt_collects_more_than_horizontal_on_a_low_sun_day():
    components = components_at(OCTOBER_NOON)

    flat = PVArrayModel(dc_capacity_kw=10.0, tilt_deg=0.0, azimuth_deg=180.0)
    tilted = PVArrayModel(dc_capacity_kw=10.0, tilt_deg=45.0, azimuth_deg=180.0)

    flat.set_irradiance_components(components)
    tilted.set_irradiance_components(components)

    assert tilted.irradiance_w_per_m2 > flat.irradiance_w_per_m2 * 1.3


def test_azimuth_matters():
    components = components_at(OCTOBER_NOON)

    south = PVArrayModel(dc_capacity_kw=10.0, tilt_deg=45.0, azimuth_deg=180.0)
    east = PVArrayModel(dc_capacity_kw=10.0, tilt_deg=45.0, azimuth_deg=90.0)

    south.set_irradiance_components(components)
    east.set_irradiance_components(components)

    assert south.irradiance_w_per_m2 > east.irradiance_w_per_m2


def test_tilt_with_the_legacy_model_falls_back_and_warns_once(caplog):
    legacy = IrradianceModel()
    legacy.advance(datetime(2025, 10, 31, 12, tzinfo=timezone.utc), 12 * 3600.0, 60.0)
    components = legacy.get_components()

    panel = PVArrayModel(dc_capacity_kw=10.0, tilt_deg=45.0, azimuth_deg=180.0)

    with caplog.at_level(logging.WARNING):
        panel.set_irradiance_components(components)
        panel.set_irradiance_components(components)

    assert panel.irradiance_w_per_m2 == pytest.approx(components.ghi)
    assert caplog.text.count("treating irradiance as already in-plane") == 1


def test_transposition_is_zero_at_night():
    panel = PVArrayModel(dc_capacity_kw=10.0, tilt_deg=45.0, azimuth_deg=180.0)
    panel.set_irradiance_components(
        components_at(datetime(2025, 10, 31, 0, tzinfo=timezone.utc))
    )

    assert panel.irradiance_w_per_m2 == 0.0
    assert panel.dc_power_w() == 0.0


# ------------------------------------------------------------
# DC sizing
# ------------------------------------------------------------

def test_capacity_and_area_paths_agree_when_equivalent():
    """area × efficiency × 1000 W/m² is the same nameplate as dc_capacity_kw."""
    capacity = PVArrayModel(dc_capacity_kw=2.0)
    area = PVArrayModel(area_m2=10.0, module_efficiency=0.20)

    for panel in (capacity, area):
        panel.set_irradiance(1000.0)

    assert capacity.dc_power_w() == pytest.approx(area.dc_power_w(), rel=1e-9)


def test_capacity_path_scales_with_irradiance():
    panel = PVArrayModel(dc_capacity_kw=10.0)

    panel.set_irradiance(1000.0)
    full = panel.dc_power_w()
    panel.set_irradiance(500.0)
    half = panel.dc_power_w()

    assert half < full
    assert half > full * 0.45


def test_derate_scales_output():
    plain = PVArrayModel(dc_capacity_kw=10.0)
    derated = PVArrayModel(dc_capacity_kw=10.0, derate=0.86)

    for panel in (plain, derated):
        panel.set_irradiance(1000.0)

    assert derated.dc_power_w() == pytest.approx(plain.dc_power_w() * 0.86, rel=1e-9)


def test_panel_needs_a_sizing_input():
    with pytest.raises(ValueError, match="dc_capacity_kw or area_m2"):
        PVArrayModel()


@pytest.mark.parametrize("derate", [0.0, -0.1, 1.5])
def test_invalid_derate_raises(derate):
    with pytest.raises(ValueError, match="derate"):
        PVArrayModel(dc_capacity_kw=10.0, derate=derate)


# ------------------------------------------------------------
# PVSimulator wiring
# ------------------------------------------------------------

def test_rated_kw_alone_keeps_the_pre_0_2_behaviour():
    """DC/AC stays 1.0, so an existing config is sized exactly as before."""
    sim = PVSimulator(rated_kw=30.0)

    assert sim.ac_rating_kw == pytest.approx(30.0)
    assert sim.dc_capacity_kw == pytest.approx(30.0)
    assert sim.tilt_deg is None


def test_dc_ac_ratio_oversizes_the_array():
    sim = PVSimulator(rated_kw=30.0, dc_ac_ratio=1.2)

    assert sim.dc_capacity_kw == pytest.approx(36.0)
    assert sim.ac_rating_kw == pytest.approx(30.0)


def test_explicit_dc_capacity_wins():
    sim = PVSimulator(ac_rating_kw=30.0, dc_capacity_kw=40.0, dc_ac_ratio=1.2)

    assert sim.dc_capacity_kw == pytest.approx(40.0)


def test_area_still_overrides_capacity_sizing():
    sim = PVSimulator(rated_kw=30.0, area_m2=200.0, module_efficiency=0.2)

    assert sim.dc_capacity_kw is None
    assert sim.panel.area_m2 == pytest.approx(200.0)


def test_azimuth_alone_adopts_a_latitude_tilt():
    sim = PVSimulator(
        rated_kw=30.0, azimuth_deg=180.0, irradiance_model=stockholm_model()
    )

    assert sim.tilt_deg == pytest.approx(solar.default_tilt_deg(STOCKHOLM_LAT))


def test_tilt_alone_adopts_a_hemisphere_azimuth():
    northern = PVSimulator(
        rated_kw=30.0, tilt_deg=45.0, irradiance_model=stockholm_model()
    )
    southern = PVSimulator(
        rated_kw=30.0,
        tilt_deg=30.0,
        irradiance_model=ClearSkyIrradianceModel(latitude=-33.92, longitude=18.42),
    )

    assert northern.azimuth_deg == 180.0
    assert southern.azimuth_deg == 0.0


def test_itemised_losses_multiply_out():
    sim = PVSimulator(
        rated_kw=10.0,
        losses={"soiling": 0.98, "wiring": 0.98, "mismatch": 0.98},
    )

    assert sim.panel.derate == pytest.approx(0.98 ** 3, rel=1e-9)


def test_itemised_losses_win_over_a_single_derate():
    sim = PVSimulator(rated_kw=10.0, derate=0.5, losses={"soiling": 0.9})

    assert sim.panel.derate == pytest.approx(0.9)


def test_simulator_transposes_through_the_irradiance_model():
    sim = PVSimulator(
        rated_kw=30.0,
        dc_ac_ratio=1.2,
        tilt_deg=45.0,
        azimuth_deg=180.0,
        irradiance_model=stockholm_model(),
    )

    sim.irradiance_model.advance(OCTOBER_NOON, 0.0, 60.0)
    sim.update(60.0)

    ghi = sim.irradiance_model.get_components().ghi
    assert sim.panel.irradiance_w_per_m2 > ghi
    assert sim.get_telemetry().total_active_power > 0.0


def test_legacy_irradiance_model_still_drives_the_simulator():
    legacy = IrradianceModel()
    sim = PVSimulator(rated_kw=10.0, irradiance_model=legacy)

    legacy.advance(datetime(2025, 10, 31, 12, tzinfo=timezone.utc), 12 * 3600.0, 60.0)
    sim.update(60.0)

    assert sim.panel.irradiance_w_per_m2 == pytest.approx(1000.0)


def test_set_irradiance_still_works_directly():
    sim = PVSimulator(rated_kw=10.0)
    sim.set_irradiance(800.0)

    assert sim.panel.irradiance_w_per_m2 == pytest.approx(800.0)


# ------------------------------------------------------------
# Daily energy counter
# ------------------------------------------------------------

def test_today_energy_resets_at_the_utc_day_boundary():
    model = stockholm_model(cloud_model=None)
    sim = PVSimulator(rated_kw=30.0, irradiance_model=model)

    moment = JUNE_NOON
    for step in range(10):
        model.advance(moment + timedelta(minutes=step), 0.0, 60.0)
        sim.update(60.0)

    assert sim.today_energy_kwh > 0.0
    lifetime_before = sim.lifetime_energy_kwh

    # Same clock time, next day
    model.advance(JUNE_NOON + timedelta(days=1), 0.0, 60.0)
    sim.update(60.0)

    assert sim.today_energy_kwh < lifetime_before
    assert sim.lifetime_energy_kwh > lifetime_before
