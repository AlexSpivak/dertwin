"""
Solar geometry and clear-sky irradiance.

Reference location throughout: Stockholm, 59.3293 N / 18.0686 E, 44 m.
"""

import math
from datetime import datetime, timedelta, timezone

import pytest

from dertwin.devices.external import solar


STOCKHOLM_LAT = 59.3293
STOCKHOLM_LON = 18.0686
STOCKHOLM_ALT_M = 44.0


def utc(year, month, day, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# ------------------------------------------------------------
# Declination and the equation of time
# ------------------------------------------------------------

@pytest.mark.parametrize("date,expected_deg", [
    (utc(2025, 6, 21), 23.44),   # June solstice
    (utc(2025, 12, 21), -23.44),  # December solstice
    (utc(2025, 3, 20), 0.0),      # March equinox
    (utc(2025, 9, 22), 0.0),      # September equinox
])
def test_declination_at_solstices_and_equinoxes(date, expected_deg):
    actual = math.degrees(solar.declination_rad(date))
    assert actual == pytest.approx(expected_deg, abs=0.7)


def test_equation_of_time_extremes():
    """Roughly +16.4 min in early November and -14.2 min in mid February."""
    assert solar.equation_of_time_min(utc(2025, 11, 3)) == pytest.approx(16.4, abs=1.0)
    assert solar.equation_of_time_min(utc(2025, 2, 11)) == pytest.approx(-14.2, abs=1.0)


# ------------------------------------------------------------
# Solar position
# ------------------------------------------------------------

def find_solar_noon(date, lat, lon):
    """Scan a day at one-minute resolution for peak elevation."""
    start = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
    best = None

    for minute in range(24 * 60):
        moment = start + timedelta(minutes=minute)
        position = solar.solar_position(moment, lat, lon)
        if best is None or position.elevation_deg > best[1].elevation_deg:
            best = (moment, position)

    return best


def test_stockholm_solar_noon_is_around_half_past_ten_utc():
    """
    Longitude 18.07 E puts true noon 72 min before 12:00 UTC, and the late
    October equation of time pulls it another 16 min earlier.
    """
    moment, _ = find_solar_noon(utc(2025, 10, 31), STOCKHOLM_LAT, STOCKHOLM_LON)

    expected = datetime(2025, 10, 31, 10, 31, tzinfo=timezone.utc)
    assert abs((moment - expected).total_seconds()) <= 120


def test_sun_is_due_south_at_solar_noon():
    _, position = find_solar_noon(utc(2025, 10, 31), STOCKHOLM_LAT, STOCKHOLM_LON)
    assert position.azimuth_deg == pytest.approx(180.0, abs=1.0)


def test_azimuth_is_easterly_in_the_morning_and_westerly_in_the_afternoon():
    noon, _ = find_solar_noon(utc(2025, 6, 21), STOCKHOLM_LAT, STOCKHOLM_LON)

    morning = solar.solar_position(
        noon - timedelta(hours=4), STOCKHOLM_LAT, STOCKHOLM_LON
    )
    afternoon = solar.solar_position(
        noon + timedelta(hours=4), STOCKHOLM_LAT, STOCKHOLM_LON
    )

    assert 60.0 < morning.azimuth_deg < 180.0
    assert 180.0 < afternoon.azimuth_deg < 300.0


def test_elevation_is_symmetric_about_solar_noon():
    noon, _ = find_solar_noon(utc(2025, 10, 31), STOCKHOLM_LAT, STOCKHOLM_LON)

    before = solar.solar_position(
        noon - timedelta(hours=2), STOCKHOLM_LAT, STOCKHOLM_LON
    )
    after = solar.solar_position(
        noon + timedelta(hours=2), STOCKHOLM_LAT, STOCKHOLM_LON
    )

    assert before.elevation_deg == pytest.approx(after.elevation_deg, abs=0.3)


@pytest.mark.parametrize("date,expected_elevation", [
    (utc(2025, 10, 31), 16.8),
    (utc(2025, 6, 21), 54.1),
    (utc(2025, 12, 21), 7.3),
])
def test_stockholm_noon_elevation(date, expected_elevation):
    _, position = find_solar_noon(date, STOCKHOLM_LAT, STOCKHOLM_LON)
    assert position.elevation_deg == pytest.approx(expected_elevation, abs=0.3)


def test_cos_zenith_is_clamped_at_zero_below_the_horizon():
    midnight = utc(2025, 12, 21, 0, 0)
    position = solar.solar_position(midnight, STOCKHOLM_LAT, STOCKHOLM_LON)

    assert not position.is_up
    assert position.cos_zenith == 0.0


# ------------------------------------------------------------
# Daylight window
# ------------------------------------------------------------

@pytest.mark.parametrize("date,expected_hours", [
    (utc(2025, 10, 31), 8.72),
    (utc(2025, 6, 21), 18.27),
    (utc(2025, 12, 21), 5.75),
])
def test_stockholm_daylight_hours(date, expected_hours):
    actual = solar.daylight_hours(date, STOCKHOLM_LAT, STOCKHOLM_LON)
    assert actual == pytest.approx(expected_hours, abs=0.1)


def test_daylight_window_matches_a_minute_by_minute_scan():
    date = utc(2025, 10, 31)
    start = datetime(2025, 10, 31, tzinfo=timezone.utc)

    up = [
        minute
        for minute in range(24 * 60)
        if solar.solar_position(
            start + timedelta(minutes=minute), STOCKHOLM_LAT, STOCKHOLM_LON
        ).is_up
    ]

    scanned_hours = (up[-1] - up[0] + 1) / 60.0

    assert scanned_hours == pytest.approx(
        solar.daylight_hours(date, STOCKHOLM_LAT, STOCKHOLM_LON), abs=0.1
    )


def test_polar_night_and_midnight_sun():
    assert solar.daylight_hours(utc(2025, 12, 21), 80.0) == 0.0
    assert solar.daylight_hours(utc(2025, 6, 21), 80.0) == 24.0


def test_equator_daylight_is_about_twelve_hours_year_round():
    for month in range(1, 13):
        hours = solar.daylight_hours(utc(2025, month, 15), 0.0)
        assert hours == pytest.approx(12.0, abs=0.1)


# ------------------------------------------------------------
# Clear-sky irradiance
# ------------------------------------------------------------

@pytest.mark.parametrize("date,expected_ghi", [
    (utc(2025, 10, 31), 259.0),
    (utc(2025, 6, 21), 827.0),
    (utc(2025, 12, 21), 87.0),
])
def test_stockholm_haurwitz_peak(date, expected_ghi):
    _, position = find_solar_noon(date, STOCKHOLM_LAT, STOCKHOLM_LON)
    assert solar.haurwitz_ghi(position) == pytest.approx(expected_ghi, rel=0.05)


@pytest.mark.parametrize("date,expected_ghi", [
    (utc(2025, 10, 31), 259.0),
    (utc(2025, 6, 21), 827.0),
])
def test_stockholm_ineichen_peak_agrees_with_haurwitz(date, expected_ghi):
    moment, position = find_solar_noon(date, STOCKHOLM_LAT, STOCKHOLM_LON)

    ghi = solar.ineichen_ghi(
        position, moment, altitude_m=STOCKHOLM_ALT_M, linke_turbidity=3.0
    )

    assert ghi == pytest.approx(expected_ghi, rel=0.08)


def test_seasonal_swing_is_large_at_stockholm():
    """The whole point: late October is not midsummer."""
    def peak(date):
        moment, position = find_solar_noon(date, STOCKHOLM_LAT, STOCKHOLM_LON)
        return solar.ineichen_ghi(
            position, moment, altitude_m=STOCKHOLM_ALT_M, linke_turbidity=3.0
        )

    june = peak(utc(2025, 6, 21))
    october = peak(utc(2025, 10, 31))
    december = peak(utc(2025, 12, 21))

    assert june / october > 2.5
    assert october > december


def test_clear_sky_is_zero_at_night():
    midnight = utc(2025, 10, 31, 0, 0)
    position = solar.solar_position(midnight, STOCKHOLM_LAT, STOCKHOLM_LON)

    assert solar.haurwitz_ghi(position) == 0.0
    assert solar.ineichen_ghi(position, midnight) == 0.0


def test_clear_sky_never_exceeds_extraterrestrial_horizontal():
    start = datetime(2025, 6, 21, tzinfo=timezone.utc)

    for minute in range(0, 24 * 60, 5):
        moment = start + timedelta(minutes=minute)
        position = solar.solar_position(moment, STOCKHOLM_LAT, STOCKHOLM_LON)
        ceiling = solar.extraterrestrial_irradiance(moment) * position.cos_zenith

        for ghi in (
            solar.haurwitz_ghi(position),
            solar.ineichen_ghi(position, moment, altitude_m=STOCKHOLM_ALT_M),
        ):
            assert ghi <= ceiling + 1e-9


def test_higher_turbidity_lowers_irradiance():
    moment, position = find_solar_noon(utc(2025, 6, 21), STOCKHOLM_LAT, STOCKHOLM_LON)

    clear = solar.ineichen_ghi(position, moment, linke_turbidity=2.0)
    hazy = solar.ineichen_ghi(position, moment, linke_turbidity=6.0)

    assert hazy < clear


def test_higher_altitude_raises_irradiance():
    moment, position = find_solar_noon(utc(2025, 6, 21), STOCKHOLM_LAT, STOCKHOLM_LON)

    sea_level = solar.ineichen_ghi(position, moment, altitude_m=0.0)
    mountain = solar.ineichen_ghi(position, moment, altitude_m=2000.0)

    assert mountain > sea_level


def test_unknown_clear_sky_model_raises():
    moment, position = find_solar_noon(utc(2025, 6, 21), STOCKHOLM_LAT, STOCKHOLM_LON)

    with pytest.raises(ValueError, match="Unknown clear-sky model"):
        solar.clear_sky_ghi(position, moment, model="guesswork")


# ------------------------------------------------------------
# Airmass
# ------------------------------------------------------------

def test_airmass_is_one_overhead_and_grows_toward_the_horizon():
    assert solar.relative_airmass(0.0) == pytest.approx(1.0, abs=0.01)
    assert solar.relative_airmass(60.0) == pytest.approx(2.0, abs=0.05)
    assert solar.relative_airmass(85.0) > 10.0
    assert not math.isfinite(solar.relative_airmass(90.0))


# ------------------------------------------------------------
# Diffuse split
# ------------------------------------------------------------

def test_erbs_split_reconstructs_ghi():
    moment, position = find_solar_noon(utc(2025, 6, 21), STOCKHOLM_LAT, STOCKHOLM_LON)
    ghi = solar.ineichen_ghi(position, moment, altitude_m=STOCKHOLM_ALT_M)

    dni, dhi = solar.erbs_split(ghi, position, moment)

    assert dni * position.cos_zenith + dhi == pytest.approx(ghi, rel=1e-9)


def test_overcast_sky_is_almost_entirely_diffuse():
    moment, position = find_solar_noon(utc(2025, 6, 21), STOCKHOLM_LAT, STOCKHOLM_LON)
    clear = solar.ineichen_ghi(position, moment, altitude_m=STOCKHOLM_ALT_M)

    clear_dni, clear_dhi = solar.erbs_split(clear, position, moment)
    cloudy_dni, cloudy_dhi = solar.erbs_split(clear * 0.12, position, moment)

    assert clear_dhi / clear < 0.3
    assert cloudy_dhi / (clear * 0.12) > 0.9
    assert cloudy_dni < clear_dni


def test_erbs_split_is_zero_in_the_dark():
    midnight = utc(2025, 10, 31, 0, 0)
    position = solar.solar_position(midnight, STOCKHOLM_LAT, STOCKHOLM_LON)

    assert solar.erbs_split(0.0, position, midnight) == (0.0, 0.0)


# ------------------------------------------------------------
# Transposition
# ------------------------------------------------------------

def components_at(moment, ghi_scale=1.0):
    position = solar.solar_position(moment, STOCKHOLM_LAT, STOCKHOLM_LON)
    ghi = solar.ineichen_ghi(position, moment, altitude_m=STOCKHOLM_ALT_M) * ghi_scale
    dni, dhi = solar.erbs_split(ghi, position, moment)

    return solar.IrradianceComponents(
        ghi=ghi, dni=dni, dhi=dhi, position=position,
        clear_sky_ghi=ghi, clear_sky_index=1.0,
    )


def test_angle_of_incidence_is_zero_when_plane_faces_the_sun():
    moment, position = find_solar_noon(utc(2025, 10, 31), STOCKHOLM_LAT, STOCKHOLM_LON)

    cos_aoi = solar.cos_angle_of_incidence(
        position, tilt_deg=position.zenith_deg, azimuth_deg=position.azimuth_deg
    )

    assert cos_aoi == pytest.approx(1.0, abs=1e-9)


def test_tilt_beats_horizontal_on_a_low_sun_day():
    """At 59 N in late October a tilted array collects far more than a flat one."""
    moment, _ = find_solar_noon(utc(2025, 10, 31), STOCKHOLM_LAT, STOCKHOLM_LON)
    components = components_at(moment)

    horizontal = solar.plane_of_array_irradiance(components, 0.0, 180.0)
    tilted = solar.plane_of_array_irradiance(components, 45.0, 180.0)

    assert horizontal == pytest.approx(components.ghi, rel=1e-9)
    assert tilted > horizontal * 1.3


def test_facing_away_from_the_sun_collects_no_beam():
    """
    A steep north-facing plane at midday gets sky diffuse and ground reflection
    but no beam at all, so it sits far below a south-facing one — and below
    GHI, though above DHI alone thanks to the ground term.
    """
    moment, _ = find_solar_noon(utc(2025, 6, 21), STOCKHOLM_LAT, STOCKHOLM_LON)
    components = components_at(moment)

    north_facing = solar.plane_of_array_irradiance(components, 80.0, 0.0)
    south_facing = solar.plane_of_array_irradiance(components, 80.0, 180.0)

    assert solar.cos_angle_of_incidence(components.position, 80.0, 0.0) < 0.0
    assert 0.0 < north_facing < components.ghi
    assert north_facing < south_facing * 0.4


def test_transposition_is_zero_at_night():
    midnight = utc(2025, 10, 31, 0, 0)
    components = components_at(midnight)

    assert solar.plane_of_array_irradiance(components, 45.0, 180.0) == 0.0


def test_default_orientation_follows_the_hemisphere():
    assert solar.default_tilt_deg(STOCKHOLM_LAT) == pytest.approx(STOCKHOLM_LAT)
    assert solar.default_tilt_deg(75.0) == 60.0
    assert solar.default_azimuth_deg(STOCKHOLM_LAT) == 180.0
    assert solar.default_azimuth_deg(-33.9) == 0.0


# ------------------------------------------------------------
# Beam ceiling
# ------------------------------------------------------------

def test_ineichen_dni_falls_away_at_extreme_airmass():
    moment, position = find_solar_noon(utc(2025, 6, 21), STOCKHOLM_LAT, STOCKHOLM_LON)
    summer_noon = solar.ineichen_dni(
        position, moment, altitude_m=STOCKHOLM_ALT_M, linke_turbidity=2.5
    )

    low_sun = solar.solar_position(
        datetime(2025, 12, 21, 13, 11, tzinfo=timezone.utc),
        STOCKHOLM_LAT, STOCKHOLM_LON,
    )
    winter_grazing = solar.ineichen_dni(
        low_sun, utc(2025, 12, 21), altitude_m=STOCKHOLM_ALT_M, linke_turbidity=2.5
    )

    assert 800.0 < summer_noon < 1000.0
    assert 0.0 < winter_grazing < 100.0
    assert winter_grazing < summer_noon * 0.1


def test_beam_ceiling_caps_the_erbs_split_at_low_sun():
    """
    Erbs infers DNI by dividing by cos(zenith), which blows up near the
    horizon: two degrees up in a Nordic December it reports a beam of
    ~1180 W/m², through some twenty-five airmasses.
    """
    moment = datetime(2025, 12, 21, 13, 11, tzinfo=timezone.utc)
    position = solar.solar_position(moment, STOCKHOLM_LAT, STOCKHOLM_LON)

    ghi = solar.ineichen_ghi(
        position, moment, altitude_m=STOCKHOLM_ALT_M, linke_turbidity=2.5
    )
    dni, dhi = solar.erbs_split(ghi, position, moment)

    raw = solar.IrradianceComponents(
        ghi=ghi, dni=dni, dhi=dhi, position=position,
        clear_sky_ghi=ghi, clear_sky_index=1.0,
    )
    capped = solar.limit_beam_to_clear_sky(
        raw, moment, altitude_m=STOCKHOLM_ALT_M, linke_turbidity=2.5
    )

    assert dni > 1000.0                       # the problem
    assert capped.dni < dni * 0.1             # the fix
    assert capped.ghi == pytest.approx(ghi)   # GHI is preserved exactly
    assert capped.dhi > raw.dhi               # the excess arrives as diffuse


def test_beam_ceiling_preserves_ghi_whenever_it_bites():
    start = datetime(2025, 12, 21, tzinfo=timezone.utc)

    for minute in range(0, 24 * 60, 5):
        moment = start + timedelta(minutes=minute)
        position = solar.solar_position(moment, STOCKHOLM_LAT, STOCKHOLM_LON)

        if not position.is_up:
            continue

        ghi = solar.ineichen_ghi(position, moment, altitude_m=STOCKHOLM_ALT_M)
        dni, dhi = solar.erbs_split(ghi, position, moment)
        capped = solar.limit_beam_to_clear_sky(
            solar.IrradianceComponents(
                ghi=ghi, dni=dni, dhi=dhi, position=position,
                clear_sky_ghi=ghi, clear_sky_index=1.0,
            ),
            moment, altitude_m=STOCKHOLM_ALT_M,
        )

        assert capped.dni * position.cos_zenith + capped.dhi == pytest.approx(
            ghi, rel=1e-9
        )
        assert capped.dni <= dni + 1e-9
        assert capped.dhi >= -1e-9


def test_beam_ceiling_leaves_a_high_sun_alone():
    moment, position = find_solar_noon(utc(2025, 6, 21), STOCKHOLM_LAT, STOCKHOLM_LON)

    ghi = solar.ineichen_ghi(position, moment, altitude_m=STOCKHOLM_ALT_M)
    dni, dhi = solar.erbs_split(ghi, position, moment)
    raw = solar.IrradianceComponents(
        ghi=ghi, dni=dni, dhi=dhi, position=position,
        clear_sky_ghi=ghi, clear_sky_index=1.0,
    )

    assert solar.limit_beam_to_clear_sky(
        raw, moment, altitude_m=STOCKHOLM_ALT_M
    ) is raw


def test_plane_of_array_never_exceeds_a_plausible_winter_beam():
    """
    The regression this exists for: a 45 degree array at 59 N in December used
    to report a plane-of-array peak of 737 W/m² from a GHI of 108.
    """
    from dertwin.devices.external.irradiance import ClearSkyIrradianceModel

    model = ClearSkyIrradianceModel(
        latitude=STOCKHOLM_LAT, longitude=STOCKHOLM_LON,
        altitude_m=STOCKHOLM_ALT_M, linke_turbidity=2.5,
    )

    start = datetime(2025, 12, 21, tzinfo=timezone.utc)
    peak_poa = 0.0

    for minute in range(0, 24 * 60):
        moment = start + timedelta(minutes=minute)
        model.advance(moment, minute * 60.0, 60.0)
        peak_poa = max(
            peak_poa,
            solar.plane_of_array_irradiance(model.get_components(), 45.0, 180.0),
        )

    assert peak_poa < 450.0
