import math


class AmbientTemperatureModel:
    """
    Deterministic daily ambient temperature model.

    Features:
    - 24h periodic cosine variation
    - Configurable mean temperature
    - Configurable amplitude
    - Configurable peak hour
    - Fully deterministic
    """

    def __init__(
        self,
        mean_temp_c: float = 20.0,
        amplitude_c: float = 5.0,
        peak_hour: float = 15.0,
    ):
        if amplitude_c < 0:
            raise ValueError("amplitude_c must be non-negative")

        if peak_hour < 0:
            raise ValueError("peak_hour must be non-negative")

        if peak_hour > 24:
            raise ValueError("peak_hour must be less than 24")

        self.mean = mean_temp_c
        self.amplitude = amplitude_c
        self.peak_hour = peak_hour % 24.0

        self._temperature = mean_temp_c

    # --------------------------------------------------

    def update(self, sim_time: float, dt: float):
        """
        sim_time: seconds since simulation start
        dt: unused (deterministic model)
        """

        hours = (sim_time / 3600.0) % 24.0

        phase = 2.0 * math.pi * (hours - self.peak_hour) / 24.0

        self._temperature = (
            self.mean
            + self.amplitude * math.cos(phase)
        )

    # --------------------------------------------------

    def get_temperature(self) -> float:
        return self._temperature