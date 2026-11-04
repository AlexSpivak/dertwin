"""
Keyed stochastic processes: the value at a step is a function of (seed, step).

A free-running process has to be walked from wherever it started, so its
trajectory belongs to the process that started it. A keyed one can be asked
about any step directly, which is what lets a restarted simulator agree with
the one it replaced about what the weather was doing.

The thing that is easy to get wrong is keeping the correlation. An independent
draw per step is trivially restart-proof and useless: it sets phi to zero. So
the statistics are the contract here, as much as the determinism is.
"""

import math
import statistics

import pytest

from dertwin.devices.external.stochastic import (
    DEFAULT_KEYED_TOLERANCE,
    KeyedAR1,
    KeyedOrnsteinUhlenbeck,
    derive_seed,
    keyed_standard_normal,
)


SIGMA = 0.9
PHI = 0.65


def lag1_autocorrelation(values):
    mean = statistics.fmean(values)
    numerator = sum(
        (values[i] - mean) * (values[i + 1] - mean) for i in range(len(values) - 1)
    )
    denominator = sum((value - mean) ** 2 for value in values)

    return numerator / denominator


def walk(process, first, last):
    """Every step from `first` to `last`, in order, as a run would see them."""
    return [process.value_at(index) for index in range(first, last + 1)]


# ------------------------------------------------------------
# Determinism in (seed, index)
# ------------------------------------------------------------

def test_a_step_is_the_same_wherever_the_walk_started():
    from_scratch = KeyedAR1(PHI, SIGMA, seed=50).value_at(739_000)
    after_a_walk = walk(KeyedAR1(PHI, SIGMA, seed=50), 738_000, 739_000)[-1]

    assert from_scratch == after_a_walk


def test_steps_can_be_evaluated_in_any_order():
    forwards = KeyedAR1(PHI, SIGMA, seed=50)
    backwards = KeyedAR1(PHI, SIGMA, seed=50)

    indices = list(range(1_000, 1_050))
    ascending = [forwards.value_at(i) for i in indices]
    descending = [backwards.value_at(i) for i in reversed(indices)]

    assert ascending == list(reversed(descending))


def test_different_seeds_give_different_values():
    assert KeyedAR1(PHI, SIGMA, seed=50).value_at(1000) != KeyedAR1(
        PHI, SIGMA, seed=51
    ).value_at(1000)


def test_different_streams_give_different_values():
    assert KeyedAR1(PHI, SIGMA, seed=50, stream="one").value_at(1000) != KeyedAR1(
        PHI, SIGMA, seed=50, stream="two"
    ).value_at(1000)


def test_an_unseeded_process_is_random_per_instance():
    assert KeyedAR1(PHI, SIGMA).value_at(1000) != KeyedAR1(PHI, SIGMA).value_at(1000)


def test_an_unseeded_process_is_still_keyed_within_itself():
    """Random per run, but the same instance still agrees with itself."""
    process = KeyedAR1(PHI, SIGMA)

    assert process.value_at(1000) == process.value_at(1000)


def test_derive_seed_is_stable_and_stream_specific():
    assert derive_seed(7, "a") == derive_seed(7, "a")
    assert derive_seed(7, "a") != derive_seed(7, "b")
    assert derive_seed(7, "a") != derive_seed(8, "a")
    assert derive_seed(None, "a") != derive_seed(None, "a")


def test_keyed_normals_are_independent_across_indices():
    values = [keyed_standard_normal(50, "s", i) for i in range(20_000)]

    assert statistics.fmean(values) == pytest.approx(0.0, abs=0.03)
    assert statistics.stdev(values) == pytest.approx(1.0, abs=0.03)
    assert abs(lag1_autocorrelation(values)) < 0.03


# ------------------------------------------------------------
# Cost: set by phi, not by the index
# ------------------------------------------------------------

@pytest.mark.parametrize("phi", [0.3, 0.65, 0.9, 0.99])
def test_the_window_is_the_truncation_of_the_moving_average(phi):
    """
    The dropped tail has standard deviation sigma * phi**window, so the
    window is what puts the truncation error below the tolerance — and it
    depends on phi alone.
    """
    process = KeyedAR1(phi, SIGMA, seed=50)

    assert process.window == math.ceil(
        math.log(DEFAULT_KEYED_TOLERANCE) / math.log(phi)
    )
    assert phi ** process.window <= DEFAULT_KEYED_TOLERANCE


def test_the_window_does_not_depend_on_the_index():
    """
    The cost bound: there is no epoch, so a step a million days out is one
    evaluation of the same length as a step at the origin.
    """
    process = KeyedAR1(PHI, SIGMA, seed=50)
    near = process.value_at(0)
    innovations_for_one_step = len(process._innovations)

    far = process.value_at(1_000_000_000)

    assert len(process._innovations) <= 2 * innovations_for_one_step
    assert near != far


def test_the_innovation_cache_stays_bounded_over_a_long_walk():
    process = KeyedAR1(PHI, SIGMA, seed=50)

    walk(process, 0, 20_000)

    assert len(process._innovations) <= 4 * process.window + 1


def test_a_long_window_is_capped():
    process = KeyedAR1(0.999999, SIGMA, seed=50, max_window=100)

    assert process.window == 100


