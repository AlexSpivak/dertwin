import unittest
import math

from dertwin.devices.external.grid_voltage import (
    GridVoltageModel,
    ConstantGridVoltageModel,
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

    def advance_to(self, model, sim_time: float, dt: float = 1.0):
        t = 0.0
        while t <= sim_time:
            model.update(t, dt)
            t += dt

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

    def test_voltage_ln_consistent_with_ll(self):
        """LN must always equal LL / sqrt(3)."""
        self.model.update(0.0, 1.0)
        self.assertAlmostEqual(
            self.model.get_voltage_ln(),
            self.model.get_voltage_ll() / math.sqrt(3),
            places=6,
        )

    # --------------------------------------------------
    # Step sag
    # --------------------------------------------------

    def test_voltage_sag_step(self):
        self.model.add_event(VoltageEvent(
            start_time=0.0, duration=10.0, delta_v=-0.1, shape="step",
        ))
        self.model.update(0.0, 1.0)
        self.assertAlmostEqual(self.model.get_voltage_ll(), 360.0, places=6)

    def test_voltage_swell_step(self):
        self.model.add_event(VoltageEvent(
            start_time=0.0, duration=10.0, delta_v=0.1, shape="step",
        ))
        self.model.update(0.0, 1.0)
        self.assertAlmostEqual(self.model.get_voltage_ll(), 440.0, places=6)

    def test_step_sag_before_start_is_nominal(self):
        """Before event start_time, voltage must be nominal."""
        self.model.add_event(VoltageEvent(
            start_time=5.0, duration=10.0, delta_v=-0.1, shape="step",
        ))
        self.model.update(3.0, 1.0)
        self.assertAlmostEqual(self.model.get_voltage_ll(), 400.0, places=6)

    # --------------------------------------------------
    # Ramp swell
    # --------------------------------------------------

    def test_voltage_ramp_midpoint(self):
        self.model.add_event(VoltageEvent(
            start_time=0.0, duration=10.0, delta_v=0.2, shape="ramp",
        ))
        sim_time = 0.0
        for _ in range(6):
            self.model.update(sim_time, 1.0)
            sim_time += 1.0
        self.assertAlmostEqual(self.model.get_voltage_ll(), 400.0 * 1.1, places=6)

    def test_voltage_ramp_at_start_is_nominal(self):
        """Ramp starts at 0% effect at t=start_time."""
        self.model.add_event(VoltageEvent(
            start_time=0.0, duration=10.0, delta_v=0.2, shape="ramp",
        ))
        self.model.update(0.0, 1.0)
        self.assertAlmostEqual(self.model.get_voltage_ll(), 400.0, places=6)

    def test_voltage_ramp_at_end_is_full(self):
        """Ramp reaches full delta at t=start+duration."""
        self.model.add_event(VoltageEvent(
            start_time=0.0, duration=10.0, delta_v=0.2, shape="ramp",
        ))
        sim_time = 0.0
        for _ in range(11):
            self.model.update(sim_time, 1.0)
            sim_time += 1.0
        self.assertAlmostEqual(self.model.get_voltage_ll(), 400.0 * 1.2, places=6)

    # --------------------------------------------------
    # Event expiration
    # --------------------------------------------------

    def test_event_expires(self):
        self.model.add_event(VoltageEvent(
            start_time=0.0, duration=1.0, delta_v=-0.1, shape="step",
        ))
        self.model.update(0.0, 1.0)
        self.assertAlmostEqual(self.model.get_voltage_ll(), 360.0, places=6)

        self.model.update(2.0, 1.0)
        self.assertAlmostEqual(self.model.get_voltage_ll(), 400.0, places=6)

    def test_multiple_sequential_events(self):
        """Second event fires after first has expired."""
        self.model.add_event(VoltageEvent(
            start_time=0.0, duration=2.0, delta_v=-0.1, shape="step",
        ))
        self.model.add_event(VoltageEvent(
            start_time=5.0, duration=2.0, delta_v=0.1, shape="step",
        ))

        self.model.update(1.0, 1.0)
        self.assertAlmostEqual(self.model.get_voltage_ll(), 360.0, places=6)

        self.model.update(3.5, 1.0)
        self.assertAlmostEqual(self.model.get_voltage_ll(), 400.0, places=6)

        self.model.update(6.0, 1.0)
        self.assertAlmostEqual(self.model.get_voltage_ll(), 440.0, places=6)

    def test_overlapping_events_accumulate(self):
        """Two simultaneous step events should combine their delta_v."""
        self.model.add_event(VoltageEvent(
            start_time=0.0, duration=10.0, delta_v=-0.1, shape="step",
        ))
        self.model.add_event(VoltageEvent(
            start_time=0.0, duration=10.0, delta_v=-0.1, shape="step",
        ))
        self.model.update(1.0, 1.0)
        # -10% + -10% = -20% → 400 * 0.8 = 320
        self.assertAlmostEqual(self.model.get_voltage_ll(), 320.0, places=6)

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
            self.assertAlmostEqual(m1.get_voltage_ll(), m2.get_voltage_ll(), places=6)
            sim_time += 1.0

    def test_different_seeds_differ(self):
        m1 = GridVoltageModel(nominal_v_ll=400.0, noise_std=5.0, seed=1)
        m2 = GridVoltageModel(nominal_v_ll=400.0, noise_std=5.0, seed=2)
        readings = []
        sim_time = 0.0
        for _ in range(50):
            m1.update(sim_time, 1.0)
            m2.update(sim_time, 1.0)
            readings.append((m1.get_voltage_ll(), m2.get_voltage_ll()))
            sim_time += 1.0
        self.assertFalse(all(a == b for a, b in readings))

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

    def test_voltage_ln_never_negative(self):
        """LN voltage must always be positive."""
        model = GridVoltageModel(
            nominal_v_ll=400.0, noise_std=10.0, drift_std=5.0,
            min_v_ll=10.0, seed=99,
        )
        sim_time = 0.0
        for _ in range(200):
            model.update(sim_time, 1.0)
            self.assertGreater(model.get_voltage_ln(), 0.0)
            sim_time += 1.0

    # --------------------------------------------------
    # Severe sag scenario
    # --------------------------------------------------

    def test_severe_voltage_sag(self):
        model = GridVoltageModel(
            nominal_v_ll=400.0, noise_std=0.0, drift_std=0.0, min_v_ll=150.0,
        )
        model.add_event(VoltageEvent(
            start_time=5.0, duration=5.0, delta_v=-0.5, shape="step",
        ))
        sim_time = 0.0
        for _ in range(10):
            model.update(sim_time, 1.0)
            sim_time += 1.0
        self.assertAlmostEqual(model.get_voltage_ll(), 200.0, places=6)


# --------------------------------------------------
# ConstantGridVoltageModel
# --------------------------------------------------

class TestConstantGridVoltageModel(unittest.TestCase):

    def test_always_returns_configured_voltage(self):
        model = ConstantGridVoltageModel(400.0)
        for t in (0.0, 100.0, 3600.0):
            model.update(t, 1.0)
            self.assertAlmostEqual(model.get_voltage_ll(), 400.0, places=6)

    def test_ln_conversion_correct(self):
        model = ConstantGridVoltageModel(400.0)
        model.update(0.0, 1.0)
        self.assertAlmostEqual(
            model.get_voltage_ln(), 400.0 / math.sqrt(3), places=6,
        )

    def test_no_drift_over_time(self):
        model = ConstantGridVoltageModel(230.0)
        readings = []
        for t in range(1000):
            model.update(float(t), 1.0)
            readings.append(model.get_voltage_ll())
        self.assertEqual(len(set(readings)), 1)

    def test_add_event_has_no_effect(self):
        """ConstantGridVoltageModel must ignore events — it's a stub."""
        model = ConstantGridVoltageModel(400.0)
        model.add_event(VoltageEvent(
            start_time=0.0, duration=100.0, delta_v=-0.5, shape="step",
        ))
        model.update(1.0, 1.0)
        self.assertAlmostEqual(model.get_voltage_ll(), 400.0, places=6)


if __name__ == "__main__":
    unittest.main()