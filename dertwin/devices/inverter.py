from typing import Dict
from dertwin.core.device import SimulatedDevice


class InverterSimulator(SimulatedDevice):
    def __init__(self, rated_kw: float = 10.0, ambient_temp_c: float = 20.0):
        super().__init__()

        # Ratings
        self.rated_power_w = rated_kw * 1000.0
        self.efficiency = 0.97

        # External input (must be set by EMS / simulation driver)
        self.irradiance_factor = 0.0  # 0..1

        # Thermal model
        self.temperature_c = 30.0
        self.ambient_temp_c = ambient_temp_c
        self.thermal_mass = 20000.0
        self.cooling_coeff = 10.0

        # Energy counters
        self.today_energy_kwh = 0.0
        self.lifetime_energy_kwh = 0.0

        # Electrical state
        self.active_power_w = 0.0
        self.grid_voltage = 230.0
        self.grid_frequency = 50.0
        self.power_factor = 1.0

        self.fault_code = 0
        self.telemetry = {}

    # ---------------------------------------------------------
    # External control inputs
    # ---------------------------------------------------------

    def set_irradiance(self, factor: float):
        """Set normalized irradiance 0..1"""
        self.irradiance_factor = max(0.0, min(1.0, float(factor)))

    def set_grid_conditions(self, voltage: float, frequency: float):
        self.grid_voltage = float(voltage)
        self.grid_frequency = float(frequency)

    # ---------------------------------------------------------
    # Thermal model
    # ---------------------------------------------------------

    def update_temperature(self, power_w: float, dt: float):
        heat_fraction = 1.0 - self.efficiency
        heat_power = heat_fraction * abs(power_w)

        cooling_power = self.cooling_coeff * max(
            0.0, self.temperature_c - self.ambient_temp_c
        )

        delta_t = (heat_power - cooling_power) * dt / self.thermal_mass
        self.temperature_c += delta_t

        self.temperature_c = max(
            self.ambient_temp_c, min(80.0, self.temperature_c)
        )

    def update(self, dt: float) -> None:
        dt = float(dt)

        # PV input
        input_w = self.rated_power_w * self.irradiance_factor

        # AC output
        output_w = input_w * self.efficiency
        self.active_power_w = output_w

        # Energy integration
        dt_h = dt / 3600.0
        delta_kwh = (output_w / 1000.0) * dt_h

        self.today_energy_kwh += delta_kwh
        self.lifetime_energy_kwh += delta_kwh

        # Temperature
        self.update_temperature(output_w, dt)

        # Derived values
        current = (
            output_w / self.grid_voltage
            if self.grid_voltage > 0
            else 0.0
        )

        self.power_factor = 1.0 if output_w < 100 else 0.98

        status = 1 if output_w > 50 else 0

        self.telemetry = {
            "inverter_status": status,
            "total_input_power": input_w,
            "total_active_power": output_w,
            "grid_frequency": self.grid_frequency,
            "phase_neutral_voltage_1": self.grid_voltage,
            "phase_current_1": current,
            "phase_active_power_1": output_w,
            "today_output_energy": self.today_energy_kwh,
            "lifetime_output_energy": self.lifetime_energy_kwh,
            "temp_inverter": self.temperature_c,
            "power_factor": self.power_factor,
            "fault_code": self.fault_code,
        }

    def get_telemetry(self) -> Dict[str, float]:
        return self.telemetry

    # ---------------------------------------------------------
    # Command handler (placeholder for Modbus writes)
    # ---------------------------------------------------------

    def apply_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        applied = {}
        for k, v in commands.items():
            applied[k] = v
        return applied
