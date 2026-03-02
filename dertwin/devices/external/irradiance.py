import math


class IrradianceModel:
    """
    Deterministic clear-sky irradiance model.

    Features:
    - Daily sinusoidal irradiance curve
    - Configurable peak irradiance
    - Configurable sunrise/sunset
    - Fully deterministic
    - 24h periodic
    """

    def __init__(
        self,
        peak_irradiance_w_m2: float = 1000.0,
        sunrise_hour: float = 6.0,
        sunset_hour: float = 18.0,
    ):
        if sunset_hour <= sunrise_hour:
            raise ValueError("sunset_hour must be greater than sunrise_hour")

        self.peak = peak_irradiance_w_m2
        self.sunrise = sunrise_hour
        self.sunset = sunset_hour

        self._irradiance = 0.0

    # --------------------------------------------------

    def update(self, sim_time: float, dt: float):
        """
        sim_time: seconds since simulation start
        dt: timestep (unused, deterministic model)
        """

        hours = (sim_time / 3600.0) % 24.0

        if hours <= self.sunrise or hours >= self.sunset:
            self._irradiance = 0.0
            return

        daylight_duration = self.sunset - self.sunrise
        normalized_time = (hours - self.sunrise) / daylight_duration

        self._irradiance = (
            self.peak * math.sin(math.pi * normalized_time)
        )

    # --------------------------------------------------

    def get_irradiance(self) -> float:
        return max(self._irradiance, 0.0)

    def set_irradiance(self, irradiance: float):
        self._irradiance = irradiance