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

    # --------------------------------------------------
    # Day behavior
    # --------------------------------------------------

    def test_midday_peak(self):
        # Midpoint between 6 and 18 = 12
        self.update_at_hour(12.0)
        self.assertAlmostEqual(
            self.model.get_irradiance(),
            1000.0,
            places=6,
        )

    def test_morning_value(self):
        # 9 AM (quarter of daylight)
        self.update_at_hour(9.0)

        expected = 1000.0 * math.sin(math.pi * 0.25)
        self.assertAlmostEqual(
            self.model.get_irradiance(),
            expected,
            places=6,
        )

    # --------------------------------------------------
    # Periodicity
    # --------------------------------------------------

    def test_daily_periodicity(self):
        # Noon day 1
        self.model.update(12 * 3600, 1.0)
        day1 = self.model.get_irradiance()

        # Noon day 2
        self.model.update((12 + 24) * 3600, 1.0)
        day2 = self.model.get_irradiance()

        self.assertAlmostEqual(day1, day2, places=6)

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

            self.assertAlmostEqual(
                m1.get_irradiance(),
                m2.get_irradiance(),
                places=6,
            )

    # --------------------------------------------------
    # Custom sunrise/sunset
    # --------------------------------------------------

    def test_custom_sun_times(self):
        model = IrradianceModel(
            peak_irradiance_w_m2=800.0,
            sunrise_hour=8.0,
            sunset_hour=16.0,
        )

        # Midpoint 12:00
        model.update(12 * 3600.0, 1.0)

        self.assertAlmostEqual(
            model.get_irradiance(),
            800.0,
            places=6,
        )

    # --------------------------------------------------
    # Invalid config
    # --------------------------------------------------

    def test_invalid_sun_times(self):
        with self.assertRaises(ValueError):
            IrradianceModel(
                sunrise_hour=18.0,
                sunset_hour=6.0,
            )


if __name__ == "__main__":
    unittest.main()