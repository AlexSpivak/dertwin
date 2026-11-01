"""
Deterministic end-to-end run of the site load, driven by the real engine.

Also the regression guard for the time-origin bug: the load used to be read
through `base_load_supplier(t)` with `SitePowerModel`'s own accumulator, which
starts at zero and ignores `start_time_h` — so a load profile would have sat
out of phase with the irradiance and ambient temperature models, which use the
engine clock.
"""

import statistics

import pytest

from dertwin.controllers.site_controller import SiteController


SHAPE = [
    0.55, 0.52, 0.50, 0.50, 0.52, 0.60, 0.75, 0.95,
    1.05, 1.05, 1.00, 0.98, 0.95, 0.95, 0.98, 1.05,
    1.20, 1.35, 1.30, 1.15, 1.00, 0.85, 0.70, 0.60,
]

STEP_S = 60.0
TICKS_PER_DAY = int(24 * 3600 / STEP_S)


def build_site(load=None, start_time_h=0.0, assets=None, **external):
    external_models = {}
    if load is not None:
        external_models["load"] = load
    external_models.update(external)

    cfg = {
        "site_name": "load-sim",
        "step": STEP_S,
        "real_time": False,
        "start_time_h": start_time_h,
        "external_models": external_models,
        # Device-only: no ports, so no Modbus servers are needed.
        "assets": assets if assets is not None else [{"type": "energy_meter"}],
    }

    site = SiteController(cfg)
    site.build()
    return site


async def run_ticks(site, ticks):
    meter = site._devices_by_type["energy_meter"][0]
    power_model = site.external_models.power_model
    load_model = site.external_models.load_model

    history = []
    for _ in range(ticks):
        await site.engine.step_once()
        history.append({
            "sim_time": site.engine.sim_time,
            "load_kw": load_model.get_power_kw(),
            "grid_kw": power_model.grid_power_kw,
            "meter_kw": meter.get_telemetry().total_active_power,
        })

    return history


# ------------------------------------------------------------
# The load reaches the meter
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_is_the_only_thing_the_meter_sees():
    site = build_site({"base_kw": 40.0})

    history = await run_ticks(site, 60)

    for row in history:
        assert row["grid_kw"] == pytest.approx(40.0)
        assert row["meter_kw"] == pytest.approx(40.0)


@pytest.mark.asyncio
async def test_daily_profile_shows_up_at_the_meter():
    site = build_site({
        "base_kw": 40.0,
        "profile": {"model": "hourly", "hourly": SHAPE},
    })

    history = await run_ticks(site, TICKS_PER_DAY)

    powers = [row["meter_kw"] for row in history]

    # The normalised shape leaves the daily average at base_kw
    assert statistics.fmean(powers) == pytest.approx(40.0, rel=0.002)

    # ...and the evening peak is where the shape puts it
    peak_row = max(history, key=lambda row: row["meter_kw"])
    peak_hour = (peak_row["sim_time"] / 3600.0) % 24.0
    assert peak_hour == pytest.approx(17.5, abs=0.2)

    assert max(powers) > min(powers) * 2.0


@pytest.mark.asyncio
async def test_meter_import_energy_matches_the_integrated_load():
    site = build_site({
        "base_kw": 40.0,
        "profile": {"model": "hourly", "hourly": SHAPE},
        "drift": {"sigma": 0.1, "tau_s": 7200.0},
        "noise": {"sigma": 0.02, "tau_s": 30.0},
        "seed": 7,
    })

    history = await run_ticks(site, TICKS_PER_DAY)
    meter = site._devices_by_type["energy_meter"][0]

    expected_kwh = sum(row["grid_kw"] for row in history) * STEP_S / 3600.0

    assert meter.get_telemetry().total_import_energy == pytest.approx(
        expected_kwh, rel=1e-9
    )
    assert meter.get_telemetry().total_export_energy == pytest.approx(0.0)


# ------------------------------------------------------------
# Time origin: the regression guard
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_profile_respects_start_time_h():
    """
    With start_time_h=12 the first tick must land at midday on the profile.
    Read through the power model's own clock it would have landed at midnight,
    at the bottom of the curve instead of the middle.
    """
    site = build_site(
        {"base_kw": 100.0, "profile": {"model": "hourly", "hourly": SHAPE}},
        start_time_h=12.0,
    )

    history = await run_ticks(site, 1)
    profile = site.external_models.load_model.profile

    assert history[0]["load_kw"] == pytest.approx(
        100.0 * profile.factor_at(12.0), rel=1e-9
    )
    assert history[0]["load_kw"] != pytest.approx(
        100.0 * profile.factor_at(0.0), rel=1e-3
    )


