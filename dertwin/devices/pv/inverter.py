import math


class PVInverterModel:
    """
    AC-side inverter model for PV.

    Features:
    - AC rating clamp
    - DC/AC clipping
    - Curtailment (active power rate)
    - Reactive power control
    - Thermal model
    - Grid protection
    """

    def __init__(
        self,
        rated_ac_power_w: float,
        efficiency: float = 0.97,
        ambient_temp_c: float = 25.0,
    ):
        self.rated_ac_power_w = rated_ac_power_w
        self.efficiency = efficiency

        self.active_power_rate = 100.0  # %
        self.reactive_power_rate = 0.0  # %
        self.power_factor_setpoint = 1.0

        self.grid_voltage = 230.0
        self.grid_frequency = 50.0

        self.temperature_c = 30.0
        self.ambient_temp_c = ambient_temp_c
        self.thermal_mass = 20000.0
        self.cooling_coeff = 10.0

        self.fault_code = 0

        self.active_power_w = 0.0
        self.reactive_power_var = 0.0

    # -------------------------------------------------
    # Grid protection
    # -------------------------------------------------

    def grid_ok(self) -> bool:
        if self.grid_voltage < 180 or self.grid_voltage > 260:
            self.fault_code = 1
            return False
        if self.grid_frequency < 47 or self.grid_frequency > 53:
            self.fault_code = 2
            return False
        self.fault_code = 0
        return True

    # -------------------------------------------------
    # Thermal model
    # -------------------------------------------------

    def update_temperature(self, power_w: float, dt: float):
        heat = (1.0 - self.efficiency) * abs(power_w)
        cooling = self.cooling_coeff * max(
            0.0, self.temperature_c - self.ambient_temp_c
        )

        delta = (heat - cooling) * dt / self.thermal_mass
        self.temperature_c += delta
        self.temperature_c = max(
            self.ambient_temp_c, min(85.0, self.temperature_c)
        )

    # -------------------------------------------------
    # Main step
    # -------------------------------------------------

    def step(self, dc_input_w: float, dt: float):

        if not self.grid_ok():
            self.active_power_w = 0.0
            self.reactive_power_var = 0.0
            return

        # DC → AC conversion
        ac_available = dc_input_w * self.efficiency

        # AC rating limit
        ac_available = min(ac_available, self.rated_ac_power_w)

        # Curtailment
        ac_limit = self.rated_ac_power_w * (self.active_power_rate / 100.0)
        self.active_power_w = min(ac_available, ac_limit)

        # Reactive power
        if self.power_factor_setpoint != 0:
            angle = math.acos(max(-1.0, min(1.0, self.power_factor_setpoint)))
            self.reactive_power_var = self.active_power_w * math.tan(angle)
        else:
            self.reactive_power_var = 0.0

        self.update_temperature(self.active_power_w, dt)

    # -------------------------------------------------

    def apparent_power(self):
        return math.hypot(self.active_power_w, self.reactive_power_var)