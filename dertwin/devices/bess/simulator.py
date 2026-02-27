from typing import Dict, Optional

from dertwin.core.device import SimulatedDevice
from dertwin.devices.bess.battery import BatteryModel
from dertwin.devices.bess.inverter import InverterModel
from dertwin.devices.bess.bess import BESSModel
from dertwin.devices.bess.controller import BESSController
from dertwin.devices.external.ambient_temperature import AmbientTemperatureModel
from dertwin.devices.external.grid_frequency import GridFrequencyModel
from dertwin.devices.external.grid_voltage import GridVoltageModel


class BESSSimulator(SimulatedDevice):
    """
    Compatibility wrapper around new architecture.

    Exposes the same public API as old monolithic simulator
    so legacy tests continue to pass.
    """

    def __init__(
            self,
            ramp_rate_kw_per_s: float = 100.0,
            ambient_temp_c: float = 20.0,
            ambient_temp_model: Optional[AmbientTemperatureModel] = None,
            grid_frequency_model: Optional[GridFrequencyModel] = None,
            grid_voltage_model: Optional[GridVoltageModel] = None,
    ):
        super().__init__()

        # --- Core models ---
        self.battery = BatteryModel(
            capacity_kwh=100,
            initial_soc=50,
            ambient_temp_c=ambient_temp_c,
        )

        self.inverter = InverterModel(
            max_charge_kw=20.0,
            max_discharge_kw=20.0,
            ramp_rate_kw_per_s=ramp_rate_kw_per_s,
        )

        self.bess = BESSModel(self.battery, self.inverter)
        self.controller = BESSController(self.bess)

        self._last_telemetry: Dict[str, float] = {}

        self.ambient_temp_model = ambient_temp_model
        self.grid_frequency_model = grid_frequency_model
        self.grid_voltage_model = grid_voltage_model

    # =========================================================
    # ---- Compatibility Properties (OLD API)
    # =========================================================

    @property
    def soc(self):
        return self.battery.soc

    @soc.setter
    def soc(self, value):
        self.battery.energy_kwh = (
            self.battery.capacity_kwh * float(value) / 100.0
        )

    @property
    def commanded_power_kw(self):
        return self.inverter.current_power

    @commanded_power_kw.setter
    def commanded_power_kw(self, value):
        self.inverter._current_power = float(value)

    @property
    def max_charge_kw(self):
        return self.inverter.max_charge_kw

    @max_charge_kw.setter
    def max_charge_kw(self, value):
        self.inverter.max_charge_kw = float(value)

    @property
    def max_discharge_kw(self):
        return self.inverter.max_discharge_kw

    @max_discharge_kw.setter
    def max_discharge_kw(self, value):
        self.inverter.max_discharge_kw = float(value)

    @property
    def ramp_rate_kw_per_s(self):
        return self.inverter.ramp_rate

    @property
    def mode(self):
        run_mode = self.controller.state.run_mode

        if run_mode == 1:
            return "run"

        if run_mode == 3:
            return "standby"

        if run_mode == 2:
            return "idle"

        return "idle"

    @property
    def local_remote_settings(self):
        return self.controller.state.local_remote_settings

    @property
    def power_control_mode(self):
        return self.controller.state.power_control_mode

    @property
    def fault_code(self):
        return self.controller.state.fault_code

    @fault_code.setter
    def fault_code(self, value):
        self.controller.state.fault_code = value

    @property
    def soc_upper_limit_1(self):
        return self.battery.limits.soc_upper_limit_1

    @property
    def soc_upper_limit_2(self):
        return self.battery.limits.soc_upper_limit_2

    @property
    def soc_lower_limit_1(self):
        return self.battery.limits.soc_lower_limit_1

    @property
    def soc_lower_limit_2(self):
        return self.battery.limits.soc_lower_limit_2

    # =========================================================
    # ---- Old Command API Compatibility
    # =========================================================

    def set_on_grid_power_kw(self, kw: float):
        self.controller.apply_command("active_power_setpoint", kw)

    def apply_commanded_power(self, dt: float):
        self.inverter.step(dt)

    def battery_voltage(self):
        power_kw = self.commanded_power_kw
        voc = self.battery.open_circuit_voltage()

        if voc == 0:
            return 0.0
        current = (power_kw * 1000.0) / voc
        terminal_v = voc - abs(current) * self.battery.internal_resistance

        return max(500.0, terminal_v)

    def service_current(self):
        voltage = self.battery_voltage()
        if voltage == 0:
            return 0.0
        return (self.commanded_power_kw * 1000.0) / voltage

    # =========================================================
    # ---- SimulatedDevice Interface
    # =========================================================

    def update(self, dt: float) -> None:
        # Ambient temperature
        if self.ambient_temp_model:
            ambient = self.ambient_temp_model.get_temperature()
            self.battery.set_ambient_temperature(ambient)

        # Grid frequency
        if self.grid_frequency_model:
            freq = self.grid_frequency_model.get_frequency()
            self.inverter.set_grid_frequency(freq)

        # Grid voltage
        if self.grid_voltage_model:
            voltage = self.grid_voltage_model.get_voltage_ll()
            self.inverter.set_grid_voltage(voltage)

        self._last_telemetry = self.controller.step(dt)

    def get_telemetry(self) -> Dict[str, float]:
        return self._last_telemetry

    def apply_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        for name, value in commands.items():
            self.controller.apply_command(name, value)
        return commands

    def init_applied_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        self.controller.init_applied_commands(commands)
        return commands