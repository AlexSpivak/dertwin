import unittest
import time

from dertwin.devices.external.grid_frequency import GridFrequencyModel, FrequencyEvent


class TestGridFrequencyModel(unittest.TestCase):

    def setUp(self):
        # Fixed seed for deterministic tests
        self.model = GridFrequencyModel(
            nominal_hz=50.0,
            noise_std=0.0,
            drift_std=0.0,
            seed=42,
        )

    # --------------------------------------------------
    # Baseline behavior
    # --------------------------------------------------
    def test_nominal_frequency_no_events(self):
        f = self.model.get_frequency()
        self.assertAlmostEqual(f, 50.0, places=6)

    def test_frequency_stable_over_time(self):
        f1 = self.model.get_frequency()
        time.sleep(0.01)
        f2 = self.model.get_frequency()
        self.assertAlmostEqual(f1, f2, places=6)

    # --------------------------------------------------
    # Step event
    # --------------------------------------------------
    def test_step_event(self):
        self.model.add_event(
            FrequencyEvent(
                start_time=0.0,
                duration=10.0,
                delta_hz=-0.2,
                shape="step",
            )
        )

        f = self.model.get_frequency()
        self.assertAlmostEqual(f, 49.8, places=6)

    # --------------------------------------------------
    # Ramp event
    # --------------------------------------------------
    def test_ramp_event_midpoint(self):
        self.model.add_event(
            FrequencyEvent(
                start_time=0.0,
                duration=10.0,
                delta_hz=-0.4,
                shape="ramp",
            )
        )

        # simulate midpoint
        fake_now = self.model._start_time + 5.0
        f = self.model.get_frequency(now=fake_now)

        # Half of -0.4 applied
        self.assertAlmostEqual(f, 49.8, places=6)

    # --------------------------------------------------
    # Event expiration
    # --------------------------------------------------
    def test_event_expires(self):
        self.model.add_event(
            FrequencyEvent(
                start_time=0.0,
                duration=1.0,
                delta_hz=-0.3,
                shape="step",
            )
        )

        # After event duration
        fake_now = self.model._start_time + 2.0
        f = self.model.get_frequency(now=fake_now)

        self.assertAlmostEqual(f, 50.0, places=6)

    # --------------------------------------------------
    # Noise behavior
    # --------------------------------------------------
    def test_noise_changes_frequency(self):
        noisy_model = GridFrequencyModel(
            nominal_hz=50.0,
            noise_std=0.02,
            drift_std=0.0,
            seed=1,
        )

        f1 = noisy_model.get_frequency()
        f2 = noisy_model.get_frequency()

        self.assertNotAlmostEqual(f1, f2, places=6)

    # --------------------------------------------------
    # Drift behavior
    # --------------------------------------------------
    def test_drift_accumulates(self):
        drift_model = GridFrequencyModel(
            nominal_hz=50.0,
            noise_std=0.0,
            drift_std=0.01,
            seed=123,
        )

        values = [drift_model.get_frequency() for _ in range(50)]
        self.assertNotAlmostEqual(values[0], values[-1], places=4)

    def test_no_auto_events_when_disabled(self):
        model = GridFrequencyModel(auto_events=False)

        for _ in range(200):
            model.get_frequency()

        self.assertEqual(len(model._events), 0)

    def test_frequency_bounds(self):
        model = GridFrequencyModel(
            auto_events=True,
            event_rate=3.0,
            noise_std=0.01,
            drift_std=0.001,
            seed=7,
        )

        values = [model.get_frequency() for _ in range(500)]

        self.assertTrue(min(values) > 48.0)
        self.assertTrue(max(values) < 52.0)

    def test_deterministic_with_seed(self):
        m1 = GridFrequencyModel(auto_events=True, event_rate=2.0, seed=42)
        m2 = GridFrequencyModel(auto_events=True, event_rate=2.0, seed=42)

        for _ in range(100):
            f1 = m1.get_frequency()
            f2 = m2.get_frequency()
            self.assertAlmostEqual(f1, f2, places=6)

    def test_auto_events_generated(self):
        model = GridFrequencyModel(
            auto_events=True,
            event_rate=5.0,     # high rate for fast test
            seed=1,
        )

        start = time.time()
        while time.time() - start < 1.0:
            model.get_frequency()
            time.sleep(0.01)

        self.assertGreater(len(model._events), 0)

if __name__ == "__main__":
    unittest.main()
