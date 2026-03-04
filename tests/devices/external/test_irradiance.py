import unittest
import math

from dertwin.devices.external.irradiance import IrradianceModel


class TestIrradianceModel(unittest.TestCase):

    def setUp(self):
        self.model = IrradianceModel(
            peak_irradiance_w_m2=1000.0,
            sunrise_hour=6.0,
            sunset_hour=18.0,
        )

    # --------------------------------------------------
    # Helpers
    # --------------------------------------------------

    def update_at_hour(self, hour: float):
        sim_time = hour * 3600.0
        self.model.update(sim_time, dt=1.0)

    # --------------------------------------------------
    # Night behavior
    # --------------------------------------------------

    def test_midnight_zero(self):
        self.update_at_hour(0.0)
        self.assertEqual(self.model.get_irradiance(), 0.0)

    def test_before_sunrise_zero(self):
        self.update_at_hour(5.0)
        self.assertEqual(self.model.get_irradiance(), 0.0)

    def test_at_sunrise_zero(self):
        self.update_at_hour(6.0)
        self.assertEqual(self.model.get_irradiance(), 0.0)

    def test_at_sunset_zero(self):
        self.update_at_hour(18.0)
        self.assertEqual(self.model.get_irradiance(), 0.0)

    def test_after_sunset_zero(self):
        self.update_at_hour(20.0)
        self.assertEqual(self.model.get_irradiance(), 0.0)

    def test_irradiance_never_negative(self):
        """Irradiance must be >= 0 at all hours."""
        for hour in range(0, 25):
            self.update_at_hour(float(hour))
            self.assertGreaterEqual(self.model.get_irradiance(), 0.0)

    # --------------------------------------------------
    # Day behavior
    # --------------------------------------------------

    def test_midday_peak(self):
        self.update_at_hour(12.0)
        self.assertAlmostEqual(self.model.get_irradiance(), 1000.0, places=6)

    def test_morning_value(self):
        self.update_at_hour(9.0)
        expected = 1000.0 * math.sin(math.pi * 0.25)
        self.assertAlmostEqual(self.model.get_irradiance(), expected, places=6)

    def test_afternoon_symmetry_with_morning(self):
        """Afternoon must mirror morning — sine curve is symmetric about solar noon."""
        self.update_at_hour(9.0)
        morning = self.model.get_irradiance()

        self.update_at_hour(15.0)  # 3 hours after noon, mirror of 9 AM
        afternoon = self.model.get_irradiance()

        self.assertAlmostEqual(morning, afternoon, places=6)

    def test_daytime_value_increases_toward_noon(self):
        self.update_at_hour(7.0)
        early = self.model.get_irradiance()

        self.update_at_hour(10.0)
        mid_morning = self.model.get_irradiance()

        self.update_at_hour(12.0)
        noon = self.model.get_irradiance()

        self.assertLess(early, mid_morning)
        self.assertLess(mid_morning, noon)

    def test_peak_irradiance_respected(self):
        """Peak irradiance at noon must equal the configured value."""
        model = IrradianceModel(peak_irradiance_w_m2=750.0)
        model.update(12 * 3600.0, 1.0)
        self.assertAlmostEqual(model.get_irradiance(), 750.0, places=6)

    def test_irradiance_never_exceeds_peak(self):
        """Irradiance must never exceed configured peak at any hour."""
        for hour_tenth in range(0, 240):
            hour = hour_tenth / 10.0
            self.model.update(hour * 3600.0, 1.0)
            self.assertLessEqual(self.model.get_irradiance(), 1000.0 + 1e-9)

    # --------------------------------------------------
    # Periodicity
    # --------------------------------------------------

    def test_daily_periodicity(self):
        self.model.update(12 * 3600, 1.0)
        day1 = self.model.get_irradiance()

        self.model.update((12 + 24) * 3600, 1.0)
        day2 = self.model.get_irradiance()

        self.assertAlmostEqual(day1, day2, places=6)

    def test_multi_day_periodicity(self):
        """Pattern must repeat identically across many days."""
        hours = [7.0, 12.0, 15.0, 17.5]
        for hour in hours:
            self.model.update(hour * 3600.0, 1.0)
            day1 = self.model.get_irradiance()

            self.model.update((hour + 72) * 3600.0, 1.0)  # 3 days later
            day3 = self.model.get_irradiance()

            self.assertAlmostEqual(day1, day3, places=6,
                                   msg=f"Periodicity failed at hour {hour}")

    # --------------------------------------------------
    # Determinism
    # --------------------------------------------------

    def test_deterministic(self):
        m1 = IrradianceModel()
        m2 = IrradianceModel()

        for hour in range(0, 24):
            sim_time = hour * 3600.0
            m1.update(sim_time, 1.0)
            m2.update(sim_time, 1.0)
            self.assertAlmostEqual(m1.get_irradiance(), m2.get_irradiance(), places=6)

    # --------------------------------------------------
    # Custom sunrise/sunset
    # --------------------------------------------------

    def test_custom_sun_times(self):
        model = IrradianceModel(
            peak_irradiance_w_m2=800.0,
            sunrise_hour=8.0,
            sunset_hour=16.0,
        )
        model.update(12 * 3600.0, 1.0)
        self.assertAlmostEqual(model.get_irradiance(), 800.0, places=6)

    def test_custom_sun_times_before_sunrise_zero(self):
        model = IrradianceModel(sunrise_hour=8.0, sunset_hour=16.0)
        model.update(7 * 3600.0, 1.0)
        self.assertEqual(model.get_irradiance(), 0.0)

    def test_custom_sun_times_after_sunset_zero(self):
        model = IrradianceModel(sunrise_hour=8.0, sunset_hour=16.0)
        model.update(17 * 3600.0, 1.0)
        self.assertEqual(model.get_irradiance(), 0.0)

    def test_short_day_peak_still_at_midpoint(self):
        """For a short day (8–16), solar noon should be at 12:00."""
        model = IrradianceModel(
            peak_irradiance_w_m2=1000.0,
            sunrise_hour=8.0,
            sunset_hour=16.0,
        )
        model.update(12 * 3600.0, 1.0)
        self.assertAlmostEqual(model.get_irradiance(), 1000.0, places=6)

    # --------------------------------------------------
    # Invalid config
    # --------------------------------------------------

    def test_invalid_sun_times(self):
        with self.assertRaises(ValueError):
            IrradianceModel(sunrise_hour=18.0, sunset_hour=6.0)

    def test_equal_sunrise_sunset_raises(self):
        """Zero-length day is degenerate — should raise."""
        with self.assertRaises(ValueError):
            IrradianceModel(sunrise_hour=12.0, sunset_hour=12.0)

    def test_negative_peak_irradiance_raises(self):
        """Negative peak irradiance is physically invalid."""
        with self.assertRaises(ValueError):
            IrradianceModel(peak_irradiance_w_m2=-100.0)


if __name__ == "__main__":
    unittest.main()