import math
import random
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class VoltageEvent:
    start_time: float
    duration: float
    delta_v: float  # relative deviation (e.g. -0.1 for -10%)
    shape: str = "step"  # "step" or "ramp"


class GridVoltageModel:
    """
    Deterministic grid voltage model (L-L RMS).

    Features:
    - Nominal voltage
    - Brownian drift with mean reversion
    - Low-pass filtered noise
    - Sag / swell events
    - Safe voltage bounds
    """

    def __init__(
        self,
        nominal_v_ll: float = 400.0,
        noise_std: float = 0.5,
        drift_std: float = 0.05,
        noise_tau: float = 10.0,
        mean_reversion_tau: float = 600.0,
        min_v_ll: float = 300.0,
        max_v_ll: float = 480.0,
        seed: Optional[int] = None,
    ):
        self.nominal_v_ll = nominal_v_ll
        self.noise_std = noise_std
        self.drift_std = drift_std
        self.noise_tau = noise_tau
        self.mean_reversion_tau = mean_reversion_tau
        self.min_v_ll = min_v_ll
        self.max_v_ll = max_v_ll

        self._rng = random.Random(seed)

        self._drift = 0.0
        self._noise = 0.0
        self._voltage = nominal_v_ll

        self._events: List[VoltageEvent] = []

    # --------------------------------------------------

    def add_event(self, event: VoltageEvent):
        self._events.append(event)

    def clear_events(self):
        self._events.clear()

    # --------------------------------------------------

    def update(self, sim_time: float, dt: float):
        dt = max(dt, 1e-9)

        # ---- Drift (Brownian + mean reversion)
        self._drift += self._rng.gauss(0.0, self.drift_std * math.sqrt(dt))

        if self.mean_reversion_tau > 0:
            self._drift *= math.exp(-dt / self.mean_reversion_tau)

        # ---- Noise (low-pass)
        noise_target = self._rng.gauss(0.0, self.noise_std)
        alpha = 1.0 - math.exp(-dt / self.noise_tau)
        self._noise += alpha * (noise_target - self._noise)

        # ---- Events
        event_delta = 0.0
        remaining_events = []

        for ev in self._events:

            # Drop expired events
            if sim_time > ev.start_time + ev.duration:
                continue

            remaining_events.append(ev)

            if ev.start_time <= sim_time <= ev.start_time + ev.duration:
                local_t = sim_time - ev.start_time

                if ev.shape == "step":
                    event_delta += ev.delta_v

                elif ev.shape == "ramp":
                    event_delta += ev.delta_v * (local_t / ev.duration)

        self._events = remaining_events

        multiplier = 1.0 + event_delta

        voltage = (
            self.nominal_v_ll * multiplier
            + self._drift
            + self._noise
        )

        # ---- Clamp
        self._voltage = max(self.min_v_ll, min(self.max_v_ll, voltage))

    # --------------------------------------------------

    def get_voltage_ll(self) -> float:
        return self._voltage

    def get_voltage_ln(self) -> float:
        return self._voltage / math.sqrt(3)