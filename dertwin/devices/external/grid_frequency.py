import math
import random
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class FrequencyEvent:
    start_time: float
    duration: float
    delta_hz: float
    shape: str = "step"  # "step" or "ramp"


class GridFrequencyModel:
    """
    Deterministic simulation-time driven grid frequency model.

    Features:
    - Brownian drift with configurable mean reversion
    - Low-pass filtered noise
    - Deterministic disturbance events
    - Frequency clamping to safe bounds
    - Fully simulation-time driven
    """

    def __init__(
        self,
        nominal_hz: float = 50.0,
        noise_std: float = 0.002,
        drift_std: float = 0.0002,
        noise_tau: float = 5.0,
        mean_reversion_tau: float = 300.0,
        min_hz: float = 45.0,
        max_hz: float = 55.0,
        seed: Optional[int] = None,
    ):
        self.nominal_hz = nominal_hz
        self.noise_std = noise_std
        self.drift_std = drift_std
        self.noise_tau = noise_tau
        self.mean_reversion_tau = mean_reversion_tau
        self.min_hz = min_hz
        self.max_hz = max_hz

        self._rng = random.Random(seed)

        self._drift = 0.0
        self._noise = 0.0
        self._frequency = nominal_hz

        self._events: List[FrequencyEvent] = []

    # --------------------------------------------------
    # Event handling
    # --------------------------------------------------
    def add_event(self, event: FrequencyEvent):
        self._events.append(event)

    def clear_events(self):
        self._events.clear()

    # --------------------------------------------------
    # Main frequency computation
    # --------------------------------------------------

    def update(self, sim_time: float, dt: float):
        dt = max(dt, 1e-9)

        # ---- Drift (Brownian + mean reversion)
        self._drift += self._rng.gauss(0.0, self.drift_std * math.sqrt(dt))

        if self.mean_reversion_tau > 0:
            self._drift *= math.exp(-dt / self.mean_reversion_tau)

        # ---- Noise (low-pass filtered)
        noise_target = self._rng.gauss(0.0, self.noise_std)
        alpha = 1.0 - math.exp(-dt / self.noise_tau)
        self._noise += alpha * (noise_target - self._noise)

        # ---- Event contribution
        event_delta = 0.0
        remaining_events = []

        for ev in self._events:

            # If event expired → drop it
            if sim_time > ev.start_time + ev.duration:
                continue

            # Otherwise keep it
            remaining_events.append(ev)

            # If event is active → apply effect
            if ev.start_time <= sim_time <= ev.start_time + ev.duration:
                local_t = sim_time - ev.start_time

                if ev.shape == "step":
                    event_delta += ev.delta_hz

                elif ev.shape == "ramp":
                    event_delta += ev.delta_hz * (local_t / ev.duration)

        # Update event list after evaluation
        self._events = remaining_events

        freq = (
            self.nominal_hz
            + self._drift
            + self._noise
            + event_delta
        )

        # ---- Clamp frequency
        self._frequency = max(self.min_hz, min(self.max_hz, freq))

    # --------------------------------------------------

    def get_frequency(self) -> float:
        return self._frequency

class ConstantGridFrequencyModel(GridFrequencyModel):
    def __init__(self, frequency_hz: float = 50.0):
        super().__init__()
        self._frequency = frequency_hz

    def update(self, sim_time: float, dt: float):
        pass

    def get_frequency(self) -> float:
        return self._frequency