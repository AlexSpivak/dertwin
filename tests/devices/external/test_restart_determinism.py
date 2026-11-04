"""
The weather of a simulated day is a function of (seed, date).

Found in a long-lived deployment with frequent restarts. The daily cloud
regime used to be drawn once at engine start and stepped from there, so every
boot replayed the same first day: with one particular seed the daily clear-sky
index came out at the same value after every restart, and a site restarted
twice a day never saw a clear one. "Synthetic weather" had become "post-boot
overcast".

What the model owes a deployment is therefore not just reproducibility for a
given run, but reproducibility *of a date*: the same day must come out the
same whether the process has been up for a month, started an hour ago, or
started a hundred days earlier in simulated time. These tests are that
contract, for the cloud regime and for the load drift.

The load's fast noise and the cloud's intraday perturbation are deliberately
left free-running, and the tests below say so rather than pretending
otherwise.
"""

import statistics
from datetime import datetime, timedelta, timezone

import pytest

from dertwin.controllers.site_controller import SiteController
from dertwin.devices.external.clouds import AR1CloudModel
from dertwin.devices.external.load import SiteLoadModel


# The seed from the deployment that turned this up.
SEED = 50

STEP_S = 60.0
A_DATE = datetime(2026, 6, 10, tzinfo=timezone.utc)


def run_days(model, first_day, last_day, step_s=3600.0, hours=24):
    """
    Walk a cloud model from `first_day` to `last_day` the way a run does, and
    return the daily regime of each day.
    """

    regimes = {}
    day = first_day

    while day <= last_day:
        for step in range(hours):
            model.advance(day + timedelta(seconds=step_s * step), step_s)
        regimes[day.date()] = model.daily_clear_sky_index
        day += timedelta(days=1)

    return regimes


def lag1_autocorrelation(values):
    mean = statistics.fmean(values)
    numerator = sum(
        (values[i] - mean) * (values[i + 1] - mean) for i in range(len(values) - 1)
    )
    denominator = sum((value - mean) ** 2 for value in values)

    return numerator / denominator


# ------------------------------------------------------------
# Clouds: a restart does not change today's weather
# ------------------------------------------------------------

def test_a_restart_mid_day_keeps_the_same_daily_index():
    morning = AR1CloudModel(seed=SEED)
    run_days(morning, A_DATE, A_DATE, step_s=STEP_S, hours=8 * 60)

    # A new process, booted at 13:00 the same day, with the same seed.
    rebooted = AR1CloudModel(seed=SEED)
    rebooted.advance(A_DATE + timedelta(hours=13), STEP_S)

    assert rebooted.daily_clear_sky_index == morning.daily_clear_sky_index


@pytest.mark.parametrize("boot_hour", [0, 1, 7, 12, 18, 23])
def test_the_boot_hour_does_not_choose_the_weather(boot_hour):
    reference = AR1CloudModel(seed=SEED)
    reference.advance(A_DATE, STEP_S)

    booted = AR1CloudModel(seed=SEED)
    booted.advance(A_DATE + timedelta(hours=boot_hour), STEP_S)

    assert booted.daily_clear_sky_index == reference.daily_clear_sky_index


def test_repeated_restarts_do_not_all_report_the_same_day():
    """
    The actual symptom: every boot replaying day one. Now a boot reports the
    day it booted on, so a week of first-days is a week of different skies.
    """
    first_days = []

    for day in range(7):
        booted = AR1CloudModel(seed=SEED)
        booted.advance(A_DATE + timedelta(days=day), STEP_S)
        first_days.append(booted.daily_clear_sky_index)

    assert len(set(first_days)) == 7


def test_different_dates_get_different_weather():
    regimes = run_days(AR1CloudModel(seed=SEED), A_DATE, A_DATE + timedelta(days=99))

    assert len(set(regimes.values())) == 100


def test_the_starting_date_does_not_change_a_later_day():
    """Start on day N, and on day N-100: day N comes out the same."""
    target = A_DATE + timedelta(days=100)

    late = run_days(AR1CloudModel(seed=SEED), target, target)
    early = run_days(AR1CloudModel(seed=SEED), A_DATE, target)

    assert early[target.date()] == late[target.date()]


