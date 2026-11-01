"""
Reusable stochastic processes for external world models.

Standard library only, seedable, and deterministic for a given seed.
"""

import math
import random
from typing import Optional


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
