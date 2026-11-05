"""
A live irradiance series: periodic re-read, and the stochastic fallback that
takes over when it goes stale.

Reference latitude 50 N / 10 E on the June solstice — a mid-latitude sun that
is well up at 12:00 UTC, so "the fallback still makes power" is a meaningful
assertion rather than an accident of the hour.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import pytest

from dertwin.devices.external.clouds import AR1CloudModel, SeriesCloudModel
from dertwin.devices.external.irradiance import (
    ClearSkyIrradianceModel,
    SeriesIrradianceModel,
    build_irradiance_model,
)
from dertwin.devices.external.series_feed import SeriesFeed
from dertwin.devices.external.timeseries import read_series_table

SITE = dict(latitude=50.0, longitude=10.0)

STEP_S = 60.0


def utc(hour, minute=0):
    return datetime(2025, 6, 21, hour, minute, tzinfo=timezone.utc)


def rows(first_hour, last_hour, value=600.0, column="ghi_w_m2", step_h=1):
    body = [f"timestamp,{column}"]

    for hour in range(first_hour, last_hour + 1, step_h):
        body.append(f"{utc(hour).isoformat().replace('+00:00', 'Z')},{value}")

    return "\n".join(body) + "\n"


def publish(path, body):
    """
    Write the file the way the contract asks a fetcher to: a temporary file in
    the same directory, then an atomic rename over the target.
    """

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body)
    os.replace(tmp, path)
    return path


def live_model(tmp_path, body=None, path_name="ghi.csv", **overrides):
    path = tmp_path / path_name

    if body is not None:
        publish(path, body)

    cfg = dict(
        SITE,
        series_csv=path_name,
        series_refresh_s=600.0,
        series_stale_after_s=1800.0,
        cloud={"seed": 7},
    )
    cfg.update(overrides)

    return build_irradiance_model(
        cfg, data_root=tmp_path, sim_start_utc_configured=True
    ), path


def walk(model, first, last, step_s=STEP_S):
    """Advance a model minute by minute, yielding (instant, GHI)."""

    when = first
    sim_time = 0.0

    while when <= last:
        model.advance(when, sim_time, step_s)
        yield when, model.get_irradiance()
        when += timedelta(seconds=step_s)
        sim_time += step_s


def trace(model, first, last, step_s=STEP_S):
    """
    Walk a model and record whether each sample came from the series or the
    fallback, so a test can point at the switch tick itself rather than
    guessing which minute it landed on.
    """

    samples = []

    for when, ghi in walk(model, first, last, step_s):
        samples.append((when, ghi, model.switch.on_fallback))

    return samples


def switch_index(samples):
    for index, (_, _, on_fallback) in enumerate(samples):
        if on_fallback:
            return index

    raise AssertionError("the model never left the series")


# ------------------------------------------------------------
# Re-reading a file that is still being written
# ------------------------------------------------------------

def test_a_live_feed_picks_up_appended_rows(tmp_path):
    model, path = live_model(tmp_path, rows(6, 10))

    list(walk(model, utc(9), utc(9, 30)))
    assert model.get_irradiance() == pytest.approx(600.0)

    publish(path, rows(6, 14, value=300.0))

    # Past the refresh interval, the new rows are in play.
    list(walk(model, utc(12), utc(12, 30)))
    assert model.get_irradiance() == pytest.approx(300.0)
    assert model.feed.rows == 9


def test_a_one_shot_feed_never_re_reads(tmp_path):
    model, path = live_model(
        tmp_path, rows(6, 20), series_refresh_s=0.0, series_stale_after_s=None
    )

    list(walk(model, utc(9), utc(9, 10)))
    assert model.get_irradiance() == pytest.approx(600.0)

    publish(path, rows(6, 20, value=50.0))

    list(walk(model, utc(12), utc(12, 30)))
    assert model.get_irradiance() == pytest.approx(600.0)
    assert model.feed.reload_count == 0


def test_an_unchanged_file_is_not_re_parsed(tmp_path):
    model, _ = live_model(tmp_path, rows(6, 20))

    list(walk(model, utc(8), utc(13)))

    assert model.feed.reload_count == 0


def test_a_replaced_file_is_read_whole_across_many_replacements(tmp_path):
    """
    An atomic rename cannot be observed half-done: every read either predates
    it or follows it. A hundred of them must leave a hundred good snapshots
    and no read error.
    """

    model, path = live_model(tmp_path, rows(6, 10), series_refresh_s=60.0)

    seen = set()
    when = utc(8)

    # The first advance only starts the refresh clock, so it is primed here
    # and the loop below is a hundred genuine re-reads.
    model.advance(when, 0.0, STEP_S)
    when += timedelta(seconds=120)

    for n in range(100):
        publish(path, rows(6, 10, value=100.0 + n))
        model.advance(when, 0.0, STEP_S)
        seen.add(round(model.get_irradiance(), 6))
        when += timedelta(seconds=120)

    assert model.feed.reload_count == 100
    assert len(seen) == 100


# ------------------------------------------------------------
# Going stale
# ------------------------------------------------------------

def test_a_stale_series_falls_back_to_a_stochastic_sky(tmp_path):
    model, _ = live_model(tmp_path, rows(6, 8))

    list(walk(model, utc(11), utc(12)))

    assert model.switch.on_fallback
    # Daylight, so the fallback has to make irradiance: neither dark nor the
    # cloudless curve.
    assert model.get_irradiance() > 0.0
    assert 0.0 < model.get_components().clear_sky_index < 1.0


def test_the_last_row_is_held_through_the_grace_period(tmp_path):
    model, _ = live_model(tmp_path, rows(6, 10), series_stale_after_s=3600.0)

    model.advance(utc(10, 30), 0.0, STEP_S)

    assert not model.switch.on_fallback
    assert model.get_irradiance() == pytest.approx(600.0)

    model.advance(utc(11, 30), 3600.0, STEP_S)

    assert model.switch.on_fallback


def test_a_missing_file_at_startup_runs_on_the_stochastic_model(tmp_path):
    model, _ = live_model(tmp_path, body=None, path_name="never-written.csv")

    list(walk(model, utc(11), utc(12)))

    assert not model.feed.has_data
    assert model.switch.on_fallback
    assert model.get_irradiance() > 0.0


def test_a_missing_file_is_still_an_error_in_one_shot_mode(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        live_model(
            tmp_path,
            body=None,
            path_name="absent.csv",
            series_refresh_s=0.0,
            series_stale_after_s=None,
        )


def test_a_feed_that_appears_later_is_picked_up(tmp_path):
    model, path = live_model(tmp_path, body=None)

    list(walk(model, utc(10), utc(10, 30)))
    assert model.switch.on_fallback

    publish(path, rows(6, 14, value=420.0))

    list(walk(model, utc(11), utc(11, 30)))

    assert not model.switch.on_fallback
    assert model.feed.has_data


def test_recovery_returns_to_the_series(tmp_path):
    model, path = live_model(tmp_path, rows(6, 8), series_handover_tau_s=0.0)

    list(walk(model, utc(11), utc(12)))
    assert model.switch.on_fallback

    publish(path, rows(6, 16, value=515.0))

    list(walk(model, utc(13), utc(13, 30)))

    assert not model.switch.on_fallback
    assert model.get_irradiance() == pytest.approx(515.0)
    assert model.switch.transitions == 2


def test_each_transition_is_logged_once(tmp_path, caplog):
    model, path = live_model(tmp_path, rows(6, 8))

    with caplog.at_level(logging.INFO):
        list(walk(model, utc(11), utc(12)))
        publish(path, rows(6, 16))
        list(walk(model, utc(13), utc(14)))

    assert caplog.text.count("falling back to the stochastic cloud model") == 1
    assert caplog.text.count("leaving the stochastic cloud model") == 1


# ------------------------------------------------------------
# Continuity at the switch
# ------------------------------------------------------------

def largest_step(samples):
    return max(
        (abs(b - a) for a, b in zip(samples, samples[1:])), default=0.0
    )


def test_the_switch_into_the_fallback_adds_no_step_of_its_own(tmp_path):
    """
    Seeding the model's intraday perturbation with the last observed index
    means the switch tick moves the sky no further than the model moves it on
    any other tick.
    """

    model, _ = live_model(tmp_path, rows(6, 10, value=400.0))

    samples = trace(model, utc(9), utc(12))
    index = switch_index(samples)

    at_switch = abs(samples[index][1] - samples[index - 1][1])
    stochastic = [ghi for _, ghi, on_fallback in samples if on_fallback]

    # The stochastic segment's own largest minute-to-minute move is the only
    # fair yardstick: the handover must not exceed what the model does anyway.
    assert at_switch <= largest_step(stochastic)


def test_disabling_the_handover_makes_the_switch_a_plain_jump(tmp_path):
    seeded, _ = live_model(tmp_path, rows(6, 10, value=400.0))
    plain, _ = live_model(
        tmp_path, rows(6, 10, value=400.0),
        path_name="plain.csv", series_handover_tau_s=0.0,
    )

    def step_across(model):
        samples = trace(model, utc(9), utc(12))
        index = switch_index(samples)
        return abs(samples[index][1] - samples[index - 1][1])

    assert step_across(seeded) < step_across(plain)


def test_the_handover_decays_onto_the_measurement(tmp_path):
    model, path = live_model(tmp_path, rows(6, 8, value=400.0))

    list(walk(model, utc(11), utc(12)))
    assert model.switch.on_fallback

    publish(path, rows(6, 16, value=400.0))
    list(walk(model, utc(13), utc(13, 1)))

    assert not model.switch.on_fallback
    assert model.handover.active

    # Four time constants on, the offset is gone and the series is reported
    # exactly.
    list(walk(model, utc(14), utc(15)))

    assert not model.handover.active
    assert model.get_irradiance() == pytest.approx(400.0)


# ------------------------------------------------------------
# Bad rows, duplicates, gaps
# ------------------------------------------------------------

def test_a_malformed_row_does_not_kill_the_run(tmp_path):
    # No handover, so the assertion below is about the rows that parsed and
    # not about an offset still decaying onto them.
    model, path = live_model(tmp_path, rows(6, 10), series_handover_tau_s=0.0)

    body = (
        "timestamp,ghi_w_m2\n"
        "2025-06-21T06:00:00Z,100\n"
        "2025-06-21T07:00:00Z,not-a-number\n"
        "nonsense,300\n"
        "2025-06-21T16:00:00Z,100\n"
    )
    publish(path, body)

    # The run keeps stepping across the re-read either way; what matters is
    # that it is still running afterwards, on the rows that did parse.
    samples = [ghi for _, ghi in walk(model, utc(12), utc(13))]

    assert model.feed.has_data
    assert model.feed.rows == 2
    assert model.feed.reload_count == 1
    assert samples[-1] == pytest.approx(100.0)
    assert not model.switch.on_fallback


def test_a_file_that_turns_unusable_keeps_the_last_good_snapshot(tmp_path):
    model, path = live_model(tmp_path, rows(6, 20, value=250.0))

    list(walk(model, utc(9), utc(9, 10)))
    publish(path, "timestamp,ghi_w_m2\n")

    list(walk(model, utc(12), utc(12, 30)))

    # The snapshot stays authoritative until it goes stale by time, which is
    # what stops a fetcher's bad minute from flapping the whole site.
    assert model.get_irradiance() == pytest.approx(250.0)
    assert not model.switch.on_fallback


def test_the_last_row_wins_for_a_duplicate_timestamp(tmp_path):
    body = (
        "timestamp,ghi_w_m2\n"
        "2025-06-21T10:00:00Z,100\n"
        "2025-06-21T10:00:00Z,700\n"
    )
    table = read_series_table(publish(tmp_path / "dup.csv", body), "ghi_w_m2")

    assert table.values_at(utc(10)) == {"ghi_w_m2": pytest.approx(700.0)}


def test_out_of_order_rows_are_sorted(tmp_path):
    body = (
        "timestamp,ghi_w_m2\n"
        "2025-06-21T12:00:00Z,800\n"
        "2025-06-21T08:00:00Z,400\n"
    )
    table = read_series_table(publish(tmp_path / "mixed.csv", body), "ghi_w_m2")

    assert table.start_utc == utc(8)
    assert table.end_utc == utc(12)
    assert table.values_at(utc(10)) == {"ghi_w_m2": pytest.approx(600.0)}


def test_a_gap_wider_than_the_limit_is_missing_data(tmp_path):
    body = (
        "timestamp,ghi_w_m2\n"
        "2025-06-21T06:00:00Z,400\n"
        "2025-06-21T16:00:00Z,400\n"
    )
    model, _ = live_model(
        tmp_path, body, series_max_gap_s=7200.0, series_stale_after_s=600.0
    )

    model.advance(utc(11), 0.0, STEP_S)

    assert model.switch.on_fallback
    assert model.get_irradiance() > 0.0


def test_a_gap_is_bridged_by_default(tmp_path):
    body = (
        "timestamp,ghi_w_m2\n"
        "2025-06-21T06:00:00Z,400\n"
        "2025-06-21T16:00:00Z,400\n"
    )
    model, _ = live_model(tmp_path, body)

    model.advance(utc(11), 0.0, STEP_S)

    assert not model.switch.on_fallback
    assert model.get_irradiance() == pytest.approx(400.0)


# ------------------------------------------------------------
# Columns
# ------------------------------------------------------------

def test_a_sky_index_series_matches_the_equivalent_ghi_series(tmp_path):
    """
    kt and GHI are two ways of writing the same sky, and the simulator must
    not be able to tell them apart: GHI = clear-sky GHI x kt.
    """

    kt_body = [
        "timestamp,kt",
        f"{utc(6).isoformat().replace('+00:00', 'Z')},0.80",
        f"{utc(12).isoformat().replace('+00:00', 'Z')},0.45",
        f"{utc(18).isoformat().replace('+00:00', 'Z')},0.65",
    ]
    kt_model, _ = live_model(
        tmp_path, "\n".join(kt_body) + "\n",
        path_name="kt.csv", series_column="kt",
    )

    assert isinstance(kt_model, ClearSkyIrradianceModel)
    assert isinstance(kt_model.cloud_model, SeriesCloudModel)

    # Take the clear-sky curve from the model itself and multiply it out.
    instants = [utc(7) + timedelta(minutes=30 * n) for n in range(20)]
    ghi_rows = ["timestamp,ghi_w_m2"]
    kt_reported = []

    for when in instants:
        kt_model.advance(when, 0.0, STEP_S)
        components = kt_model.get_components()
        kt_reported.append(components.ghi)
        ghi_rows.append(
            f"{when.isoformat().replace('+00:00', 'Z')},{components.ghi!r}"
        )

    ghi_model, _ = live_model(
        tmp_path, "\n".join(ghi_rows) + "\n", path_name="equiv.csv"
    )

    assert isinstance(ghi_model, SeriesIrradianceModel)

    for when, expected in zip(instants, kt_reported):
        ghi_model.advance(when, 0.0, STEP_S)
        assert ghi_model.get_irradiance() == pytest.approx(expected, rel=1e-9)


def test_a_stale_sky_index_series_falls_back_too(tmp_path):
    body = (
        "timestamp,kt\n"
        "2025-06-21T06:00:00Z,0.9\n"
        "2025-06-21T08:00:00Z,0.9\n"
    )
    model, _ = live_model(tmp_path, body, path_name="kt.csv", series_column="kt")

    list(walk(model, utc(11), utc(12)))

    assert model.cloud_model.switch.on_fallback
    assert isinstance(model.cloud_model.fallback, AR1CloudModel)
    assert model.get_irradiance() > 0.0
    assert model.get_components().clear_sky_index < 1.0


def test_measured_beam_and_diffuse_columns_are_used_as_given(tmp_path):
    body = (
        "timestamp,ghi_w_m2,dni_w_m2,dhi_w_m2\n"
        "2025-06-21T10:00:00Z,600,700,150\n"
        "2025-06-21T14:00:00Z,600,700,150\n"
    )
    model, _ = live_model(tmp_path, body)

    model.advance(utc(12), 0.0, STEP_S)
    components = model.get_components()

    assert components.ghi == pytest.approx(600.0)
    assert components.dni == pytest.approx(700.0)
    assert components.dhi == pytest.approx(150.0)


def test_a_diffuse_column_alone_gives_the_beam(tmp_path):
    body = (
        "timestamp,ghi_w_m2,dhi_w_m2\n"
        "2025-06-21T10:00:00Z,600,150\n"
        "2025-06-21T14:00:00Z,600,150\n"
    )
    model, _ = live_model(tmp_path, body)

    model.advance(utc(12), 0.0, STEP_S)
    components = model.get_components()

    cos_z = components.position.cos_zenith

    assert components.dhi == pytest.approx(150.0)
    assert (
        components.dni * cos_z + components.dhi
        == pytest.approx(600.0, rel=1e-9)
    )


def test_components_fall_back_to_the_erbs_split_without_them(tmp_path):
    model, _ = live_model(tmp_path, rows(6, 20, value=600.0))

    model.advance(utc(12), 0.0, STEP_S)
    components = model.get_components()

    cos_z = components.position.cos_zenith

    assert (
        components.dni * cos_z + components.dhi
        == pytest.approx(components.ghi, rel=1e-9)
    )


# ------------------------------------------------------------
# One-shot mode is untouched
# ------------------------------------------------------------

def test_one_shot_over_a_simulated_day_reports_only_the_series(tmp_path):
    """
    With none of the new keys, the model replays the file and nothing else:
    no fallback object exists, so no stochastic value can reach the output,
    and instants outside the series are dark exactly as before.
    """

    model, _ = live_model(
        tmp_path,
        rows(6, 18, value=550.0),
        series_refresh_s=0.0,
        series_stale_after_s=None,
    )

    assert model.fallback is None

    dark = 0
    lit = 0

    for when, ghi in walk(model, utc(0), utc(23, 59), step_s=300.0):
        if utc(6) <= when <= utc(18):
            assert ghi == pytest.approx(550.0)
            assert model.get_components().clear_sky_index == 1.0
            lit += 1
        else:
            assert ghi == 0.0
            dark += 1

    assert lit and dark


def test_one_shot_is_the_default(tmp_path):
    path = publish(tmp_path / "ghi.csv", rows(6, 18))

    model = build_irradiance_model(
        dict(SITE, series_csv="ghi.csv"),
        data_root=tmp_path,
        sim_start_utc_configured=True,
    )

    assert model.fallback is None
    assert not model.feed.is_live
    assert model.feed.stale_after_s is None


# ------------------------------------------------------------
# Feed validation
# ------------------------------------------------------------

@pytest.mark.parametrize("kwargs,match", [
    ({"refresh_s": -1.0}, "refresh_s must be non-negative"),
    ({"stale_after_s": -1.0}, "stale_after_s must be non-negative"),
])
def test_bad_feed_settings_are_rejected(tmp_path, kwargs, match):
    path = publish(tmp_path / "ghi.csv", rows(6, 10))

    with pytest.raises(ValueError, match=match):
        SeriesFeed(path, primary="ghi_w_m2", **kwargs)


def test_a_feed_needs_a_path_or_a_table():
    with pytest.raises(ValueError, match="either a path or a table"):
        SeriesFeed(None, primary="ghi_w_m2")


# ------------------------------------------------------------
# The handover has to be continuous in what a PV array sees,
# not only in GHI
# ------------------------------------------------------------

def poa(components, tilt_deg=45.0, azimuth_deg=180.0):
    """Plane-of-array irradiance for a steep south-facing array."""

    from dertwin.devices.external import solar

    return solar.plane_of_array_irradiance(
        components, tilt_deg=tilt_deg, azimuth_deg=azimuth_deg, albedo=0.2
    )


def returning_model(tmp_path, handover_tau_s=900.0, **overrides):
    """
    A model that goes stale and is then handed a file covering the present
    again — the return switch, with the handover still decaying.
    """

    model, path = live_model(
        tmp_path, rows(6, 8, value=140.0),
        series_handover_tau_s=handover_tau_s, **overrides,
    )

    # Out to the fallback first.
    list(walk(model, utc(10), utc(11)))
    assert model.switch.on_fallback

    publish(path, rows(6, 20, value=140.0))

    return model, path


def test_the_handover_splits_the_irradiance_it_reports(tmp_path):
    """
    Erbs is strongly non-linear in the clear-sky index, so the split has to be
    taken from the GHI being reported. Splitting the file's GHI and scaling
    the components afterwards gives a sky whose GHI is right and whose diffuse
    fraction belongs to a much cloudier one.
    """

    from dertwin.devices.external import solar

    model, _ = returning_model(tmp_path)

    list(walk(model, utc(12), utc(12, 1)))

    assert not model.switch.on_fallback
    assert model.handover.active

    components = model.get_components()
    expected_dni, expected_dhi = solar.erbs_split(
        components.ghi, components.position, model.last_utc_time
    )

    assert components.dni == pytest.approx(expected_dni, rel=1e-9)
    assert components.dhi == pytest.approx(expected_dhi, rel=1e-9)

    # And the file's own GHI is well below what is being reported, so this is
    # a real handover and not a trivially satisfied assertion.
    raw = model.feed.sample(model.last_utc_time)[model.feed.primary]
    assert components.ghi > raw * 1.2


def low_sun_return(tmp_path, handover_tau_s, path_name="ghi.csv"):
    """
    A return switch with the sun low.

    Low sun is where a mis-ordered handover does its damage: a steep array is
    then dominated by beam, and the beam fraction is the part of the split
    that moves most with the clear-sky index — at 14 degrees of elevation the
    diffuse fraction runs 0.94 at kt 0.5 and 0.61 at kt 0.85.
    """

    model, path = live_model(
        tmp_path, rows(4, 5, value=100.0),
        path_name=path_name, series_handover_tau_s=handover_tau_s,
    )

    samples = []

    for when, _ in walk(model, utc(5), utc(7)):
        samples.append((when, model.get_components(),
                        model.switch.on_fallback))

        if when == utc(6):
            publish(path, rows(4, 12, value=100.0))

    out_at = next(i for i, s in enumerate(samples) if s[2])
    back_at = next(i for i, s in enumerate(samples) if i > out_at and not s[2])

    return samples, out_at, back_at


def test_the_return_switch_does_not_step_the_plane_of_array(tmp_path):
    """
    The gate property, at the quantity a PV array actually integrates: the
    return tick must move POA no further than the stochastic model moves it on
    a tick of its own.
    """

    samples, out_at, back_at = low_sun_return(tmp_path, 900.0)

    at_return = abs(poa(samples[back_at][1]) - poa(samples[back_at - 1][1]))
    stochastic = samples[out_at:back_at]
    inside = max(
        (abs(poa(b[1]) - poa(a[1])) for a, b in zip(stochastic, stochastic[1:])),
        default=0.0,
    )

    assert inside > 0.0
    assert at_return <= inside

    # And in absolute terms: GHI moves well under a percent per tick here, so
    # POA has to as well. Comparing against the stochastic segment alone is
    # too loose a bound when the clear-sky curve is climbing fast.
    before = poa(samples[back_at - 1][1])
    assert at_return <= 0.02 * before


def test_the_return_switch_does_not_step_the_diffuse_fraction(tmp_path):
    """
    The invariant underneath the POA assertion, and free of any array
    geometry: the sky handed over at the switch has to be the same *kind* of
    sky, not merely one with the same GHI.
    """

    samples, _, back_at = low_sun_return(tmp_path, 900.0)

    def diffuse_fraction(components):
        return components.dhi / components.ghi

    before = diffuse_fraction(samples[back_at - 1][1])
    after = diffuse_fraction(samples[back_at][1])

    assert before == pytest.approx(after, abs=0.01)


def test_disabling_the_handover_steps_the_plane_of_array(tmp_path):
    """The contrast: without the handover the return is a plain jump."""

    def step_across(handover_tau_s, path_name):
        samples, _, back_at = low_sun_return(
            tmp_path, handover_tau_s, path_name=path_name
        )
        return abs(poa(samples[back_at][1]) - poa(samples[back_at - 1][1]))

    assert step_across(900.0, "ghi.csv") < step_across(0.0, "plain.csv")


def test_measured_components_keep_their_ratio_through_a_handover(tmp_path):
    """
    A file that carries DNI and DHI is believed, so those are scaled with GHI
    rather than re-split: the handover must not quietly replace a measured
    beam fraction with a modelled one.
    """

    body = (
        "timestamp,ghi_w_m2,dni_w_m2,dhi_w_m2\n"
        "2025-06-21T06:00:00Z,140,200,80\n"
        "2025-06-21T08:00:00Z,140,200,80\n"
    )
    model, path = live_model(tmp_path, body, series_handover_tau_s=900.0)

    list(walk(model, utc(10), utc(11)))
    assert model.switch.on_fallback

    publish(path, (
        "timestamp,ghi_w_m2,dni_w_m2,dhi_w_m2\n"
        "2025-06-21T06:00:00Z,140,200,80\n"
        "2025-06-21T20:00:00Z,140,200,80\n"
    ))

    list(walk(model, utc(12), utc(12, 1)))

    assert not model.switch.on_fallback
    assert model.handover.active

    components = model.get_components()

    # 200/80 in the file; the beam cap may move it, so only an untouched beam
    # is held to the ratio exactly.
    assert components.dni / components.dhi == pytest.approx(200.0 / 80.0)
    assert components.ghi > 140.0 * 1.2


def test_backfilled_rows_older_than_now_are_not_replayed(tmp_path):
    """
    The README contract says only the rows bracketing the simulated instant
    matter. A file replaced with a backfill of the outage must therefore leave
    no trace of those older rows in the output: the clock does not rewind.
    """

    model, path = live_model(
        tmp_path, rows(6, 8, value=140.0), series_handover_tau_s=0.0
    )

    list(walk(model, utc(10), utc(11)))
    assert model.switch.on_fallback

    # Rows covering the outage carry a value the run must never report.
    sentinel = 999.0
    body = ["timestamp,ghi_w_m2"]
    for hour in range(6, 21):
        value = sentinel if hour < 12 else 140.0
        body.append(f"{utc(hour).isoformat().replace('+00:00', 'Z')},{value}")
    publish(path, "\n".join(body) + "\n")

    reported = [ghi for _, ghi in walk(model, utc(12), utc(14))]

    assert not model.switch.on_fallback
    assert reported
    assert all(ghi == pytest.approx(140.0) for ghi in reported)
    assert max(reported) < sentinel
