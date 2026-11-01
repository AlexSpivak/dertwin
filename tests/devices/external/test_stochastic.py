"""
Ornstein-Uhlenbeck process.

The exact discrete-time solution is used rather than an Euler step, so the
statistics must not depend on the timestep. That is the whole point: this
simulator's step is configurable, and an Euler discretisation would quietly
change the noise amplitude when you switch from 0.1 s to 60 s steps.
"""

import math
import random
import statistics

import pytest

from dertwin.devices.external.stochastic import OrnsteinUhlenbeck


SIGMA = 0.12
TAU_S = 10800.0


def lag1_autocorrelation(values):
    mean = statistics.fmean(values)
    numerator = sum(
        (values[i] - mean) * (values[i + 1] - mean) for i in range(len(values) - 1)
    )
    denominator = sum((value - mean) ** 2 for value in values)

    return numerator / denominator


def run(process, steps, dt):
    return [process.advance(dt) for _ in range(steps)]


# ------------------------------------------------------------
# Stationary distribution
# ------------------------------------------------------------

@pytest.mark.parametrize("dt", [0.1, 1.0, 60.0, 600.0, 3600.0, 100_000.0])
def test_stationary_spread_does_not_depend_on_the_timestep(dt):
    """
    Fresh instances advanced one step each: this isolates the stationarity
    property from the autocorrelation that makes a single long run a poor
    estimator.
    """
    values = []

    for seed in range(20_000):
        process = OrnsteinUhlenbeck(sigma=SIGMA, tau_s=TAU_S, seed=seed)
        process.advance(dt)
        values.append(process.value)

    assert statistics.stdev(values) == pytest.approx(SIGMA, rel=0.03)
    assert statistics.fmean(values) == pytest.approx(0.0, abs=0.01)


def test_starts_in_its_stationary_distribution():
    """No warm-up transient: the first value is already correctly spread."""
    values = [
        OrnsteinUhlenbeck(sigma=SIGMA, tau_s=TAU_S, seed=seed).value
        for seed in range(20_000)
    ]

    assert statistics.stdev(values) == pytest.approx(SIGMA, rel=0.03)
    assert statistics.fmean(values) == pytest.approx(0.0, abs=0.01)


def test_can_start_at_zero_instead():
    process = OrnsteinUhlenbeck(
        sigma=SIGMA, tau_s=TAU_S, seed=7, start_at_stationary=False
    )

    assert process.value == 0.0


# ------------------------------------------------------------
# Autocorrelation
# ------------------------------------------------------------

@pytest.mark.parametrize("tau_s", [3600.0, 10800.0])
def test_autocorrelation_matches_the_analytic_value(tau_s):
    dt = tau_s / 10.0
    process = OrnsteinUhlenbeck(sigma=SIGMA, tau_s=tau_s, seed=7)

    values = run(process, 100_000, dt)

    assert lag1_autocorrelation(values) == pytest.approx(
        math.exp(-dt / tau_s), abs=0.01
    )


def test_a_longer_tau_is_more_persistent():
    fast = run(OrnsteinUhlenbeck(sigma=SIGMA, tau_s=60.0, seed=7), 20_000, 60.0)
    slow = run(OrnsteinUhlenbeck(sigma=SIGMA, tau_s=6000.0, seed=7), 20_000, 60.0)

    assert lag1_autocorrelation(slow) > lag1_autocorrelation(fast)


# ------------------------------------------------------------
# Determinism
# ------------------------------------------------------------

def test_same_seed_gives_the_same_path():
    first = run(OrnsteinUhlenbeck(sigma=SIGMA, tau_s=TAU_S, seed=7), 500, 60.0)
    second = run(OrnsteinUhlenbeck(sigma=SIGMA, tau_s=TAU_S, seed=7), 500, 60.0)

    assert first == second


def test_different_seeds_give_different_paths():
    first = run(OrnsteinUhlenbeck(sigma=SIGMA, tau_s=TAU_S, seed=7), 500, 60.0)
    second = run(OrnsteinUhlenbeck(sigma=SIGMA, tau_s=TAU_S, seed=8), 500, 60.0)

    assert first != second


def test_a_shared_rng_lets_one_seed_drive_several_processes():
    rng = random.Random(7)
    drift = OrnsteinUhlenbeck(sigma=SIGMA, tau_s=TAU_S, rng=rng)
    noise = OrnsteinUhlenbeck(sigma=0.02, tau_s=30.0, rng=rng)

    rng_again = random.Random(7)
    drift_again = OrnsteinUhlenbeck(sigma=SIGMA, tau_s=TAU_S, rng=rng_again)
    noise_again = OrnsteinUhlenbeck(sigma=0.02, tau_s=30.0, rng=rng_again)

    for _ in range(100):
        drift.advance(60.0)
        noise.advance(60.0)
        drift_again.advance(60.0)
        noise_again.advance(60.0)

    assert drift.value == drift_again.value
    assert noise.value == noise_again.value


# ------------------------------------------------------------
# Degenerate cases
# ------------------------------------------------------------

def test_zero_sigma_is_identically_zero():
    process = OrnsteinUhlenbeck(sigma=0.0, tau_s=TAU_S, seed=7)

    assert process.value == 0.0
    assert run(process, 100, 60.0) == [0.0] * 100


def test_zero_sigma_draws_nothing_from_the_random_stream():
    """
    A disabled process must not shift the shared stream, or enabling it would
    change an unrelated process's path.
    """
    rng = random.Random(7)
    disabled = OrnsteinUhlenbeck(sigma=0.0, tau_s=TAU_S, rng=rng)
    active = OrnsteinUhlenbeck(sigma=SIGMA, tau_s=TAU_S, rng=rng)

    for _ in range(50):
        disabled.advance(60.0)
        active.advance(60.0)

    rng_again = random.Random(7)
    alone = OrnsteinUhlenbeck(sigma=SIGMA, tau_s=TAU_S, rng=rng_again)
    for _ in range(50):
        alone.advance(60.0)

    assert active.value == alone.value


def test_zero_dt_does_not_advance():
    process = OrnsteinUhlenbeck(sigma=SIGMA, tau_s=TAU_S, seed=7)
    before = process.value

    process.advance(0.0)

    assert process.value == before


# ------------------------------------------------------------
# Validation
# ------------------------------------------------------------

@pytest.mark.parametrize("kwargs,match", [
    (dict(sigma=-0.1, tau_s=TAU_S), "sigma"),
    (dict(sigma=SIGMA, tau_s=0.0), "tau_s"),
    (dict(sigma=SIGMA, tau_s=-1.0), "tau_s"),
])
def test_invalid_configuration_raises(kwargs, match):
    with pytest.raises(ValueError, match=match):
        OrnsteinUhlenbeck(**kwargs)