def test_a_year_of_days_is_the_same_from_either_starting_date():
    first = A_DATE + timedelta(days=200)
    last = first + timedelta(days=364)

    late = run_days(AR1CloudModel(seed=SEED), first, last)
    early = run_days(AR1CloudModel(seed=SEED), A_DATE, last)

    assert all(early[day] == regime for day, regime in late.items())


def test_a_different_seed_still_gives_a_different_sky():
    assert run_days(
        AR1CloudModel(seed=SEED), A_DATE, A_DATE + timedelta(days=29)
    ) != run_days(
        AR1CloudModel(seed=SEED + 1), A_DATE, A_DATE + timedelta(days=29)
    )


def test_an_unseeded_model_is_random_per_run():
    """Unseeded behaviour is unchanged: a fresh process gets a fresh sky."""
    first = AR1CloudModel()
    second = AR1CloudModel()

    first.advance(A_DATE, STEP_S)
    second.advance(A_DATE, STEP_S)

    assert first.daily_clear_sky_index != second.daily_clear_sky_index


def test_an_unseeded_model_is_still_consistent_within_its_own_run():
    model = AR1CloudModel()

    model.advance(A_DATE, STEP_S)
    morning = model.daily_clear_sky_index
    model.advance(A_DATE + timedelta(hours=18), STEP_S)

    assert model.daily_clear_sky_index == morning


def test_a_jump_of_ten_thousand_days_needs_no_catch_up():
    """
    The old model stepped its chain once per skipped day and had to cap the
    catch-up at ten thousand. A keyed day is one lookup, so the cap is gone
    and a far-off date is exact rather than resynced.
    """
    far = A_DATE + timedelta(days=10_000)

    jumped = AR1CloudModel(seed=SEED)
    jumped.advance(A_DATE, STEP_S)
    jumped.advance(far, STEP_S)

    fresh = AR1CloudModel(seed=SEED)
    fresh.advance(far, STEP_S)

    assert jumped.daily_clear_sky_index == fresh.daily_clear_sky_index


def test_the_cost_of_a_day_is_set_by_phi_alone():
    assert AR1CloudModel(seed=SEED, phi=0.65).day_regime_window < AR1CloudModel(
        seed=SEED, phi=0.9
    ).day_regime_window


# ------------------------------------------------------------
# Clouds: the keyed chain is still an AR(1)
# ------------------------------------------------------------

def test_five_years_of_keyed_days_keep_the_configured_statistics():
    """
    Tolerances, for 1826 consecutive days at phi=0.65:

    - the lag-1 estimator has a standard error of about
      sqrt((1 - phi^2) / n) = 0.019, so 0.05 is well over three of them;
    - the daily index has a spread of about 0.17 and the effective sample
      size is n*(1 - phi)/(1 + phi) = 388, giving a standard error of 0.009
      on the mean. The mean itself sits a little below mean_kt — the logit
      mapping makes the *median* exact, not the mean — so 0.03 covers both
      that offset and the sampling error.
    """
    regimes = list(
        run_days(
            AR1CloudModel(mean_kt=0.62, phi=0.65, seed=SEED),
            A_DATE,
            A_DATE + timedelta(days=1825),
            step_s=3600.0,
            hours=4,
        ).values()
    )

    assert len(regimes) == 1826
    assert lag1_autocorrelation(regimes) == pytest.approx(0.65, abs=0.05)
    assert statistics.fmean(regimes) == pytest.approx(0.62, abs=0.03)
    assert statistics.median(regimes) == pytest.approx(0.62, abs=0.04)


@pytest.mark.parametrize("phi", [0.3, 0.7, 0.9])
def test_persistence_survives_the_keying(phi):
    """
    The thing a naive fix breaks. An independent draw per date would pass
    every determinism test above and report phi = 0 here.
    """
    regimes = list(
        run_days(
            AR1CloudModel(phi=phi, seed=SEED),
            A_DATE,
            A_DATE + timedelta(days=3999),
            step_s=3600.0,
            hours=2,
        ).values()
    )

    assert lag1_autocorrelation(regimes) == pytest.approx(phi, abs=0.04)


