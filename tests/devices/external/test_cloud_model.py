"""
Cloud cover: persistent daily regimes plus intraday variability.

The statistics are the contract. An AR(1) process in logit space has an
analytic stationary distribution, so the tests assert the median, the spread
and the lag-1 autocorrelation rather than eyeballing a plot.
"""

import math
import statistics
from datetime import datetime, timedelta, timezone

import pytest

from dertwin.devices.external.clouds import (
    AR1CloudModel,
    ClearSkyCloudModel,
    SeriesCloudModel,
    build_cloud_model,
)
from dertwin.devices.external.timeseries import TimeSeries


START = datetime(2025, 1, 1, 12, tzinfo=timezone.utc)


def daily_regimes(n_days: int, **kwargs) -> list[float]:
    model = AR1CloudModel(**kwargs)
    values = []

    for day in range(n_days):
        model.advance(START + timedelta(days=day), 60.0)
        values.append(model.daily_clear_sky_index)

    return values


def lag1_autocorrelation(values: list[float]) -> float:
    mean = statistics.fmean(values)
    numerator = sum(
        (values[i] - mean) * (values[i + 1] - mean) for i in range(len(values) - 1)
    )
    denominator = sum((value - mean) ** 2 for value in values)

    return numerator / denominator


# ------------------------------------------------------------
# Determinism
# ------------------------------------------------------------

def test_same_seed_gives_the_same_sequence():
    assert daily_regimes(200, seed=59) == daily_regimes(200, seed=59)


def test_different_seeds_give_different_sequences():
    assert daily_regimes(200, seed=59) != daily_regimes(200, seed=60)


def test_unseeded_models_still_run():
    values = daily_regimes(50)
    assert len(values) == 50


# ------------------------------------------------------------
# Bounds
# ------------------------------------------------------------

def test_index_stays_inside_its_bounds():
    model = AR1CloudModel(kt_min=0.08, intraday_sigma=0.3, seed=59)

    for minute in range(20_000):
        model.advance(START + timedelta(minutes=minute), 60.0)
        kt = model.clear_sky_index()
        assert 0.08 - 1e-12 <= kt <= 1.0 + 1e-12


def test_daily_regime_stays_inside_its_bounds():
    for value in daily_regimes(5_000, kt_min=0.08, seed=59):
        assert 0.08 <= value <= 1.0


# ------------------------------------------------------------
# Daily statistics
# ------------------------------------------------------------

def test_median_daily_index_is_the_configured_mean():
    """Exact by symmetry: the innovation is symmetric in logit space."""
    values = daily_regimes(20_000, mean_kt=0.62, seed=7)

    assert statistics.median(values) == pytest.approx(0.62, abs=0.02)


def test_mean_daily_index_is_close_to_the_configured_mean():
    values = daily_regimes(20_000, mean_kt=0.62, seed=7)

    assert statistics.fmean(values) == pytest.approx(0.62, abs=0.03)


def test_default_spread_covers_real_weather():
    """
    The default must produce both clear and genuinely overcast days. A narrow
    sigma_day gives a sky that is permanently hazy and never either.
    """
    values = sorted(daily_regimes(20_000, seed=7))

    assert statistics.stdev(values) > 0.12
    assert values[int(0.05 * len(values))] < 0.35
    assert values[int(0.95 * len(values))] > 0.80
    assert any(value < 0.3 for value in values)


@pytest.mark.parametrize("phi", [0.3, 0.65, 0.9])
def test_day_to_day_persistence_matches_phi(phi):
    values = daily_regimes(20_000, phi=phi, seed=7)

    assert lag1_autocorrelation(values) == pytest.approx(phi, abs=0.05)


def test_zero_phi_gives_independent_days():
    values = daily_regimes(20_000, phi=0.0, seed=7)

    assert abs(lag1_autocorrelation(values)) < 0.05


def test_regime_changes_only_at_the_utc_day_boundary():
    model = AR1CloudModel(seed=59)
    model.advance(datetime(2025, 3, 1, 0, 0, tzinfo=timezone.utc), 60.0)
    first = model.daily_clear_sky_index

    model.advance(datetime(2025, 3, 1, 23, 59, tzinfo=timezone.utc), 60.0)
    assert model.daily_clear_sky_index == first

    model.advance(datetime(2025, 3, 2, 0, 0, tzinfo=timezone.utc), 60.0)
    assert model.daily_clear_sky_index != first


def test_skipping_days_advances_the_regime_once_per_day():
    # intraday_sigma=0 so the only random draws are the daily ones
    stepwise = AR1CloudModel(seed=59, intraday_sigma=0.0)
    jumping = AR1CloudModel(seed=59, intraday_sigma=0.0)

    for day in range(10):
        stepwise.advance(START + timedelta(days=day), 60.0)

    jumping.advance(START, 60.0)
    jumping.advance(START + timedelta(days=9), 60.0)

    assert jumping.daily_clear_sky_index == pytest.approx(
        stepwise.daily_clear_sky_index
    )


