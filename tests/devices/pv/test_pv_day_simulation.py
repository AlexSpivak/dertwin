"""
Deterministic end-to-end PV day, driven by the real engine.

Reference site: Stockholm, 59.3293 N / 18.0686 E, 44 m, 36 kW DC / 30 kW AC on
a 45 degree south-facing array.

These are the tests that would have failed before stage 2: the old model gave
every day of the year a 12-hour window and a 1000 W/m² peak, so 31 October at
59 N produced roughly four times the energy it should.
"""

import pytest

from dertwin.controllers.site_controller import SiteController


STOCKHOLM_IRRADIANCE = {
    "latitude": 59.3293,
    "longitude": 18.0686,
    "timezone": "Europe/Stockholm",
    "altitude_m": 44.0,
    "linke_turbidity": 3.0,
}

STEP_S = 60.0
TICKS_PER_DAY = int(24 * 3600 / STEP_S)


def build_site(date="2025-10-31", cloud=None, **pv_overrides):
    pv = {
        "type": "inverter",
        "asset_id": "pv-0",
        "ac_rating_kw": 30.0,
        "dc_capacity_kw": 36.0,
        "tilt_deg": 45.0,
        "azimuth_deg": 180.0,
    }
    pv.update(pv_overrides)

    cfg = {
        "site_name": "stockholm-pv",
        "step": STEP_S,
        "real_time": False,
        "sim_start_utc": f"{date}T00:00:00Z",
        "external_models": {
            "irradiance": dict(
                STOCKHOLM_IRRADIANCE,
                cloud=cloud if cloud is not None else {"enabled": False},
            ),
            "ambient_temperature": {"mean": 8.0, "amplitude": 4.0},
        },
        "assets": [pv],
    }

    site = SiteController(cfg)
    site.build()
    return site


async def run_day(site, ticks=TICKS_PER_DAY):
    pv = site._devices_by_type["inverter"][0]
    history = []

    for _ in range(ticks):
        await site.engine.step_once()
        telemetry = pv.get_telemetry()
        history.append({
            "utc": site.engine.utc_time,
            "ghi": site.external_models.irradiance_model.get_components().ghi,
            "ac_kw": telemetry.total_active_power,
            "dc_kw": telemetry.total_input_power,
            "today_kwh": telemetry.today_output_energy,
        })

    return history


def daylight_hours(history):
    lit = [row for row in history if row["ghi"] > 0.0]
    if not lit:
        return 0.0
    first = history.index(lit[0])
    last = history.index(lit[-1])
    return (last - first + 1) * STEP_S / 3600.0


# ------------------------------------------------------------
# A late-October day looks like late October
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_october_day_has_the_right_daylight_window_and_peak():
    site = build_site("2025-10-31")
    history = await run_day(site)

    assert daylight_hours(history) == pytest.approx(8.72, abs=0.2)

    # Clear-sky GHI peaks near 259 W/m², and a 45 degree tilt collects more
    peak_ghi = max(row["ghi"] for row in history)
    assert peak_ghi == pytest.approx(259.0, rel=0.08)

    # ~700 W/m² in-plane on a 36 kW DC array: a healthy clear autumn day,
    # still comfortably short of the 30 kW AC rating
    peak_ac = max(row["ac_kw"] for row in history)
    assert 15.0 < peak_ac < 25.0


@pytest.mark.asyncio
async def test_midsummer_produces_far_more_than_late_october():
    october = await run_day(build_site("2025-10-31"))
    june = await run_day(build_site("2025-06-21"))

    october_kwh = october[-1]["today_kwh"]
    june_kwh = june[-1]["today_kwh"]

    # A 45 degree tilt deliberately flattens the seasonal curve at this
    # latitude — it is matched to the low autumn sun and mismatched to the high
    # summer one — so daily energy swings by roughly 2x even though peak GHI
    # swings by more than 3x.
    assert june_kwh > october_kwh * 2.0
    assert daylight_hours(june) > daylight_hours(october) * 1.9

    june_peak_ghi = max(row["ghi"] for row in june)
    october_peak_ghi = max(row["ghi"] for row in october)
    assert june_peak_ghi > october_peak_ghi * 3.0


@pytest.mark.asyncio
async def test_december_is_the_thinnest_day():
    december = await run_day(build_site("2025-12-21"))

    assert daylight_hours(december) == pytest.approx(5.75, abs=0.2)
    assert max(row["ghi"] for row in december) == pytest.approx(87.0, rel=0.10)


