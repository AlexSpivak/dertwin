import unittest

from dertwin.devices.external.grid_frequency import (
    GridFrequencyModel,
    ConstantGridFrequencyModel,
    FrequencyEvent,
)


class TestGridFrequencyModel(unittest.TestCase):

    def setUp(self):
        self.model = GridFrequencyModel(
            nominal_hz=50.0,
            noise_std=0.0,
            drift_std=0.0,
            seed=42,
        )

    # --------------------------------------------------
    # Helpers
    # --------------------------------------------------

    def step_model(self, model, seconds: float, dt: float = 1.0):
        sim_time = 0.0
        steps = int(seconds / dt)
        for _ in range(steps + 1):
            model.update(sim_time, dt)
            sim_time += dt

    # --------------------------------------------------
    # Baseline behavior
    # --------------------------------------------------

    def test_nominal_frequency_no_events(self):
        self.model.update(0.0, 1.0)
        self.assertAlmostEqual(self.model.get_frequency(), 50.0, places=6)

    def test_frequency_stable_without_noise_or_drift(self):
        self.step_model(self.model, seconds=100)
        self.assertAlmostEqual(self.model.get_frequency(), 50.0, places=6)

    def test_frequency_never_negative(self):
        """Frequency must be positive under any noise/drift combination."""
        model = GridFrequencyModel(
            nominal_hz=50.0, noise_std=5.0, drift_std=2.0,
            min_hz=0.1, seed=0,
        )
        sim_time = 0.0
        for _ in range(300):
            model.update(sim_time, 1.0)
            self.assertGreater(model.get_frequency(), 0.0)
            sim_time += 1.0

    # --------------------------------------------------
    # Step event
    # --------------------------------------------------

    def test_step_event_applied(self):
        self.model.add_event(FrequencyEvent(
            start_time=0.0, duration=10.0, delta_hz=-0.2, shape="step",
        ))
        self.model.update(0.0, 1.0)
        self.assertAlmostEqual(self.model.get_frequency(), 49.8, places=6)

    def test_step_event_before_start_is_nominal(self):
        """Event must not fire before start_time."""
        self.model.add_event(FrequencyEvent(
            start_time=5.0, duration=10.0, delta_hz=-0.2, shape="step",
        ))
        self.model.update(3.0, 1.0)
        self.assertAlmostEqual(self.model.get_frequency(), 50.0, places=6)

    def test_step_swell_event(self):
        self.model.add_event(FrequencyEvent(
            start_time=0.0, duration=10.0, delta_hz=0.3, shape="step",
        ))
        self.model.update(0.0, 1.0)
        self.assertAlmostEqual(self.model.get_frequency(), 50.3, places=6)

    # --------------------------------------------------
    # Ramp event
    # --------------------------------------------------

    def test_ramp_event_midpoint(self):
        self.model.add_event(FrequencyEvent(
            start_time=0.0, duration=10.0, delta_hz=-0.4, shape="ramp",
        ))
        self.step_model(self.model, seconds=5)
        self.assertAlmostEqual(self.model.get_frequency(), 49.8, places=6)

    def test_ramp_event_at_start_is_nominal(self):
        """Ramp must start at 0% effect at t=start_time."""
        self.model.add_event(FrequencyEvent(
            start_time=0.0, duration=10.0, delta_hz=-0.4, shape="ramp",
        ))
        self.model.update(0.0, 1.0)
        self.assertAlmostEqual(self.model.get_frequency(), 50.0, places=6)

    def test_ramp_event_at_end_is_full(self):
        """Ramp must reach full delta at t=start+duration."""
        self.model.add_event(FrequencyEvent(
            start_time=0.0, duration=10.0, delta_hz=-0.4, shape="ramp",
        ))
        self.step_model(self.model, seconds=10)
        self.assertAlmostEqual(self.model.get_frequency(), 49.6, places=6)

    # --------------------------------------------------
    # Event expiration
    # --------------------------------------------------

    def test_event_expires(self):
        self.model.add_event(FrequencyEvent(
            start_time=0.0, duration=2.0, delta_hz=-0.3, shape="step",
        ))
        self.model.update(0.0, 1.0)
        self.assertAlmostEqual(self.model.get_frequency(), 49.7, places=6)

        self.model.update(3.0, 1.0)
        self.assertAlmostEqual(self.model.get_frequency(), 50.0, places=6)

    def test_sequential_events(self):
        """Second event fires only after first expires."""
        self.model.add_event(FrequencyEvent(
            start_time=0.0, duration=3.0, delta_hz=-0.2, shape="step",
        ))
        self.model.add_event(FrequencyEvent(
            start_time=5.0, duration=3.0, delta_hz=0.2, shape="step",
        ))

        self.model.update(1.0, 1.0)
        self.assertAlmostEqual(self.model.get_frequency(), 49.8, places=6)

        self.model.update(4.0, 1.0)
        self.assertAlmostEqual(self.model.get_frequency(), 50.0, places=6)

        self.model.update(6.0, 1.0)
        self.assertAlmostEqual(self.model.get_frequency(), 50.2, places=6)

    # --------------------------------------------------
    # Noise behavior
    # --------------------------------------------------

    def test_noise_changes_frequency(self):
        noisy_model = GridFrequencyModel(
            nominal_hz=50.0, noise_std=0.02, drift_std=0.0, seed=1,
        )
        noisy_model.update(0.0, 1.0)
        f1 = noisy_model.get_frequency()
        noisy_model.update(1.0, 1.0)
        f2 = noisy_model.get_frequency()
        self.assertNotAlmostEqual(f1, f2, places=6)

    # --------------------------------------------------
    # Drift behavior
    # --------------------------------------------------

    def test_drift_accumulates(self):
        drift_model = GridFrequencyModel(
            nominal_hz=50.0, noise_std=0.0, drift_std=0.01, seed=123,
        )
        sim_time = 0.0
        values = []
        for _ in range(100):
            drift_model.update(sim_time, 1.0)
            values.append(drift_model.get_frequency())
            sim_time += 1.0
        self.assertNotAlmostEqual(values[0], values[-1], places=4)

    # --------------------------------------------------
    # Determinism with seed
    # --------------------------------------------------

    def test_deterministic_with_seed(self):
        m1 = GridFrequencyModel(seed=42)
        m2 = GridFrequencyModel(seed=42)
        sim_time = 0.0
        for _ in range(200):
            m1.update(sim_time, 1.0)
            m2.update(sim_time, 1.0)
            self.assertAlmostEqual(m1.get_frequency(), m2.get_frequency(), places=6)
            sim_time += 1.0

    def test_different_seeds_differ(self):
        m1 = GridFrequencyModel(nominal_hz=50.0, noise_std=0.05, seed=1)
        m2 = GridFrequencyModel(nominal_hz=50.0, noise_std=0.05, seed=2)
        readings = []
        sim_time = 0.0
        for _ in range(50):
            m1.update(sim_time, 1.0)
            m2.update(sim_time, 1.0)
            readings.append((m1.get_frequency(), m2.get_frequency()))
            sim_time += 1.0
        self.assertFalse(all(a == b for a, b in readings))

    # --------------------------------------------------
    # Frequency bounds
    # --------------------------------------------------

    def test_frequency_bounds_respected(self):
        model = GridFrequencyModel(
            nominal_hz=50.0, noise_std=0.5, drift_std=0.2,
            seed=7, min_hz=48.0, max_hz=52.0,
        )
        sim_time = 0.0
        values = []
        for _ in range(500):
            model.update(sim_time, 1.0)
            values.append(model.get_frequency())
            sim_time += 1.0
        self.assertGreaterEqual(min(values), 48.0)
        self.assertLessEqual(max(values), 52.0)

    # --------------------------------------------------
    # Large disturbance (FCR scenario)
    # --------------------------------------------------

    def test_large_frequency_drop_fcr_case(self):
        model = GridFrequencyModel(nominal_hz=50.0, noise_std=0.0, drift_std=0.0)
        model.add_event(FrequencyEvent(
            start_time=10.0, duration=5.0, delta_hz=-0.5, shape="step",
        ))
        sim_time = 0.0
        for _ in range(15):
            model.update(sim_time, 1.0)
            sim_time += 1.0
        self.assertAlmostEqual(model.get_frequency(), 49.5, places=6)

    # --------------------------------------------------
    # Overlapping events
    # --------------------------------------------------

    def test_overlapping_events(self):
        model = GridFrequencyModel(nominal_hz=50.0, noise_std=0.0, drift_std=0.0)
        model.add_event(FrequencyEvent(0.0, 10.0, -0.2, "step"))
        model.add_event(FrequencyEvent(0.0, 10.0, -0.3, "step"))
        model.update(0.0, 1.0)
        self.assertAlmostEqual(model.get_frequency(), 49.5, places=6)