# ------------------------------------------------------------
# It is still an AR(1)
# ------------------------------------------------------------

def test_the_stationary_spread_is_sigma():
    values = walk(KeyedAR1(PHI, SIGMA, seed=50), 0, 20_000)

    assert statistics.fmean(values) == pytest.approx(0.0, abs=0.05)
    assert statistics.stdev(values) == pytest.approx(SIGMA, rel=0.05)


@pytest.mark.parametrize("phi", [0.0, 0.3, 0.65, 0.9])
def test_the_persistence_is_phi(phi):
    values = walk(KeyedAR1(phi, SIGMA, seed=50), 0, 20_000)

    assert lag1_autocorrelation(values) == pytest.approx(phi, abs=0.03)


def test_the_autocorrelation_decays_geometrically():
    values = walk(KeyedAR1(PHI, SIGMA, seed=50), 0, 20_000)
    mean = statistics.fmean(values)
    variance = sum((value - mean) ** 2 for value in values)

    for lag in (1, 2, 3, 5):
        numerator = sum(
            (values[i] - mean) * (values[i + lag] - mean)
            for i in range(len(values) - lag)
        )
        assert numerator / variance == pytest.approx(PHI ** lag, abs=0.03)


def test_zero_sigma_is_identically_zero():
    process = KeyedAR1(PHI, 0.0, seed=50)

    assert process.value_at(0) == 0.0
    assert process.value_at(10_000) == 0.0


@pytest.mark.parametrize("kwargs,match", [
    (dict(phi=1.0, sigma=SIGMA), "phi"),
    (dict(phi=-0.1, sigma=SIGMA), "phi"),
    (dict(phi=PHI, sigma=-1.0), "sigma"),
    (dict(phi=PHI, sigma=SIGMA, tolerance=0.0), "tolerance"),
    (dict(phi=PHI, sigma=SIGMA, tolerance=1.0), "tolerance"),
])
def test_invalid_ar1_configuration_raises(kwargs, match):
    with pytest.raises(ValueError, match=match):
        KeyedAR1(**kwargs)


# ------------------------------------------------------------
# The keyed Ornstein-Uhlenbeck
# ------------------------------------------------------------

OU_SIGMA = 0.12
OU_TAU_S = 10800.0


def sample(process, hours, dt, offset=0.0):
    steps = int(hours * 3600.0 / dt)
    return [process.value_at(offset + step * dt) for step in range(steps)]


def test_an_instant_is_the_same_wherever_the_run_started():
    instant = 1.8e9

    asked_cold = KeyedOrnsteinUhlenbeck(OU_SIGMA, OU_TAU_S, seed=7).value_at(instant)

    walked = KeyedOrnsteinUhlenbeck(OU_SIGMA, OU_TAU_S, seed=7)
    sample(walked, hours=48, dt=600.0, offset=instant - 48 * 3600.0)

    assert walked.value_at(instant) == asked_cold
    assert walked.value == asked_cold


@pytest.mark.parametrize("dt", [10.0, 60.0, 600.0])
def test_the_stationary_spread_does_not_depend_on_the_timestep(dt):
    """
    The lattice is tied to tau, not to the caller's step, so sampling the
    same process at 10 s and at 600 s sees the same spread. 600 hours is 200
    correlation times, which pins the spread to a few percent.
    """
    values = sample(
        KeyedOrnsteinUhlenbeck(OU_SIGMA, OU_TAU_S, seed=7),
        hours=600, dt=dt,
    )

    assert statistics.fmean(values) == pytest.approx(0.0, abs=0.04)
    assert statistics.stdev(values) == pytest.approx(OU_SIGMA, rel=0.2)


def test_the_autocorrelation_matches_the_analytic_value():
    dt = OU_TAU_S
    values = sample(
        KeyedOrnsteinUhlenbeck(OU_SIGMA, OU_TAU_S, seed=7),
        hours=20_000, dt=dt,
    )

    assert lag1_autocorrelation(values) == pytest.approx(math.exp(-1.0), abs=0.05)


def test_the_lattice_is_a_fraction_of_the_correlation_time():
    process = KeyedOrnsteinUhlenbeck(OU_SIGMA, OU_TAU_S, seed=7)

    assert process.lattice_s < OU_TAU_S / 8.0

    # Held between lattice points, so a step far shorter than the lattice
    # repeats rather than redrawing.
    assert process.value_at(1.8e9) == process.value_at(1.8e9 + 1.0)


def test_zero_sigma_is_identically_zero_over_time():
    process = KeyedOrnsteinUhlenbeck(0.0, OU_TAU_S, seed=7)

    assert process.value_at(0.0) == 0.0
    assert process.value_at(1.8e9) == 0.0
    assert process.value == 0.0


@pytest.mark.parametrize("kwargs,match", [
    (dict(sigma=-1.0, tau_s=OU_TAU_S), "sigma"),
    (dict(sigma=OU_SIGMA, tau_s=0.0), "tau_s"),
    (dict(sigma=OU_SIGMA, tau_s=OU_TAU_S, steps_per_tau=0), "steps_per_tau"),
])
def test_invalid_ou_configuration_raises(kwargs, match):
    with pytest.raises(ValueError, match=match):
        KeyedOrnsteinUhlenbeck(**kwargs)
