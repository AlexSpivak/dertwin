import math
import random
from datetime import datetime
from typing import Dict, Optional

from dertwin.devices.device import DeviceSimulator


# -------------------------
# PV Simulator
# -------------------------
class InverterSimulator(DeviceSimulator):
    def __init__(self, rated_kw: float = 10.0, interval: float = 0.1):
        super().__init__()

        # Power ratings
        self.rated_power_w = rated_kw * 1000.0

        # Temperature model
        self.inverter_temp_c = 30.0
        self.thermal_mass = 20000.0  # J/K (tunable)
        self.cooling_coeff = 10.0  # W/K (tunable)
        self.heat_fraction = 0.01  # 1% of electrical power -> heat
        self.ambient_temp = 20.0
        self.efficiency = 0.97  # optional if you prefer (heat_fraction = 1-eff)

        # Internal storage of last simulation
        self.total_active_power_latest: float = 0.0
        self.today_energy = 0.0
        self.cumulative_energy = 0.0
        self.dt = interval

    # ---------------------------------------------------------
    def get_solar_factor(self, now=None) -> float:
        if now is None:
            now = datetime.now()
        hour = now.hour + now.minute / 60
        sunrise = 6.0
        sunset = 21.0
        if hour < sunrise or hour > sunset:
            return 0.0
        x = (hour - sunrise) / (sunset - sunrise) * math.pi
        base = math.sin(x)
        variability = random.uniform(0.95, 1.05)
        return max(0.0, base * variability)

    def update_temperature(self, power_w: float, dt: float = 2.0, ambient: float = None):
        if ambient is None:
            ambient = self.ambient_temp

        # fraction of electrical power converted to heat (tunable)
        heat_fraction = self.heat_fraction

        # prefer using inverter efficiency if available:
        eff = self.efficiency
        heat_fraction = 1.0 - eff  # e.g. 0.03 if eff=0.97
        # Joule/heat power (W)
        heat_power = heat_fraction * abs(power_w)

        # Cooling power (W) proportional to temperature difference
        cooling_power = self.cooling_coeff * max(0.0, self.inverter_temp_c - ambient)

        # Temperature change (°C): dT = (heat_power - cooling_power) * dt / C
        delta_t = (heat_power - cooling_power) * dt / self.thermal_mass
        self.inverter_temp_c += delta_t

        # Prevent unrealistic values; don't go below ambient
        self.inverter_temp_c = max(ambient, min(80.0, self.inverter_temp_c))
        return self.inverter_temp_c

    # ---------------------------------------------------------
    def simulate_values(self, dt: Optional[float]) -> Dict[str, float]:
        self.reset_daily_counters()

        # Solar irradiance factor 0–1
        factor = self.get_solar_factor()

        # Power generation (W)
        total_input_w = self.rated_power_w * factor
        efficiency = 0.97
        output_w = total_input_w * efficiency

        # Grid parameters
        grid_voltage = 230 + random.uniform(-1.5, 1.5)
        grid_frequency = 50 + random.uniform(-0.03, 0.03)

        power_factor = 1.0 if output_w < 100 else 0.98 + random.uniform(-0.005, 0.005)

        # Temperature update
        self.inverter_temp_c = self.update_temperature(output_w, self.dt)

        # Energy update
        energy_increment_kwh = (output_w / 1000.0) * (self.dt / 3600.0)
        self.today_energy += energy_increment_kwh
        self.cumulative_energy += energy_increment_kwh

        # Inverter status
        status = 1 if output_w > 50 else 0

        self.total_active_power_latest = output_w

        # Store last simulation values
        return {
            'inverter_status': status,
            'total_input_power': total_input_w,
            'total_active_power': output_w,
            'grid_frequency': grid_frequency,
            'phase_neutral_voltage_1': grid_voltage,
            'phase_current_1': output_w / grid_voltage if grid_voltage > 0 else 0,
            'phase_active_power_1': output_w,
            'today_output_energy': self.today_energy,
            'lifetime_output_energy': self.cumulative_energy,
            'temp_inverter': self.inverter_temp_c,
            'power_factor': power_factor,
            'fault_code': 0,
        }
    def get_pv_watts(self) -> float:
        """Return last simulated PV output (W) without re-simulating."""
        return self.total_active_power_latest

    def execute_write_instructions(self, instructions: dict) -> dict:
        """Dummy executor: simply returns instructions as applied (no effect)."""
        return {k: v for k, v in instructions.items()}

    def init_applied_commands(self, commands: Dict[str, float]):
        # we can implement it later
        pass