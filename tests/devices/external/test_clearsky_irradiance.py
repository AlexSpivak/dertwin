"""
Date- and location-aware irradiance, and the factory that picks a model.

Reference location: Stockholm, 59.3293 N / 18.0686 E, 44 m.

The old fixed-shape model made every day of the year 12 hours long and 1000
W/m² at its peak, everywhere. These tests pin down that a late-October day at
59 N now looks like a late-October day at 59 N.
"""

from datetime import datetime, timedelta, timezone

import pytest

from dertwin.devices.external.clouds import AR1CloudModel, ClearSkyCloudModel
from dertwin.devices.external.irradiance import (
    ClearSkyIrradianceModel,
    IrradianceModel,
    build_irradiance_model,
)


STOCKHOLM = dict(latitude=59.3293, longitude=18.0686, altitude_m=44.0)


def build(**overrides):
    kwargs = dict(STOCKHOLM, linke_turbidity=3.0)
    kwargs.update(overrides)
    return ClearSkyIrradianceModel(**kwargs)


def scan_day(model, date, step_minutes=1):
    """Advance a model across one UTC day, returning every sample."""
    start = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
    samples = []

    for minute in range(0, 24 * 60, step_minutes):
        moment = start + timedelta(minutes=minute)
        model.advance(moment, minute * 60.0, step_minutes * 60.0)
        samples.append((moment, model.get_components()))

    return samples


def daylight_window_hours(samples, step_minutes=1):
    lit = [moment for moment, c in samples if c.ghi > 0.0]
    if not lit:
        return 0.0
    return (lit[-1] - lit[0]).total_seconds() / 3600.0 + step_minutes / 60.0


def peak_ghi(samples):
    return max(c.ghi for _, c in samples)


# ------------------------------------------------------------
# Stockholm reference days
# ------------------------------------------------------------

@pytest.mark.parametrize("date,expected_hours,expected_peak", [
    (datetime(2025, 10, 31), 8.72, 259.0),
    (datetime(2025, 6, 21), 18.27, 827.0),
    (datetime(2025, 12, 21), 5.75, 87.0),
])
def test_stockholm_daylight_and_peak(date, expected_hours, expected_peak):
    samples = scan_day(build(), date)

    assert daylight_window_hours(samples) == pytest.approx(expected_hours, abs=0.15)
    assert peak_ghi(samples) == pytest.approx(expected_peak, rel=0.08)


def test_late_october_is_not_midsummer():
    october = peak_ghi(scan_day(build(), datetime(2025, 10, 31)))
    june = peak_ghi(scan_day(build(), datetime(2025, 6, 21)))

    assert june / october > 2.5


def test_peak_lands_at_solar_noon():
    samples = scan_day(build(), datetime(2025, 10, 31))
    moment, _ = max(samples, key=lambda item: item[1].ghi)

    expected = datetime(2025, 10, 31, 10, 31, tzinfo=timezone.utc)
    assert abs((moment - expected).total_seconds()) <= 600


def test_night_is_dark_and_irradiance_is_never_negative():
    samples = scan_day(build(), datetime(2025, 10, 31))

    for moment, components in samples:
        assert components.ghi >= 0.0
        if not components.position.is_up:
            assert components.ghi == 0.0
            assert components.dni == 0.0
            assert components.dhi == 0.0


def test_components_reconstruct_ghi():
    for moment, components in scan_day(build(), datetime(2025, 6, 21), 15):
        if components.ghi <= 0.0:
            continue
        reconstructed = (
            components.dni * components.position.cos_zenith + components.dhi
        )
        assert reconstructed == pytest.approx(components.ghi, rel=1e-9)


def test_haurwitz_and_ineichen_broadly_agree():
    haurwitz = peak_ghi(
        scan_day(build(clearsky_model="haurwitz"), datetime(2025, 10, 31))
    )
    ineichen = peak_ghi(
        scan_day(build(clearsky_model="ineichen"), datetime(2025, 10, 31))
    )

    assert haurwitz == pytest.approx(ineichen, rel=0.10)


def test_days_differ_from_each_other():
    """The old model was 24h periodic; this one is not."""
    first = peak_ghi(scan_day(build(), datetime(2025, 10, 1), 10))
    last = peak_ghi(scan_day(build(), datetime(2025, 10, 31), 10))

    assert first > last * 1.2


def test_southern_hemisphere_seasons_are_inverted():
    cape_town = ClearSkyIrradianceModel(latitude=-33.92, longitude=18.42)

    june = peak_ghi(scan_day(cape_town, datetime(2025, 6, 21), 10))
    december = peak_ghi(scan_day(cape_town, datetime(2025, 12, 21), 10))

    assert december > june


def test_longitude_shifts_the_day_without_changing_its_length():
    west = ClearSkyIrradianceModel(latitude=59.3293, longitude=-18.0686)
    east = ClearSkyIrradianceModel(latitude=59.3293, longitude=18.0686)

    west_samples = scan_day(west, datetime(2025, 6, 21), 5)
    east_samples = scan_day(east, datetime(2025, 6, 21), 5)

    assert daylight_window_hours(west_samples, 5) == pytest.approx(
        daylight_window_hours(east_samples, 5), abs=0.3
    )

    west_noon, _ = max(west_samples, key=lambda item: item[1].ghi)
    east_noon, _ = max(east_samples, key=lambda item: item[1].ghi)

    hours_apart = (west_noon - east_noon).total_seconds() / 3600.0
    assert hours_apart == pytest.approx(2 * 18.0686 / 15.0, abs=0.3)