def test_clock_moving_backwards_resyncs_without_redrawing():
    model = AR1CloudModel(seed=59)
    model.advance(START + timedelta(days=5), 60.0)
    regime = model.daily_clear_sky_index

    model.advance(START, 60.0)

    assert model.daily_clear_sky_index == regime


# ------------------------------------------------------------
# Intraday statistics
# ------------------------------------------------------------

def test_intraday_variation_is_autocorrelated():
    tau = 900.0
    dt = 60.0
    model = AR1CloudModel(
        intraday_sigma=0.07, intraday_tau_s=tau, sigma_day=0.0, seed=3
    )

    values = []
    moment = datetime(2025, 6, 1, tzinfo=timezone.utc)
    for step in range(1_440):
        model.advance(moment + timedelta(seconds=dt * step), dt)
        values.append(model.clear_sky_index())

    assert lag1_autocorrelation(values) == pytest.approx(
        math.exp(-dt / tau), abs=0.05
    )
    assert statistics.stdev(values) == pytest.approx(0.07, rel=0.25)


def test_zero_intraday_sigma_holds_the_daily_regime():
    model = AR1CloudModel(intraday_sigma=0.0, seed=3)

    moment = datetime(2025, 6, 1, tzinfo=timezone.utc)
    for step in range(100):
        model.advance(moment + timedelta(minutes=step), 60.0)
        assert model.clear_sky_index() == pytest.approx(
            model.daily_clear_sky_index
        )


def test_zero_dt_does_not_advance_the_intraday_process():
    model = AR1CloudModel(seed=3)
    moment = datetime(2025, 6, 1, tzinfo=timezone.utc)

    model.advance(moment, 60.0)
    before = model.clear_sky_index()
    model.advance(moment, 0.0)

    assert model.clear_sky_index() == before


# ------------------------------------------------------------
# Validation
# ------------------------------------------------------------

@pytest.mark.parametrize("kwargs,match", [
    (dict(kt_min=1.0), "kt_min"),
    (dict(kt_min=-0.1), "kt_min"),
    (dict(mean_kt=1.0), "mean_kt"),
    (dict(mean_kt=0.05, kt_min=0.08), "mean_kt"),
    (dict(phi=1.0), "phi"),
    (dict(phi=-0.1), "phi"),
    (dict(sigma_day=-1.0), "sigma_day"),
    (dict(intraday_sigma=-1.0), "intraday_sigma"),
    (dict(intraday_tau_s=0.0), "intraday_tau_s"),
])
def test_invalid_configuration_raises(kwargs, match):
    with pytest.raises(ValueError, match=match):
        AR1CloudModel(**kwargs)


# ------------------------------------------------------------
# The other cloud models
# ------------------------------------------------------------

def test_clear_sky_cloud_model_is_always_one():
    model = ClearSkyCloudModel()
    model.advance(START, 60.0)
    assert model.clear_sky_index() == 1.0


def test_build_cloud_model_defaults_to_ar1():
    """Clouds are on by default — a permanently cloudless sky is no more
    realistic than a fixed 12-hour day."""
    assert isinstance(build_cloud_model(None), AR1CloudModel)
    assert isinstance(build_cloud_model({}), AR1CloudModel)
    assert isinstance(build_cloud_model({"enabled": False}), ClearSkyCloudModel)
    assert isinstance(build_cloud_model({"model": "none"}), ClearSkyCloudModel)


def test_build_cloud_model_rejects_unknown_models():
    with pytest.raises(ValueError, match="Unknown cloud.model"):
        build_cloud_model({"model": "crystal-ball"})


def test_series_cloud_model_replays_recorded_cover():
    series = TimeSeries([
        (datetime(2025, 6, 1, 0, tzinfo=timezone.utc), 1.0),
        (datetime(2025, 6, 1, 12, tzinfo=timezone.utc), 0.2),
    ])
    model = SeriesCloudModel(series)

    model.advance(datetime(2025, 6, 1, 0, tzinfo=timezone.utc), 60.0)
    assert model.clear_sky_index() == pytest.approx(1.0)

    model.advance(datetime(2025, 6, 1, 6, tzinfo=timezone.utc), 60.0)
    assert model.clear_sky_index() == pytest.approx(0.6)

    # Outside the series: fall back to clear sky
    model.advance(datetime(2025, 6, 2, tzinfo=timezone.utc), 60.0)
    assert model.clear_sky_index() == pytest.approx(1.0)
