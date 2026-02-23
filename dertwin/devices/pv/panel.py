class PVArrayModel:
    """
    DC-side photovoltaic array model.

    Deterministic physics:
    - Irradiance-based production
    - Temperature derating
    - Simple thermal cell model
    """

    def __init__(
        self,
        area_m2: float,
        module_efficiency: float = 0.20,
        temp_coefficient: float = -0.004,  # per °C
        noct_c: float = 45.0,  # nominal operating cell temp
        ambient_temp_c: float = 25.0,
    ):
        self.area_m2 = area_m2
        self.module_efficiency = module_efficiency
        self.temp_coefficient = temp_coefficient
        self.noct_c = noct_c

        self.irradiance_w_per_m2 = 0.0
        self.ambient_temp_c = ambient_temp_c
        self.cell_temperature_c = ambient_temp_c

    # -------------------------------------------------
    # External inputs
    # -------------------------------------------------

    def set_irradiance(self, irradiance_w_per_m2: float):
        self.irradiance_w_per_m2 = max(0.0, irradiance_w_per_m2)

    def set_ambient_temperature(self, temp_c: float):
        self.ambient_temp_c = temp_c

    # -------------------------------------------------
    # Thermal model (NOCT approximation)
    # -------------------------------------------------

    def update_cell_temperature(self):
        # Simplified NOCT-based temperature model
        delta = (self.noct_c - 20.0) / 800.0 * self.irradiance_w_per_m2
        self.cell_temperature_c = self.ambient_temp_c + delta

    # -------------------------------------------------
    # DC Power Output
    # -------------------------------------------------

    def dc_power_w(self) -> float:
        self.update_cell_temperature()

        if self.irradiance_w_per_m2 <= 0:
            return 0.0

        # Temperature correction
        temp_factor = 1.0 + self.temp_coefficient * (
            self.cell_temperature_c - 25.0
        )

        raw_power = (
            self.irradiance_w_per_m2
            * self.area_m2
            * self.module_efficiency
            * temp_factor
        )

        return max(0.0, raw_power)