# ------------------------------------------------------------
# Clouds on top
# ------------------------------------------------------------

def test_clouds_only_ever_reduce_irradiance():
    clear = build(cloud_model=ClearSkyCloudModel())
    cloudy = build(cloud_model=AR1CloudModel(seed=59))

    clear_samples = dict(scan_day(clear, datetime(2025, 6, 21), 10))
    cloudy_samples = dict(scan_day(cloudy, datetime(2025, 6, 21), 10))

    for moment, components in cloudy_samples.items():
        assert components.ghi <= clear_samples[moment].ghi + 1e-9

    assert sum(c.ghi for c in cloudy_samples.values()) < sum(
        c.ghi for c in clear_samples.values()
    )


def test_clear_sky_ghi_is_reported_alongside_the_clouded_value():
    model = build(cloud_model=AR1CloudModel(mean_kt=0.4, seed=59))
    samples = scan_day(model, datetime(2025, 6, 21), 10)

    lit = [c for _, c in samples if c.ghi > 0.0]
    assert lit

    for components in lit:
        assert components.clear_sky_ghi >= components.ghi
        assert components.ghi == pytest.approx(
            components.clear_sky_ghi * components.clear_sky_index, rel=1e-9
        )


def test_no_cloud_model_means_clear_sky():
    model = build()
    samples = scan_day(model, datetime(2025, 6, 21), 30)

    lit = [c for _, c in samples if c.position.is_up]
    assert lit

    for components in lit:
        assert components.clear_sky_index == pytest.approx(1.0)


# ------------------------------------------------------------
# Validation
# ------------------------------------------------------------

@pytest.mark.parametrize("kwargs,match", [
    (dict(latitude=100.0), "latitude"),
    (dict(longitude=200.0), "longitude"),
    (dict(clearsky_model="vibes"), "clearsky_model"),
    (dict(linke_turbidity=0.0), "linke_turbidity"),
])
def test_invalid_configuration_raises(kwargs, match):
    with pytest.raises(ValueError, match=match):
        build(**kwargs)


# ------------------------------------------------------------
# Factory
# ------------------------------------------------------------

def test_factory_defaults_to_the_legacy_model():
    """An existing config, with no location, keeps the fixed-shape day."""
    model = build_irradiance_model({"peak": 900.0, "sunrise": 7.0, "sunset": 17.0})

    assert isinstance(model, IrradianceModel)
    assert model.peak == 900.0
    assert model.sunrise == 7.0
    assert model.sunset == 17.0


def test_factory_defaults_to_the_legacy_model_with_no_config_at_all():
    assert isinstance(build_irradiance_model(None), IrradianceModel)
    assert isinstance(build_irradiance_model({}), IrradianceModel)


def test_factory_selects_clearsky_when_a_latitude_is_given():
    model = build_irradiance_model(
        dict(STOCKHOLM, timezone="Europe/Stockholm"),
        sim_start_utc_configured=True,
    )

    assert isinstance(model, ClearSkyIrradianceModel)
    assert model.latitude == pytest.approx(59.3293)
    assert model.timezone_name == "Europe/Stockholm"


def test_factory_builds_the_cloud_model_from_config():
    model = build_irradiance_model(
        dict(STOCKHOLM, cloud={"model": "ar1", "mean_kt": 0.48, "seed": 59}),
        sim_start_utc_configured=True,
    )

    assert isinstance(model.cloud_model, AR1CloudModel)
    assert model.cloud_model.mean_kt == 0.48


def test_factory_honours_cloud_disabled():
    model = build_irradiance_model(
        dict(STOCKHOLM, cloud={"enabled": False}),
        sim_start_utc_configured=True,
    )

    assert isinstance(model.cloud_model, ClearSkyCloudModel)


def test_clearsky_without_a_simulation_date_raises():
    """
    Without sim_start_utc the clock sits at the Unix epoch, so a clear-sky
    model would silently simulate January 1970.
    """
    with pytest.raises(ValueError, match="sim_start_utc"):
        build_irradiance_model(dict(STOCKHOLM), sim_start_utc_configured=False)


@pytest.mark.parametrize("missing", ["latitude", "longitude"])
def test_clearsky_without_a_location_raises(missing):
    cfg = dict(STOCKHOLM, model="clearsky")
    cfg.pop(missing)

    with pytest.raises(ValueError, match=missing):
        build_irradiance_model(cfg, sim_start_utc_configured=True)


def test_unknown_model_name_raises():
    with pytest.raises(ValueError, match="Unknown irradiance.model"):
        build_irradiance_model({"model": "sunshine"})


def test_series_model_without_a_csv_raises():
    with pytest.raises(ValueError, match="series_csv"):
        build_irradiance_model(
            dict(STOCKHOLM, model="series"), sim_start_utc_configured=True
        )


def test_markov_cloud_model_is_not_implemented_yet():
    with pytest.raises(NotImplementedError, match="markov"):
        build_irradiance_model(
            dict(STOCKHOLM, cloud={"model": "markov"}),
            sim_start_utc_configured=True,
        )
