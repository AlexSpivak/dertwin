import unittest
import math

from dertwin.devices.external.grid_voltage import (
    GridVoltageModel,
    VoltageEvent,
)


class TestGridVoltageModel(unittest.TestCase):

    def setUp(self):
        self.model = GridVoltageModel(
            nominal_v_ll=400.0,
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

        for _ in range(steps):
            model.update(sim_time, dt)
            sim_time += dt

    # --------------------------------------------------
    # Baseline
    # --------------------------------------------------

    def test_nominal_voltage_no_events(self):
        self.model.update(0.0, 1.0)
        self.assertAlmostEqual(self.model.get_voltage_ll(), 400.0, places=6)

    def test_voltage_ln_conversion(self):
        self.model.update(0.0, 1.0)
        self.assertAlmostEqual(
            self.model.get_voltage_ln(),
            400.0 / math.sqrt(3),
            places=6,
        )

    # --------------------------------------------------
    # Step sag
    # --------------------------------------------------

    def test_voltage_sag_step(self):
        self.model.add_event(
            VoltageEvent(
                start_time=0.0,
                duration=10.0,
                delta_v=-0.1,  # -10%
                shape="step",
            )
        )

        self.model.update(0.0, 1.0)

        self.assertAlmostEqual(
            self.model.get_voltage_ll(),
            360.0,  # 400 * 0.9
            places=6,
        )

    # --------------------------------------------------
    # Ramp swell
    # --------------------------------------------------

    def test_voltage_ramp_midpoint(self):
        self.model.add_event(
            VoltageEvent(
                start_time=0.0,
                duration=10.0,
                delta_v=0.2,  # +20%
                shape="ramp",
            )
        )

        sim_time = 0.0

        # Advance to t=5
        for _ in range(6):
            self.model.update(sim_time, 1.0)
            sim_time += 1.0

        # 50% of 20% = 10%
        expected = 400.0 * 1.1
        self.assertAlmostEqual(
            self.model.get_voltage_ll(),
            expected,
            places=6,
        )

    # --------------------------------------------------
    # Event expiration
    # --------------------------------------------------

    def test_event_expires(self):
        self.model.add_event(
            VoltageEvent(
                start_time=0.0,
                duration=1.0,
                delta_v=-0.1,
                shape="step",
            )
        )

        # During event
        self.model.update(0.0, 1.0)
        self.assertAlmostEqual(self.model.get_voltage_ll(), 360.0, places=6)

        # After expiration
        self.model.update(2.0, 1.0)
        self.assertAlmostEqual(self.model.get_voltage_ll(), 400.0, places=6)

    # --------------------------------------------------
    # Determinism
    # --------------------------------------------------

    def test_deterministic_with_seed(self):
        m1 = GridVoltageModel(seed=123)
        m2 = GridVoltageModel(seed=123)

        sim_time = 0.0

        for _ in range(200):
            m1.update(sim_time, 1.0)
            m2.update(sim_time, 1.0)

            self.assertAlmostEqual(
                m1.get_voltage_ll(),
                m2.get_voltage_ll(),
                places=6,
            )

            sim_time += 1.0

    # --------------------------------------------------
    # Bounds
    # --------------------------------------------------

    def test_voltage_bounds(self):
        model = GridVoltageModel(
            nominal_v_ll=400.0,
            noise_std=20.0,
            drift_std=10.0,
            min_v_ll=350.0,
            max_v_ll=450.0,
            seed=7,
        )

        sim_time = 0.0
        values = []

        for _ in range(300):
            model.update(sim_time, 1.0)
            values.append(model.get_voltage_ll())
            sim_time += 1.0

        self.assertGreaterEqual(min(values), 350.0)
        self.assertLessEqual(max(values), 450.0)

    # --------------------------------------------------
    # Severe sag scenario
    # --------------------------------------------------

    def test_severe_voltage_sag(self):
        model = GridVoltageModel(
            nominal_v_ll=400.0,
            noise_std=0.0,
            drift_std=0.0,
            min_v_ll=150.0 # setting up low for the unittest pass 50% sag
        )

        model.add_event(
            VoltageEvent(
                start_time=5.0,
                duration=5.0,
                delta_v=-0.5,  # -50%
                shape="step",
            )
        )

        sim_time = 0.0

        for _ in range(10):
            model.update(sim_time, 1.0)
            sim_time += 1.0

        self.assertAlmostEqual(model.get_voltage_ll(), 200.0, places=6)


if __name__ == "__main__":
    unittest.main()