def test_clear_days_are_not_rare_after_a_boot():
    """
    The deployment's complaint, as a statistic: across a year of boots, the
    sky on the first day is clear about as often as it is on any other day.
    """
    first_days = []

    for day in range(365):
        booted = AR1CloudModel(mean_kt=0.62, seed=SEED)
        booted.advance(A_DATE + timedelta(days=day), STEP_S)
        first_days.append(booted.daily_clear_sky_index)

    clear = [value for value in first_days if value > 0.8]

    assert len(clear) > 365 * 0.15
    assert statistics.fmean(first_days) == pytest.approx(0.62, abs=0.05)


# ------------------------------------------------------------
# Clouds: the intraday perturbation is free-running, on purpose
# ------------------------------------------------------------

def test_the_intraday_perturbation_does_not_disturb_the_daily_index():
    """
    The two processes draw from separate streams, which is why a restart
    mid-day — with however many intraday steps behind it — cannot move the
    day's regime.
    """
    busy = AR1CloudModel(seed=SEED, intraday_sigma=0.3)
    for minute in range(600):
        busy.advance(A_DATE + timedelta(minutes=minute), 60.0)

    still = AR1CloudModel(seed=SEED, intraday_sigma=0.0)
    still.advance(A_DATE, 60.0)

    assert busy.daily_clear_sky_index == still.daily_clear_sky_index


def test_the_instantaneous_index_is_the_regime_plus_a_fresh_wobble():
    """
    A restart does redraw the intraday term, so the instant is not identical
    across a restart — only the day is. That is the documented trade.
    """
    settled = AR1CloudModel(seed=SEED)
    for minute in range(600):
        settled.advance(A_DATE + timedelta(minutes=minute), 60.0)

    rebooted = AR1CloudModel(seed=SEED)
    rebooted.advance(A_DATE + timedelta(minutes=599), 60.0)

    assert rebooted.daily_clear_sky_index == settled.daily_clear_sky_index
    assert rebooted.clear_sky_index() != settled.clear_sky_index()
    assert abs(
        rebooted.clear_sky_index() - settled.clear_sky_index()
    ) < 6.0 * 0.07


# ------------------------------------------------------------
# Load: the same problem, the same fix
# ------------------------------------------------------------

def drift_at(instant, seed=7, **kwargs):
    model = SiteLoadModel(
        base_kw=100.0, drift_sigma=0.12, drift_tau_s=10800.0, seed=seed, **kwargs
    )
    model.advance(0.0, STEP_S, utc_time=instant)

    return model.drift_factor()


def test_the_load_drift_at_an_instant_does_not_depend_on_the_boot():
    instant = A_DATE + timedelta(hours=13)

    walked = SiteLoadModel(
        base_kw=100.0, drift_sigma=0.12, drift_tau_s=10800.0, seed=7
    )
    for step in range(13 * 60):
        walked.advance(
            step * STEP_S, STEP_S, utc_time=A_DATE + timedelta(seconds=step * STEP_S)
        )

    assert drift_at(instant) == pytest.approx(walked.drift_factor())


def test_the_load_drift_is_not_the_same_on_every_boot():
    """
    The old model started the drift at a stationary draw from the seed, so
    every boot began at the same factor and replayed the same afternoon.
    """
    boots = [drift_at(A_DATE + timedelta(days=day)) for day in range(30)]

    assert len(set(boots)) == 30


def test_the_load_drift_moves_on_the_scale_of_hours():
    minutes_apart = drift_at(A_DATE) - drift_at(A_DATE + timedelta(minutes=5))
    days_apart = drift_at(A_DATE) - drift_at(A_DATE + timedelta(days=3))

    assert abs(minutes_apart) < abs(days_apart)


def test_the_load_drift_keeps_its_spread_over_a_long_run():
    model = SiteLoadModel(
        base_kw=100.0,
        drift_sigma=0.12,
        drift_tau_s=10800.0,
        drift_min_factor=0.0,
        drift_max_factor=10.0,
        seed=7,
    )

    factors = []
    for step in range(0, 4000):
        instant = A_DATE + timedelta(seconds=step * 900.0)
        model.advance(step * 900.0, 900.0, utc_time=instant)
        factors.append(model.drift_factor())

    assert statistics.fmean(factors) == pytest.approx(1.0, abs=0.03)
    assert statistics.stdev(factors) == pytest.approx(0.12, rel=0.25)


