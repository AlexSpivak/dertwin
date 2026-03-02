import unittest

from dertwin.devices.external.grid_frequency import (
    GridFrequencyModel,
    FrequencyEvent,
)


class TestGridFrequencyModel(unittest.TestCase):

    def setUp(self):
        # Deterministic baseline model
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
        """
        Advances model in simulation time.
        """
        sim_time = 0.0
        steps = int(seconds / dt)

        # the number of steps should much number of seconds that's why +1
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

    # --------------------------------------------------
    # Step event
    # --------------------------------------------------

    def test_step_event_applied(self):
        self.model.add_event(
            FrequencyEvent(
                start_time=0.0,
                duration=10.0,
                delta_hz=-0.2,
                shape="step",
            )
        )

        self.model.update(0.0, 1.0)
        self.assertAlmostEqual(self.model.get_frequency(), 49.8, places=6)

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

        # Advance to midpoint (5 seconds)
        self.step_model(self.model, seconds=5)

        # Half of -0.4 should be applied
        self.assertAlmostEqual(self.model.get_frequency(), 49.8, places=6)

    # --------------------------------------------------
    # Event expiration
    # --------------------------------------------------

    def test_event_expires(self):
        self.model.add_event(
            FrequencyEvent(
                start_time=0.0,
                duration=2.0,
                delta_hz=-0.3,
                shape="step",
            )
        )

        # During event
        self.model.update(0.0, 1.0)
        self.assertAlmostEqual(self.model.get_frequency(), 49.7, places=6)

        # After expiration (intentionally skipped sim_time = 2.0 -
        # because it will still have 49.7 frequency, since duration is 2.0)
        self.model.update(3.0, 1.0)
        self.assertAlmostEqual(self.model.get_frequency(), 50.0, places=6)

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
            nominal_hz=50.0,
            noise_std=0.0,
            drift_std=0.01,
            seed=123,
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

            self.assertAlmostEqual(
                m1.get_frequency(),
                m2.get_frequency(),
                places=6,
            )

            sim_time += 1.0

    # --------------------------------------------------
    # Frequency bounds
    # --------------------------------------------------

    def test_frequency_bounds_respected(self):
        model = GridFrequencyModel(
            nominal_hz=50.0,
            noise_std=0.5,
            drift_std=0.2,
            seed=7,
            min_hz=48.0,
            max_hz=52.0,
        )

        sim_time = 0.0
        values = []

        for _ in range(500):
            model.update(sim_time, 1.0)
            values.append(model.get_frequency())
            sim_time += 1.0

        # Frequency never gets out of bound even with high level of noise and drift
        self.assertGreaterEqual(min(values), 48.0)
        self.assertLessEqual(max(values), 52.0)

    # --------------------------------------------------
    # Large disturbance (FCR scenario)
    # --------------------------------------------------

    def test_large_frequency_drop_fcr_case(self):
        """
        Simulate 49.5 Hz event (typical FCR-D activation level).
        """

        model = GridFrequencyModel(
            nominal_hz=50.0,
            noise_std=0.0,
            drift_std=0.0,
        )

        model.add_event(
            FrequencyEvent(
                start_time=10.0,
                duration=5.0,
                delta_hz=-0.5,
                shape="step",
            )
        )

        sim_time = 0.0

        for _ in range(15):
            model.update(sim_time, 1.0)
            sim_time += 1.0

        self.assertAlmostEqual(model.get_frequency(), 49.5, places=6)

    # --------------------------------------------------
    # Multiple overlapping events
    # --------------------------------------------------

    def test_overlapping_events(self):
        model = GridFrequencyModel(
            nominal_hz=50.0,
            noise_std=0.0,
            drift_std=0.0,
        )

        model.add_event(
            FrequencyEvent(0.0, 10.0, -0.2, "step")
        )

        model.add_event(
            FrequencyEvent(0.0, 10.0, -0.3, "step")
        )

        model.update(0.0, 1.0)

        self.assertAlmostEqual(model.get_frequency(), 49.5, places=6)


if __name__ == "__main__":
    unittest.main()