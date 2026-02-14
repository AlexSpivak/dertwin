import math
import random
import time
import logging
from dataclasses import dataclass
from typing import Optional, List


logger = logging.getLogger(__name__)

@dataclass
class FrequencyEvent:
    """
    Represents a frequency disturbance event.
    """
    start_time: float
    duration: float
    delta_hz: float
    shape: str = "step"   # step | ramp


class GridFrequencyModel:
    """
    Continuous-time grid frequency model.

    Features:
    - Smooth stochastic drift and noise
    - Deterministic behavior with seed
    - Manual events (for unit tests & scenarios)
    - Automatic Poisson-scheduled disturbance events
    """

    def __init__(
        self,
        nominal_hz: float = 50.0,
        noise_std: float = 0.002,        # measurement noise (Hz)
        drift_std: float = 0.0002,       # slow drift (Hz / sqrt(s))
        noise_tau: float = 5.0,          # LPF time constant for noise (s)
        seed: Optional[int] = None,
        auto_events: bool = False,
        event_rate: float = 0.1,        # events per second
        max_events: int = 1,            # keep single active disturbance
    ):
        self.nominal_hz = nominal_hz
        self.noise_std = noise_std
        self.drift_std = drift_std
        self.noise_tau = noise_tau

        self.auto_events = auto_events
        self.event_rate = event_rate
        self.max_events = max_events

        self._rng = random.Random(seed)
        self._start_time = time.time()
        self._last_ts = self._start_time

        # Internal state
        self._drift = 0.0
        self._noise = 0.0
        self._freq = nominal_hz

        # Event handling
        self._events: List[FrequencyEvent] = []
        self._next_event_time: Optional[float] = None

    # --------------------------------------------------
    # Event handling
    # --------------------------------------------------
    def add_event(self, event: FrequencyEvent):
        self._events.append(event)

    def clear_events(self):
        self._events.clear()

    def _cleanup_expired_events(self, t: float):
        """
        Remove events whose duration has fully elapsed.
        """
        self._events = [
            ev for ev in self._events
            if t <= ev.start_time + ev.duration
        ]
    # --------------------------------------------------
    # Automatic event scheduling (Poisson process)
    # --------------------------------------------------
    def _schedule_next_event(self, now: float):
        if self.event_rate <= 0:
            self._next_event_time = None
            return

        wait_time = self._rng.expovariate(self.event_rate)
        self._next_event_time = now + wait_time

    def _maybe_generate_event(self, t: float):
        if not self.auto_events:
            return

        if len(self._events) >= self.max_events:
            return

        if self._next_event_time is None:
            self._schedule_next_event(t)
            return

        if t >= self._next_event_time:
            delta = self._rng.choice([-1.0, 1.0]) * self._rng.uniform(0.05, 0.4)

            event = FrequencyEvent(
                start_time=t,
                duration=self._rng.uniform(60.0, 120.0), # each disturbance event can take between 1 and 2 minute
                delta_hz=delta,
                shape=self._rng.choice(["step", "ramp"]),
            )

            logger.info(
                "Auto frequency event | Δf=%+.3f Hz | duration=%.1fs | shape=%s",
                delta,
                event.duration,
                event.shape,
            )

            self._events.append(event)
            self._schedule_next_event(t)

    # --------------------------------------------------
    # Main frequency computation
    # --------------------------------------------------
    def get_frequency(self, now: Optional[float] = None) -> float:
        if now is None:
            now = time.time()

        dt = max(now - self._last_ts, 1e-3)
        self._last_ts = now
        t = now - self._start_time

        # ---- Remove expired events first
        self._cleanup_expired_events(t)

        # ---- Possibly generate automatic event
        self._maybe_generate_event(t)

        # ---- Drift (Brownian motion with mean reversion)
        self._drift += self._rng.gauss(0.0, self.drift_std * math.sqrt(dt))
        self._drift *= math.exp(-dt / 300.0)   # 5-minute mean reversion

        # ---- Noise (low-pass filtered)
        noise_target = self._rng.gauss(0.0, self.noise_std)
        alpha = 1.0 - math.exp(-dt / self.noise_tau)
        self._noise += alpha * (noise_target - self._noise)

        # ---- Event contribution
        event_delta = 0.0
        for ev in self._events:
            if ev.start_time <= t <= ev.start_time + ev.duration:
                local_t = t - ev.start_time
                if ev.shape == "step":
                    event_delta += ev.delta_hz
                elif ev.shape == "ramp":
                    event_delta += ev.delta_hz * (local_t / ev.duration)

        # ---- Final frequency
        self._freq = (
            self.nominal_hz
            + self._drift
            + self._noise
            + event_delta
        )
        return self._freq
