import unittest

from dertwin.devices.external.ambient_temperature import (
    AmbientTemperatureModel,
)


class TestAmbientTemperatureModel(unittest.TestCase):

    def setUp(self):
        self.model = AmbientTemperatureModel(
            mean_temp_c=20.0,
            amplitude_c=5.0,
            peak_hour=15.0,
        )

    # --------------------------------------------------
    # Helper
    # --------------------------------------------------

    def update_at_hour(self, hour: float):
        sim_time = hour * 3600.0
        self.model.update(sim_time, dt=1.0)

    # --------------------------------------------------
    # Peak and minimum behavior
    # --------------------------------------------------

    def test_peak_temperature(self):
        self.update_at_hour(15.0)
        self.assertAlmostEqual(self.model.get_temperature(), 25.0, places=6)

    def test_minimum_temperature(self):
        self.update_at_hour((15.0 + 12.0) % 24.0)
        self.assertAlmostEqual(self.model.get_temperature(), 15.0, places=6)

    def test_mean_temperature_at_quarter_cycle(self):
        """6 hours before peak the sine is at zero → temperature equals mean."""
        self.update_at_hour((15.0 - 6.0) % 24.0)
        self.assertAlmostEqual(self.model.get_temperature(), 20.0, places=6)

    def test_temperature_never_exceeds_peak(self):
        """Temperature must never exceed mean + amplitude."""
        for h in range(0, 24):
            self.update_at_hour(float(h))
            self.assertLessEqual(self.model.get_temperature(), 25.0 + 1e-9)

    def test_temperature_never_below_minimum(self):
        """Temperature must never fall below mean - amplitude."""
        for h in range(0, 24):
            self.update_at_hour(float(h))
            self.assertGreaterEqual(self.model.get_temperature(), 15.0 - 1e-9)

    def test_symmetry_around_peak(self):
        """Hours equidistant from peak must have identical temperature."""
        self.update_at_hour(13.0)  # 2 hours before peak
        before = self.model.get_temperature()

        self.update_at_hour(17.0)  # 2 hours after peak
        after = self.model.get_temperature()

        self.assertAlmostEqual(before, after, places=6)

    def test_temperature_increases_toward_peak(self):
        self.update_at_hour(9.0)
        morning = self.model.get_temperature()

        self.update_at_hour(12.0)
        midday = self.model.get_temperature()

        self.update_at_hour(15.0)
        peak = self.model.get_temperature()

        self.assertLess(morning, midday)
        self.assertLess(midday, peak)

    # --------------------------------------------------
    # Periodicity
    # --------------------------------------------------

    def test_daily_periodicity(self):
        self.model.update(15 * 3600.0, 1.0)
        day1 = self.model.get_temperature()

        self.model.update((15 + 24) * 3600.0, 1.0)
        day2 = self.model.get_temperature()

        self.assertAlmostEqual(day1, day2, places=6)

    def test_multi_day_periodicity(self):
        """Pattern must repeat identically over many days."""
        hours = [3.0, 9.0, 15.0, 21.0]
        for hour in hours:
            self.model.update(hour * 3600.0, 1.0)
            day1 = self.model.get_temperature()

            self.model.update((hour + 72.0) * 3600.0, 1.0)  # 3 days later
            day3 = self.model.get_temperature()

            self.assertAlmostEqual(day1, day3, places=6,
                                   msg=f"Periodicity failed at hour {hour}")

    # --------------------------------------------------
    # Determinism
    # --------------------------------------------------

    def test_deterministic(self):
        m1 = AmbientTemperatureModel()
        m2 = AmbientTemperatureModel()

        for hour in range(0, 48):
            sim_time = hour * 3600.0
            m1.update(sim_time, 1.0)
            m2.update(sim_time, 1.0)
            self.assertAlmostEqual(m1.get_temperature(), m2.get_temperature(), places=6)

    # --------------------------------------------------
    # Zero amplitude
    # --------------------------------------------------

    def test_zero_amplitude(self):
        model = AmbientTemperatureModel(
            mean_temp_c=22.0, amplitude_c=0.0, peak_hour=12.0,
        )
        for hour in range(0, 24):
            model.update(hour * 3600.0, 1.0)
            self.assertAlmostEqual(model.get_temperature(), 22.0, places=6)

    # --------------------------------------------------
    # Custom configuration
    # --------------------------------------------------

    def test_custom_configuration(self):
        model = AmbientTemperatureModel(
            mean_temp_c=10.0, amplitude_c=8.0, peak_hour=14.0,
        )
        model.update(14 * 3600.0, 1.0)
        self.assertAlmostEqual(model.get_temperature(), 18.0, places=6)

        model.update((14 + 12) * 3600.0, 1.0)
        self.assertAlmostEqual(model.get_temperature(), 2.0, places=6)

    def test_peak_hour_at_midnight(self):
        """Peak at hour 0 (midnight) is a valid edge case."""
        model = AmbientTemperatureModel(
            mean_temp_c=0.0, amplitude_c=10.0, peak_hour=0.0,
        )
        model.update(0.0, 1.0)
        self.assertAlmostEqual(model.get_temperature(), 10.0, places=6)

        model.update(12 * 3600.0, 1.0)
        self.assertAlmostEqual(model.get_temperature(), -10.0, places=6)

    # --------------------------------------------------
    # Invalid configuration
    # --------------------------------------------------

    def test_negative_amplitude_raises(self):
        with self.assertRaises(ValueError):
            AmbientTemperatureModel(amplitude_c=-5.0)

    def test_invalid_peak_hour_raises(self):
        """Peak hour must be in [0, 24)."""
        with self.assertRaises(ValueError):
            AmbientTemperatureModel(peak_hour=25.0)

    def test_invalid_negative_peak_hour_raises(self):
        with self.assertRaises(ValueError):
            AmbientTemperatureModel(peak_hour=-1.0)


if __name__ == "__main__":
    unittest.main()