@pytest.mark.asyncio
async def test_nothing_is_produced_at_night():
    history = await run_day(build_site("2025-10-31"))

    dark = [row for row in history if row["ghi"] == 0.0]
    assert dark

    for row in dark:
        assert row["dc_kw"] == 0.0


@pytest.mark.asyncio
async def test_daily_energy_matches_the_integrated_ac_power():
    history = await run_day(build_site("2025-10-31"))

    integrated = sum(row["ac_kw"] for row in history) * STEP_S / 3600.0

    assert history[-1]["today_kwh"] == pytest.approx(integrated, rel=1e-9)


@pytest.mark.asyncio
async def test_today_energy_resets_on_the_next_utc_day():
    site = build_site("2025-10-31")
    first_day = await run_day(site)
    second_day = await run_day(site)

    assert first_day[-1]["today_kwh"] > 0.0
    assert second_day[0]["today_kwh"] < first_day[-1]["today_kwh"]


# ------------------------------------------------------------
# Clipping
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_oversized_array_clips_at_the_ac_rating_in_summer():
    site = build_site("2025-06-21", ac_rating_kw=20.0, dc_capacity_kw=36.0)
    history = await run_day(site)

    peak_ac = max(row["ac_kw"] for row in history)
    assert peak_ac == pytest.approx(20.0, rel=1e-6)

    clipped = [row for row in history if row["ac_kw"] >= 20.0 - 1e-6]
    assert len(clipped) * STEP_S / 3600.0 > 2.0
    assert all(row["dc_kw"] > row["ac_kw"] for row in clipped)


@pytest.mark.asyncio
async def test_unity_dc_ac_ratio_never_clips():
    """The pre-0.2 default sizing: DC/AC 1.0, so the rating is unreachable."""
    site = build_site("2025-06-21", ac_rating_kw=30.0, dc_capacity_kw=None,
                      dc_ac_ratio=1.0, tilt_deg=None, azimuth_deg=None)
    history = await run_day(site)

    assert max(row["ac_kw"] for row in history) < 30.0


# ------------------------------------------------------------
# Clouds
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_clouds_reduce_output_and_are_reproducible():
    clear = await run_day(build_site("2025-06-21"))
    cloudy = await run_day(
        build_site("2025-06-21", cloud={"mean_kt": 0.48, "seed": 59})
    )
    cloudy_again = await run_day(
        build_site("2025-06-21", cloud={"mean_kt": 0.48, "seed": 59})
    )

    assert cloudy[-1]["today_kwh"] < clear[-1]["today_kwh"]
    assert cloudy[-1]["today_kwh"] == pytest.approx(cloudy_again[-1]["today_kwh"])
    assert [row["ghi"] for row in cloudy] == [row["ghi"] for row in cloudy_again]


@pytest.mark.asyncio
async def test_runs_without_clouds_are_identical():
    first = await run_day(build_site("2025-10-31"))
    second = await run_day(build_site("2025-10-31"))

    assert [row["ac_kw"] for row in first] == [row["ac_kw"] for row in second]


# ------------------------------------------------------------
# Backward compatibility
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_legacy_config_still_gets_the_fixed_shape_day():
    """No location and no sim_start_utc: the original 12-hour, 1000 W/m² day."""
    site = SiteController({
        "site_name": "legacy-pv",
        "step": STEP_S,
        "real_time": False,
        "external_models": {
            "irradiance": {"peak": 1000.0, "sunrise": 6.0, "sunset": 18.0},
        },
        "assets": [{"type": "inverter", "rated_kw": 30.0}],
    })
    site.build()

    history = await run_day(site)

    assert daylight_hours(history) == pytest.approx(12.0, abs=0.1)
    assert max(row["ghi"] for row in history) == pytest.approx(1000.0, rel=1e-6)


@pytest.mark.asyncio
async def test_a_location_without_a_date_is_rejected_at_build():
    site = SiteController({
        "site_name": "no-date",
        "step": STEP_S,
        "real_time": False,
        "external_models": {"irradiance": dict(STOCKHOLM_IRRADIANCE)},
        "assets": [{"type": "inverter", "rated_kw": 30.0}],
    })

    with pytest.raises(ValueError, match="sim_start_utc"):
        site.build()