def test_the_load_drift_without_a_calendar_is_keyed_on_the_run_clock():
    """
    Without `sim_start_utc` the clock itself restarts at zero, so there is no
    absolute instant to key on and a restart does replay the drift. Keyed on
    the run clock is the best the model can do, and this is what it does.
    """
    def first_factors():
        model = SiteLoadModel(
            base_kw=100.0, drift_sigma=0.12, drift_tau_s=10800.0, seed=7
        )
        factors = []
        for step in range(10):
            model.advance(step * STEP_S, STEP_S)
            factors.append(model.drift_factor())
        return factors

    assert first_factors() == first_factors()


def test_the_load_noise_stays_free_running():
    """
    Fast, zero-mean, half-a-minute memory: a restart redraws it and nothing
    downstream can tell. Keying it would buy nothing and cost the exact
    timestep independence of the Ornstein-Uhlenbeck step.
    """
    model = SiteLoadModel(base_kw=100.0, noise_sigma=0.05, seed=7)

    values = []
    for step in range(200):
        model.advance(step * STEP_S, STEP_S, utc_time=A_DATE)
        values.append(model.noise_factor())

    assert len(set(values)) == len(values)
    assert statistics.fmean(values) == pytest.approx(1.0, abs=0.02)


# ------------------------------------------------------------
# End to end, through the engine
# ------------------------------------------------------------

def build_site(sim_start_utc, seed=SEED):
    site = SiteController({
        "site_name": "restart",
        "step": STEP_S,
        "real_time": False,
        "sim_start_utc": sim_start_utc.isoformat(),
        "external_models": {
            "irradiance": {
                "latitude": 52.0,
                "longitude": 5.0,
                "cloud": {"model": "ar1", "seed": seed},
            },
            "load": {
                "base_kw": 100.0,
                "drift": {"sigma": 0.12, "tau_s": 10800.0},
                "seed": seed,
            },
        },
        "assets": [{"type": "energy_meter"}],
    })
    site.build()

    return site


async def boot_and_run(sim_start_utc, ticks, seed=SEED):
    site = build_site(sim_start_utc, seed=seed)

    for _ in range(ticks):
        await site.engine.step_once()

    return (
        site.external_models.irradiance_model.cloud_model.daily_clear_sky_index,
        site.external_models.load_model.drift_factor(),
    )


@pytest.mark.asyncio
async def test_a_mid_day_restart_of_the_engine_keeps_the_day_and_the_drift():
    """
    A new engine, same seed, same date, booted eight hours in: the day's
    weather and the drift at that instant are what the engine it replaced
    would have been reporting.
    """
    offset_h = 8
    # The extra tick puts the long-lived run's last step on exactly the
    # instant the replacement boots at, so these are equalities and not
    # "close enough".
    ticks = int(offset_h * 3600 / STEP_S) + 1

    before_restart = await boot_and_run(A_DATE, ticks)
    after_restart = await boot_and_run(A_DATE + timedelta(hours=offset_h), 1)

    assert after_restart[0] == before_restart[0]
    assert after_restart[1] == before_restart[1]


@pytest.mark.asyncio
async def test_two_boots_on_different_days_see_different_weather():
    first = await boot_and_run(A_DATE, 1)
    second = await boot_and_run(A_DATE + timedelta(days=1), 1)

    assert first[0] != second[0]
    assert first[1] != second[1]


@pytest.mark.asyncio
async def test_a_boot_on_a_later_date_agrees_with_a_run_that_got_there():
    """
    The long-lived process and the process that has just started must agree
    about the day they are both in.
    """
    days = 3
    # One tick past the midnight, so the long-lived run is on the same day as
    # the one that just booted rather than on the last tick of the day before.
    ticks = int(days * 24 * 3600 / STEP_S) + 1

    long_lived = await boot_and_run(A_DATE, ticks)
    just_started = await boot_and_run(A_DATE + timedelta(days=days), 1)

    assert just_started[0] == long_lived[0]