@pytest.mark.asyncio
async def test_load_and_irradiance_share_one_clock():
    """
    Both models must see the same hour of day on the same tick. The load peaks
    at 17:30 and solar noon is 12:00, so with start_time_h=12 the irradiance
    model is at its peak while the load is still climbing.
    """
    site = build_site(
        {"base_kw": 100.0, "profile": {"model": "hourly", "hourly": SHAPE}},
        start_time_h=12.0,
        irradiance={"peak": 1000.0, "sunrise": 6.0, "sunset": 18.0},
    )

    await run_ticks(site, 1)

    irradiance = site.external_models.irradiance_model
    load_model = site.external_models.load_model
    profile = load_model.profile

    assert irradiance.get_irradiance() == pytest.approx(1000.0, rel=1e-6)
    assert load_model.get_power_kw() == pytest.approx(
        100.0 * profile.factor_at(12.0), rel=1e-9
    )

    # Step to the load's own peak and check the sun has moved past noon
    await run_ticks(site, int(5.5 * 3600 / STEP_S))

    assert load_model.get_power_kw() == pytest.approx(
        100.0 * profile.factor_at(17.5), rel=1e-6
    )
    assert 0.0 < irradiance.get_irradiance() < 1000.0


@pytest.mark.asyncio
async def test_weekday_factor_changes_at_simulated_midnight():
    site = build_site({
        "base_kw": 100.0,
        "weekday_factor": 1.0,
        "weekend_factor": 0.5,
        "start_weekday": "friday",
    })

    history = await run_ticks(site, 3 * TICKS_PER_DAY)

    friday = history[0]["meter_kw"]
    saturday = history[TICKS_PER_DAY + 10]["meter_kw"]
    sunday = history[2 * TICKS_PER_DAY + 10]["meter_kw"]

    assert friday == pytest.approx(100.0)
    assert saturday == pytest.approx(50.0)
    assert sunday == pytest.approx(50.0)


# ------------------------------------------------------------
# Determinism
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_seeded_runs_are_identical():
    def config():
        return {
            "base_kw": 40.0,
            "profile": {"model": "hourly", "hourly": SHAPE},
            "drift": {"sigma": 0.12, "tau_s": 10800.0},
            "noise": {"sigma": 0.02, "tau_s": 30.0},
            "seed": 7,
        }

    first = await run_ticks(build_site(config()), 500)
    second = await run_ticks(build_site(config()), 500)

    assert [row["meter_kw"] for row in first] == [
        row["meter_kw"] for row in second
    ]


@pytest.mark.asyncio
async def test_the_step_size_does_not_change_the_load_statistics():
    """
    The drift is an exact-solution Ornstein-Uhlenbeck process, so its spread
    must not depend on whether the site runs at 1 s or 60 s steps.
    """
    async def spread(step_s):
        site = build_site({
            "base_kw": 100.0,
            "drift": {"sigma": 0.12, "tau_s": 3600.0,
                      "min_factor": 0.0, "max_factor": 10.0},
            "seed": 7,
        })
        site.clock.step = step_s

        ticks = int(400 * 3600 / step_s)
        powers = [row["meter_kw"] for row in await run_ticks(site, ticks)]

        return statistics.stdev(powers)

    coarse = await spread(600.0)
    fine = await spread(60.0)

    assert coarse == pytest.approx(fine, rel=0.25)
    assert coarse == pytest.approx(12.0, rel=0.3)


# ------------------------------------------------------------
# Backward compatibility
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_legacy_base_load_w_still_drives_the_site():
    site = build_site(None, power={"base_load_w": 7000.0})

    history = await run_ticks(site, 10)

    for row in history:
        assert row["meter_kw"] == pytest.approx(7.0)


@pytest.mark.asyncio
async def test_no_external_models_config_keeps_the_five_kilowatt_default():
    site = SiteController({
        "site_name": "load-default",
        "step": STEP_S,
        "real_time": False,
        "assets": [{"type": "energy_meter"}],
    })
    site.build()

    await site.engine.step_once()

    meter = site._devices_by_type["energy_meter"][0]
    assert meter.get_telemetry().total_active_power == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_a_load_block_supersedes_base_load_w():
    site = build_site({"base_kw": 30.0}, power={"base_load_w": 7000.0})

    await site.engine.step_once()

    assert site.external_models.load_model.base_kw == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_a_load_block_without_base_kw_inherits_base_load_w():
    site = build_site(
        {"profile": {"model": "hourly", "hourly": SHAPE}},
        power={"base_load_w": 7000.0},
    )

    assert site.external_models.load_model.base_kw == pytest.approx(7.0)


# ------------------------------------------------------------
# Interaction with generation
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_pv_offsets_the_load_and_can_push_the_site_to_export():
    site = build_site(
        {"base_kw": 5.0},
        start_time_h=12.0,
        irradiance={"peak": 1000.0, "sunrise": 6.0, "sunset": 18.0},
        assets=[
            {"type": "inverter", "rated_kw": 30.0},
            {"type": "energy_meter"},
        ],
    )

    history = await run_ticks(site, 200)

    # PV ramps up, overtakes the 5 kW load, and the site starts exporting
    assert history[0]["grid_kw"] > 0.0
    assert history[-1]["grid_kw"] < 0.0

    meter = site._devices_by_type["energy_meter"][0]
    assert meter.get_telemetry().total_export_energy > 0.0
