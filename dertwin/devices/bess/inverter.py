import math


class InverterModel:
    def __init__(
        self,
        max_charge_kw: float,
        max_discharge_kw: float,
        ramp_rate_kw_per_s: float,
    ):
        self.max_charge_kw = max_charge_kw
        self.max_discharge_kw = max_discharge_kw
        self.ramp_rate = ramp_rate_kw_per_s

        self._target_power = 0.0
        self._current_power = 0.0

        self.grid_frequency = 50.0
        self.grid_voltage_ll = 400.0

    @property
    def current_power(self) -> float:
        return self._current_power

    # -------------------------------------------------
    # Target Power (ADD SETTER HERE)
    # -------------------------------------------------

    @property
    def target_power(self) -> float:
        return self._target_power

    @target_power.setter
    def target_power(self, power_kw: float):
        power_kw = float(power_kw)
        power_kw = max(-self.max_charge_kw, min(self.max_discharge_kw, power_kw))
        self._target_power = power_kw

    # Keep old API for compatibility
    def set_target_power(self, power_kw: float):
        self.target_power = power_kw

    def step(self, dt: float) -> float:
        delta = self._target_power - self._current_power
        max_delta = self.ramp_rate * dt

        if abs(delta) > max_delta:
            delta = max_delta if delta > 0 else -max_delta

        self._current_power += delta
        return self._current_power

    # AC side metrics
    def reactive_power(self) -> float:
        return 0.1 * self._current_power

    def apparent_power(self) -> float:
        return math.hypot(self._current_power, self.reactive_power())

    def set_grid_frequency(self, hz: float):
        self.grid_frequency = float(hz)

    def set_grid_voltage(self, voltage: float):
        self.grid_voltage_ll = float(voltage)