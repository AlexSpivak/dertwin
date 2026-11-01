"""
Cross-check the built-in clear-sky model against pvlib.

The core model is standard library only, so this suite only runs when the
optional extra is installed:

    pip install "dertwin[pvlib]"

It is the honest defence of hand-rolling the physics: if these pass, the
built-in Ineichen-Perez implementation agrees with the reference one.

Reference location: Stockholm, 59.3293 N / 18.0686 E, 44 m.
"""

from datetime import datetime, timedelta, timezone

import pytest

from dertwin.devices.external import solar

pvlib = pytest.importorskip("pvlib", reason="optional dertwin[pvlib] extra")


STOCKHOLM_LAT = 59.3293
STOCKHOLM_LON = 18.0686
STOCKHOLM_ALT_M = 44.0

DATES = ["2025-03-20", "2025-06-21", "2025-10-31", "2025-12-21"]


def daytime_samples(date, step_minutes=15):
    start = datetime.fromisoformat(f"{date}T00:00:00+00:00")

    for minute in range(0, 24 * 60, step_minutes):
        moment = start + timedelta(minutes=minute)
        position = solar.solar_position(moment, STOCKHOLM_LAT, STOCKHOLM_LON)

        # Skip the last degree above the horizon: every clear-sky formulation
        # disagrees wildly at extreme airmass, and it is worth single-digit W.
        if position.elevation_deg > 1.0:
            yield moment, position


@pytest.mark.parametrize("date", DATES)
def test_solar_position_agrees_with_pvlib(date):
    from pvlib.solarposition import get_solarposition

    for moment, position in daytime_samples(date):
        reference = get_solarposition(
            moment, STOCKHOLM_LAT, STOCKHOLM_LON, altitude=STOCKHOLM_ALT_M
        )

        # Geometric against geometric: pvlib's "apparent_elevation" adds
        # atmospheric refraction, which this model does not attempt (it is
        # worth up to half a degree at the horizon and almost no energy).
        #
        # The tolerance is the Spencer-71 series' inherent accuracy, not
        # slack: see test_declination_is_identical_to_pvlibs_spencer71.
        assert position.elevation_deg == pytest.approx(
            float(reference["elevation"].iloc[0]), abs=0.6
        )
        assert position.azimuth_deg == pytest.approx(
            float(reference["azimuth"].iloc[0]), abs=0.5
        )


@pytest.mark.parametrize("date", DATES)
def test_clear_sky_ghi_agrees_with_pvlib(date):
    for moment, position in daytime_samples(date):
        ours = solar.ineichen_ghi(
            position, moment,
            altitude_m=STOCKHOLM_ALT_M, linke_turbidity=3.0,
        )
        reference = solar.clear_sky_ghi(
            position, moment, model=solar.CLEARSKY_PVLIB,
            altitude_m=STOCKHOLM_ALT_M, linke_turbidity=3.0,
        )

        assert ours == pytest.approx(reference, rel=0.05, abs=5.0)


def test_pvlib_backend_is_selectable_through_the_factory():
    from dertwin.devices.external.irradiance import build_irradiance_model

    model = build_irradiance_model(
        {
            "latitude": STOCKHOLM_LAT,
            "longitude": STOCKHOLM_LON,
            "altitude_m": STOCKHOLM_ALT_M,
            "clearsky_model": "pvlib",
            "cloud": {"enabled": False},
        },
        sim_start_utc_configured=True,
    )

    noon = datetime(2025, 10, 31, 10, 31, tzinfo=timezone.utc)
    model.advance(noon, 0.0, 60.0)

    assert model.get_components().ghi == pytest.approx(259.0, rel=0.10)


# ------------------------------------------------------------
# Where the residual error actually comes from
# ------------------------------------------------------------

def test_declination_is_identical_to_pvlibs_spencer71():
    """
    The ~0.5 degree gap against SPA is the Spencer-71 series itself, not this
    implementation of it: pvlib ships the same approximation and is wrong by
    exactly the same amount. Asserting equality is a far stronger contract
    than a tolerance against SPA would be.
    """
    import math

    from pvlib.solarposition import declination_spencer71

    start = datetime(2025, 1, 1, 12, tzinfo=timezone.utc)

    for day in range(0, 365, 7):
        moment = start + timedelta(days=day)
        ours = math.degrees(solar.declination_rad(moment))
        theirs = math.degrees(
            declination_spencer71(moment.timetuple().tm_yday)
        )

        assert ours == pytest.approx(theirs, abs=1e-6)


def test_equation_of_time_is_identical_to_pvlibs_spencer71():
    from pvlib.solarposition import equation_of_time_spencer71

    start = datetime(2025, 1, 1, 12, tzinfo=timezone.utc)

    for day in range(0, 365, 7):
        moment = start + timedelta(days=day)
        ours = solar.equation_of_time_min(moment)
        theirs = float(
            equation_of_time_spencer71(moment.timetuple().tm_yday)
        )

        assert ours == pytest.approx(theirs, abs=0.02)


def test_annual_clear_sky_energy_agrees_within_two_percent():
    """
    The figure that actually matters: two independent implementations of
    Ineichen-Perez, integrated over a whole year at the same solar positions.
    The residual is the airmass and pressure handling, not the geometry.
    """
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    step_minutes = 30

    ours = theirs = 0.0

    for step in range(0, 365 * 24 * 60, step_minutes):
        moment = start + timedelta(minutes=step)
        position = solar.solar_position(moment, STOCKHOLM_LAT, STOCKHOLM_LON)

        if not position.is_up:
            continue

        ours += solar.ineichen_ghi(
            position, moment,
            altitude_m=STOCKHOLM_ALT_M, linke_turbidity=3.0,
        )
        theirs += solar.clear_sky_ghi(
            position, moment, model=solar.CLEARSKY_PVLIB,
            altitude_m=STOCKHOLM_ALT_M, linke_turbidity=3.0,
        )

    assert ours == pytest.approx(theirs, rel=0.02)


def test_solar_position_error_costs_almost_no_annual_energy():
    """
    Isolate the geometry: feed the same clear-sky model our position and
    pvlib's, and integrate over a year. The sub-degree elevation error lands
    almost entirely at the edges of the day, where there is no energy.
    """
    import pandas as pd
    from pvlib.solarposition import get_solarposition

    times = pd.date_range("2025-01-01", "2025-12-31 23:30", freq="30min", tz="UTC")
    reference = get_solarposition(
        times, STOCKHOLM_LAT, STOCKHOLM_LON, altitude=STOCKHOLM_ALT_M
    )

    ours = theirs = 0.0

    for timestamp, reference_elevation in zip(times, reference["elevation"].values):
        moment = timestamp.to_pydatetime()

        our_position = solar.solar_position(moment, STOCKHOLM_LAT, STOCKHOLM_LON)
        their_position = solar.SolarPosition(
            elevation_deg=float(reference_elevation),
            azimuth_deg=180.0,
            zenith_deg=90.0 - float(reference_elevation),
        )

        ours += solar.ineichen_ghi(
            our_position, moment, altitude_m=STOCKHOLM_ALT_M, linke_turbidity=3.0
        )
        theirs += solar.ineichen_ghi(
            their_position, moment, altitude_m=STOCKHOLM_ALT_M, linke_turbidity=3.0
        )

    assert ours == pytest.approx(theirs, rel=0.001)
