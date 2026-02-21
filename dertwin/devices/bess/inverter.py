class InverterModel:
    """
    Deterministic inverter model.
    Handles ramp rate and power limits.
    """

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

    # ---------------------------------------------------------

    @property
    def current_power(self) -> float:
        return self._current_power

    def set_target_power(self, power_kw: float):
        self._target_power = power_kw

    # ---------------------------------------------------------

    def step(self, dt_seconds: float) -> float:

        # Clamp target to physical limits
        target = max(
            -self.max_discharge_kw,
            min(self.max_charge_kw, self._target_power),
        )

        # Apply ramp rate
        delta = target - self._current_power
        max_delta = self.ramp_rate * dt_seconds

        if abs(delta) > max_delta:
            delta = max_delta if delta > 0 else -max_delta

        self._current_power += delta

        return self._current_power