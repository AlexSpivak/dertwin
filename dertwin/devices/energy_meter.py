import math
import random
from datetime import datetime
from typing import Callable, Dict

from dertwin.devices.device import DeviceSimulator
from dertwin.devices.grid_frequency import GridFrequencyModel


# -------------------------
# Energy Meter Simulator
# -------------------------
class EnergyMeterSimulator(DeviceSimulator):
    def __init__(
        self,
        pv_supplier: Callable[[], float] = None,
        bess_supplier: Callable[[], float] = None,
        grid_frequency_model: GridFrequencyModel = None
    ):
        super().__init__()
        self.import_energy_total = 0.0
        self.export_energy_total = 0.0
        self.pv_supplier = pv_supplier
        self.bess_supplier = bess_supplier

        self.grid_frequency_model = grid_frequency_model or GridFrequencyModel()
        self._last_pf = 0.99

    # ---------------------------------------------------------
    # Smooth base load
    # ---------------------------------------------------------
    def base_load_kw(self) -> float:
        """
        Smooth diurnal load profile using sine interpolation.
        Peak in morning and evening, low during day/night.
        """
        now = datetime.now()
        hour = now.hour + now.minute / 60.0

        # Morning peak (6–9)
        morning_peak = 3.0 + math.sin((hour - 6) / 3 * math.pi) if 6 <= hour <= 9 else 0.0
        # Daytime low (9–17)
        day_low = 1.5 + 0.5 * math.sin((hour - 9) / 8 * math.pi) if 9 <= hour <= 17 else 0.0
        # Evening peak (17–23)
        evening_peak = 4.5 + math.sin((hour - 17) / 6 * math.pi) if 17 <= hour <= 23 else 0.0
        # Night base load
        night = 1.0 if hour < 6 or hour > 23 else 0.0

        load = morning_peak + day_low + evening_peak + night

        # Slight random variation
        load *= random.uniform(0.95, 1.05)
        return load

    # ---------------------------------------------------------
    # Main simulation
    # ---------------------------------------------------------
    def simulate_values(self, dt: float = 2.0) -> Dict[str, float]:
        dt_h = dt / 3600.0  # convert to hours

        # Base load
        base_load = self.base_load_kw()

        # PV / BESS input
        pv_kw = (self.pv_supplier() / 1000.0) if self.pv_supplier else 0.0
        bess_kw = (self.bess_supplier() / 1000.0) if self.bess_supplier else 0.0

        # Grid power: positive = import, negative = export
        grid_kw = base_load - pv_kw - bess_kw

        # Energy accumulation
        energy_delta = grid_kw * dt_h
        if grid_kw > 0:
            self.import_energy_total += energy_delta
        else:
            self.export_energy_total += -energy_delta

        # Power factor: small random walk for smoothness
        pf_target = random.uniform(0.95, 1.0)
        self._last_pf += (pf_target - self._last_pf) * 0.05  # smoothing factor
        pf = self._last_pf

        # Reactive power
        reactive_power = grid_kw * math.tan(math.acos(pf))

        # Grid voltage (slight random variation)
        grid_voltage = 230 + random.uniform(-2.0, 2.0)

        # Balanced per-phase power
        phase_power_kw = grid_kw / 3.0
        phase_current_a = (abs(phase_power_kw) * 1000 / grid_voltage) if grid_voltage > 0 else 0.0
        # Random small phase imbalance ±3%
        imbalance_factor = random.uniform(0.97, 1.03)
        phase_current_a *= 1 if phase_power_kw >= 0 else -1
        phase_current_b = phase_current_a * imbalance_factor
        phase_current_c = phase_current_a / imbalance_factor
        grid_freq = self.grid_frequency_model.get_frequency()

        return {
            'total_active_power': grid_kw,
            'total_reactive_power': reactive_power,
            'total_power_factor': pf,
            'grid_frequency': grid_freq,
            'phase_voltage_a': grid_voltage + random.uniform(-1, 1),
            'phase_current_a': phase_current_a,
            'phase_active_power_a': phase_power_kw,
            'phase_voltage_b': grid_voltage + random.uniform(-1, 1),
            'phase_current_b': phase_current_b,
            'phase_active_power_b': phase_power_kw,
            'phase_voltage_c': grid_voltage + random.uniform(-1, 1),
            'phase_current_c': phase_current_c,
            'phase_active_power_c': phase_power_kw,
            'total_import_energy': self.import_energy_total,
            'total_export_energy': self.export_energy_total,
        }

    def execute_write_instructions(self, instructions: dict) -> dict:
        """Dummy executor: simply returns instructions as applied (no effect)."""
        return {k: v for k, v in instructions.items()}

    def init_applied_commands(self, commands: Dict[str, float]):
        # we can implement it later
        pass