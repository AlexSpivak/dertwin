"""
Reusable stochastic processes for external world models.

Standard library only, seedable, and deterministic for a given seed.

Two flavours, and the difference matters for anything long-lived:

- `OrnsteinUhlenbeck` is *free-running*: it carries a state and steps it once
  per tick. Its trajectory depends on where the process started, so a restart
  replays the same path from the same seed.
- `KeyedAR1` and `KeyedOrnsteinUhlenbeck` are *keyed*: the value at step n is a
  pure function of (seed, n), with no state carried between calls. A restart,
  or starting the run on a different date, cannot change what the process says
  about a given instant.

Keying is what lets a process that is meant to be slow — a weather regime, a
weekly load drift — survive a deployment that gets restarted twice a day.
"""

import hashlib
import math
import random
from typing import Dict, Optional


class OrnsteinUhlenbeck:
    """
    Mean-reverting process with a stationary N(0, sigma^2) distribution.

    The exact discrete-time solution is used rather than an Euler step, so
    `sigma` is the standard deviation of the process itself and the statistics
    do not depend on the timestep:

        a = exp(-dt / tau)
        x <- a*x + sqrt(1 - a^2) * sigma * N(0, 1)

    That matters for a simulator whose step is configurable: an Euler
    discretisation would quietly change the amplitude of the noise when you
    switch from 0.1 s to 60 s steps.

    The process starts drawn from its stationary distribution, so there is no
    warm-up transient. Pass an existing `rng` to draw from a shared random
    stream, so one seed drives several processes.
    """

    def __init__(
        self,
        sigma: float,
        tau_s: float,
        rng: Optional[random.Random] = None,
        seed: Optional[int] = None,
        start_at_stationary: bool = True,
    ):
        if sigma < 0.0:
            raise ValueError(f"sigma must be non-negative, got {sigma}")
        if tau_s <= 0.0:
            raise ValueError(f"tau_s must be positive, got {tau_s}")

        self.sigma = sigma
        self.tau_s = tau_s

        self._rng = rng if rng is not None else random.Random(seed)
        self._value = (
            self._rng.gauss(0.0, sigma)
            if start_at_stationary and sigma > 0.0
            else 0.0
        )

    @property
    def value(self) -> float:
        return self._value

    def set_value(self, value: float) -> None:
        """
        Place the process at `value`.

        For handing a signal over from a measurement to the model: starting
        the perturbation at the offset the measurement last showed makes the
        handover itself a step of zero, and the process then relaxes that
        offset away at its own `tau_s` rather than at some rate chosen for
        the occasion. Mean reversion does the rest, so nothing permanent is
        injected however far from zero it is placed.
        """

        self._value = float(value)

    def advance(self, dt: float) -> float:
        # Skipping the draw, rather than scaling it to zero, keeps the random
        # stream independent of how many steps a disabled process took.
        if dt <= 0.0 or self.sigma == 0.0:
            return self._value

        a = math.exp(-dt / self.tau_s)
        self._value = a * self._value + math.sqrt(
            1.0 - a * a
        ) * self.sigma * self._rng.gauss(0.0, 1.0)

        return self._value


# ----------------------------------------------------------------------
# Keyed (state-free) processes
# ----------------------------------------------------------------------

# Truncation tolerance for the keyed processes, as a fraction of sigma. The
# error it leaves is nine orders of magnitude below the signal; the point of
# fixing it as a constant is that the window length, and so the arithmetic, is
# the same on every machine and in every process.
DEFAULT_KEYED_TOLERANCE = 1e-9

# A phi close to 1 needs a long window. The cap keeps a pathological config
# from turning every day boundary into a million multiplications; it costs
# accuracy in the truncation, not reproducibility.
MAX_KEYED_WINDOW = 100_000

# Lattice resolution of KeyedOrnsteinUhlenbeck, in samples per correlation
# time. The process updates on this lattice rather than on every tick, which
# is what makes it a function of absolute time rather than of the tick pattern.
DEFAULT_STEPS_PER_TAU = 16


def derive_seed(seed: Optional[int], stream: str) -> int:
    """
    A seed for `stream`, derived from `seed`.

    Separate streams keep separate numbers: a process that draws from its own
    stream cannot shift another process's sequence by drawing more or fewer
    values. With `seed=None` the result is random, so an unseeded run stays
    random.
    """

    if seed is None:
        return random.Random().getrandbits(64)

    digest = hashlib.blake2b(
        f"{int(seed)}:{stream}".encode("utf-8"), digest_size=8
    ).digest()

    return int.from_bytes(digest, "big")


def keyed_standard_normal(seed: int, stream: str, index: int) -> float:
    """
    A standard normal draw indexed by an integer.

    Deterministic in (seed, stream, index) and independent across indices:
    the index is hashed into the seed of a throwaway generator rather than
    read off a position in one long stream, so any index can be evaluated
    directly, at the same cost, in any order.
    """

    digest = hashlib.blake2b(
        f"{seed}:{stream}:{index}".encode("utf-8"), digest_size=8
    ).digest()

    return random.Random(int.from_bytes(digest, "big")).gauss(0.0, 1.0)


