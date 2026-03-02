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
    # Peak behavior
    # --------------------------------------------------

    def test_peak_temperature(self):
        self.update_at_hour(15.0)
        self.assertAlmostEqual(
            self.model.get_temperature(),
            25.0,
            places=6,
        )

    def test_minimum_temperature(self):
        # 12 hours after peak
        self.update_at_hour((15.0 + 12.0) % 24.0)
        self.assertAlmostEqual(
            self.model.get_temperature(),
            15.0,
            places=6,
        )

    # --------------------------------------------------
    # Periodicity
    # --------------------------------------------------

    def test_daily_periodicity(self):
        # Peak day 1
        self.model.update(15 * 3600.0, 1.0)
        day1 = self.model.get_temperature()

        # Peak day 2
        self.model.update((15 + 24) * 3600.0, 1.0)
        day2 = self.model.get_temperature()

        self.assertAlmostEqual(day1, day2, places=6)

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

            self.assertAlmostEqual(
                m1.get_temperature(),
                m2.get_temperature(),
                places=6,
            )

    # --------------------------------------------------
    # Zero amplitude
    # --------------------------------------------------

    def test_zero_amplitude(self):
        model = AmbientTemperatureModel(
            mean_temp_c=22.0,
            amplitude_c=0.0,
            peak_hour=12.0,
        )

        for hour in range(0, 24):
            model.update(hour * 3600.0, 1.0)
            self.assertAlmostEqual(
                model.get_temperature(),
                22.0,
                places=6,
            )

    # --------------------------------------------------
    # Custom configuration
    # --------------------------------------------------

    def test_custom_configuration(self):
        model = AmbientTemperatureModel(
            mean_temp_c=10.0,
            amplitude_c=8.0,
            peak_hour=14.0,
        )

        # Peak
        model.update(14 * 3600.0, 1.0)
        self.assertAlmostEqual(
            model.get_temperature(),
            18.0,
            places=6,
        )

        # Minimum
        model.update((14 + 12) * 3600.0, 1.0)
        self.assertAlmostEqual(
            model.get_temperature(),
            2.0,
            places=6,
        )

    # --------------------------------------------------
    # Invalid configuration
    # --------------------------------------------------

    def test_negative_amplitude_raises(self):
        with self.assertRaises(ValueError):
            AmbientTemperatureModel(amplitude_c=-5.0)


if __name__ == "__main__":
    unittest.main()