# --------------------------------------------------
# ConstantGridFrequencyModel
# --------------------------------------------------

class TestConstantGridFrequencyModel(unittest.TestCase):

    def test_always_returns_configured_frequency(self):
        model = ConstantGridFrequencyModel(50.0)
        for t in (0.0, 100.0, 3600.0):
            model.update(t, 1.0)
            self.assertAlmostEqual(model.get_frequency(), 50.0, places=6)

    def test_no_drift_over_time(self):
        model = ConstantGridFrequencyModel(60.0)
        readings = []
        for t in range(1000):
            model.update(float(t), 1.0)
            readings.append(model.get_frequency())
        self.assertEqual(len(set(readings)), 1)

    def test_add_event_has_no_effect(self):
        """ConstantGridFrequencyModel must ignore events — it's a stub."""
        model = ConstantGridFrequencyModel(50.0)
        model.add_event(FrequencyEvent(
            start_time=0.0, duration=100.0, delta_hz=-0.5, shape="step",
        ))
        model.update(1.0, 1.0)
        self.assertAlmostEqual(model.get_frequency(), 50.0, places=6)

    def test_custom_frequency(self):
        model = ConstantGridFrequencyModel(60.0)
        model.update(0.0, 1.0)
        self.assertAlmostEqual(model.get_frequency(), 60.0, places=6)


if __name__ == "__main__":
    unittest.main()