class KeyedAR1:
    """
    Stationary AR(1) whose value at step n is a pure function of (seed, n).

    The recursion

        x_n = phi*x_{n-1} + sqrt(1 - phi^2) * sigma * e_n

    has the equivalent moving-average form

        x_n = sqrt(1 - phi^2) * sigma * sum_{j>=0} phi^j * e_{n-j}

    which needs no state at all: given the innovations, every step can be
    evaluated on its own. Drawing a fresh independent value per step instead
    would be stateless too, but it would throw away `phi` — the whole point of
    the process — so the sum is what keeps day-to-day persistence while making
    the result depend only on which step it is.

    The sum is truncated after `window` terms. Dropping the tail leaves an
    error with standard deviation `sigma * phi^window`, so

        window = ceil(log(tolerance) / log(phi))

    puts it `tolerance` below the process's own spread. Cost is therefore
    `window` multiply-adds per evaluation and one hash per innovation not
    already cached — both set by `phi` alone. Nothing here scales with the
    step number, so evaluating step 10 and step 10,000,000 cost the same:
    there is no epoch to count from and no catch-up loop to run.
    """

    def __init__(
        self,
        phi: float,
        sigma: float,
        seed: Optional[int] = None,
        stream: str = "ar1",
        tolerance: float = DEFAULT_KEYED_TOLERANCE,
        max_window: int = MAX_KEYED_WINDOW,
    ):
        if not 0.0 <= phi < 1.0:
            raise ValueError(f"phi must be in [0, 1), got {phi}")
        if sigma < 0.0:
            raise ValueError(f"sigma must be non-negative, got {sigma}")
        if not 0.0 < tolerance < 1.0:
            raise ValueError(f"tolerance must be in (0, 1), got {tolerance}")

        self.phi = phi
        self.sigma = sigma
        self.stream = stream
        self.seed = derive_seed(seed, stream)

        if phi == 0.0 or sigma == 0.0:
            self.window = 1
        else:
            self.window = min(
                max_window,
                max(1, math.ceil(math.log(tolerance) / math.log(phi))),
            )

        self._scale = sigma * math.sqrt(1.0 - phi * phi)
        self._innovations: Dict[int, float] = {}

    # --------------------------------------------------

    def _innovation(self, index: int) -> float:
        value = self._innovations.get(index)

        if value is None:
            value = keyed_standard_normal(self.seed, self.stream, index)

            # Walking forward leaves the old innovations behind. Dropping the
            # ones that have fallen out of the window keeps the cache bounded
            # without costing anything the next evaluation needs.
            if len(self._innovations) > 4 * self.window:
                oldest = index - self.window
                self._innovations = {
                    i: v for i, v in self._innovations.items() if i >= oldest
                }

            self._innovations[index] = value

        return value

    def value_at(self, index: int) -> float:
        if self.sigma == 0.0:
            return 0.0

        total = 0.0
        weight = 1.0

        for step in range(self.window):
            total += weight * self._innovation(index - step)
            weight *= self.phi

        return self._scale * total


class KeyedOrnsteinUhlenbeck:
    """
    Mean-reverting process with a stationary N(0, sigma^2) distribution, as a
    function of absolute time rather than of a sequence of steps.

    The process is sampled on a fixed internal lattice of `steps_per_tau`
    points per correlation time and held between lattice points. On the
    lattice it is the exact discrete-time solution — the same one
    `OrnsteinUhlenbeck` uses, with a = exp(-h/tau) for the lattice spacing h —
    so the stationary spread is exactly `sigma` and the autocorrelation at a
    lag of n lattice steps is exactly a^n, whatever the simulator's timestep.

    Fixing the lattice to the process's own correlation time, rather than to
    the tick, is what makes the value depend on *when* it is asked about
    instead of on how it got there. The price is that the process updates
    every tau/`steps_per_tau` seconds rather than every tick: a sixteenth of
    its own correlation time, which is well inside the resolution anything
    downstream can tell apart.
    """

    def __init__(
        self,
        sigma: float,
        tau_s: float,
        seed: Optional[int] = None,
        stream: str = "ou",
        steps_per_tau: int = DEFAULT_STEPS_PER_TAU,
        tolerance: float = DEFAULT_KEYED_TOLERANCE,
    ):
        if sigma < 0.0:
            raise ValueError(f"sigma must be non-negative, got {sigma}")
        if tau_s <= 0.0:
            raise ValueError(f"tau_s must be positive, got {tau_s}")
        if steps_per_tau < 1:
            raise ValueError(
                f"steps_per_tau must be at least 1, got {steps_per_tau}"
            )

        self.sigma = sigma
        self.tau_s = tau_s
        self.lattice_s = tau_s / steps_per_tau

        self._chain = KeyedAR1(
            phi=math.exp(-1.0 / steps_per_tau),
            sigma=sigma,
            seed=seed,
            stream=stream,
            tolerance=tolerance,
        )

        self._index: Optional[int] = None
        self._value = 0.0

    @property
    def window(self) -> int:
        return self._chain.window

    @property
    def value(self) -> float:
        """The value at the instant this process was last asked about."""
        return self._value

    def value_at(self, t: float) -> float:
        """
        The value at absolute time `t`, in seconds.

        `t` only has to be the same number for the same instant across runs;
        what it counts from is the caller's business.
        """

        if self.sigma == 0.0:
            return 0.0

        index = math.floor(t / self.lattice_s)

        if index != self._index:
            self._value = self._chain.value_at(index)
            self._index = index

        return self._value
