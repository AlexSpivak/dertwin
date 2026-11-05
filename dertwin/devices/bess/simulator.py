from pathlib import Path
from typing import Dict, Optional

from dertwin.core.device import SimulatedDevice
from dertwin.devices.bess.battery import (
    LIMIT_MODE_HARD,
    BatteryLimits,
    BatteryModel,
    BatteryThermalConfig,
)
from dertwin.devices.bess.inverter import InverterModel
from dertwin.devices.bess.bess import BESSModel
from dertwin.devices.bess.controller import BESSController
from dertwin.devices.bess.state import load_state, save_state
from dertwin.devices.external.ambient_temperature import AmbientTemperatureModel
from dertwin.devices.external.grid_frequency import GridFrequencyModel
from dertwin.devices.external.grid_voltage import GridVoltageModel
from dertwin.telemetry.bess import BESSTelemetry


class BESSSimulator(SimulatedDevice):
    """
    Compatibility wrapper around new architecture.

    Exposes the same public API as old monolithic simulator
    so legacy tests continue to pass.
    """

    def __init__(
            self,
            capacity_kwh: float = 100.0,
            initial_soc: float = 50.0,
            max_charge_kw: float = 20.0,
            max_discharge_kw: float = 20.0,
            ramp_rate_kw_per_s: float = 100.0,
            ambient_temp_c: float = 20.0,
            round_trip_eff: float = 0.92,
            internal_resistance: Optional[float] = None,
            thermal_capacity_j_per_k: Optional[float] = None,
            thermal_conductance_w_per_k: Optional[float] = None,
            limits: Optional[BatteryLimits] = None,
            ambient_temp_model: Optional[AmbientTemperatureModel] = None,
            grid_frequency_model: Optional[GridFrequencyModel] = None,
            grid_voltage_model: Optional[GridVoltageModel] = None,
            limit_mode: str = LIMIT_MODE_HARD,
            soc_min_percent: Optional[float] = None,
            soc_max_percent: Optional[float] = None,
            charge_efficiency: Optional[float] = None,
            discharge_efficiency: Optional[float] = None,
            self_discharge_percent_per_day: float = 0.0,
            thermal: Optional[BatteryThermalConfig] = None,
            state_file: Optional[str] = None,
            state_save_interval_s: float = 60.0,
            asset_id: str = "bess",
    ):
        super().__init__()

        self.asset_id = asset_id

        self.battery = BatteryModel(
            capacity_kwh=capacity_kwh,
            initial_soc=initial_soc,
            max_charge_kw=max_charge_kw,
            max_discharge_kw=max_discharge_kw,
            ambient_temp_c=ambient_temp_c,
            round_trip_eff=round_trip_eff,
            internal_resistance=internal_resistance,
            thermal_capacity_j_per_k=thermal_capacity_j_per_k,
            thermal_conductance_w_per_k=thermal_conductance_w_per_k,
            limits=limits,
            limit_mode=limit_mode,
            soc_min_percent=soc_min_percent,
            soc_max_percent=soc_max_percent,
            charge_efficiency=charge_efficiency,
            discharge_efficiency=discharge_efficiency,
            self_discharge_percent_per_day=self_discharge_percent_per_day,
            thermal=thermal,
        )

        # Optional SOC persistence. Default from config only on first boot.
        self.state_path: Optional[Path] = Path(state_file) if state_file else None
        self.state_save_interval_s = state_save_interval_s
        self._since_state_save_s = 0.0

        if self.state_path is not None:
            state = load_state(self.state_path, asset_id=self.asset_id)
            if state is not None:
                self.battery.restore_state(state)

        self.inverter = InverterModel(
            max_charge_kw=max_charge_kw,
            max_discharge_kw=max_discharge_kw,
            ramp_rate_kw_per_s=ramp_rate_kw_per_s,
        )

        self.bess = BESSModel(self.battery, self.inverter)
        self.controller = BESSController(self.bess)

        self._last_telemetry: BESSTelemetry = BESSTelemetry.zero()

        self.ambient_temp_model = ambient_temp_model
        self.grid_frequency_model = grid_frequency_model
        self.grid_voltage_model = grid_voltage_model

    # =========================================================
    # ---- Compatibility Properties
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
        self.battery.max_charge_kw = float(value)

    @property
    def max_discharge_kw(self):
        return self.inverter.max_discharge_kw

    @max_discharge_kw.setter
    def max_discharge_kw(self, value):
        self.inverter.max_discharge_kw = float(value)
        self.battery.max_discharge_kw = float(value)

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
        if self.ambient_temp_model:
            ambient = self.ambient_temp_model.get_temperature()
            self.battery.set_ambient_temperature(ambient)

        if self.grid_frequency_model:
            freq = self.grid_frequency_model.get_frequency()
            self.inverter.set_grid_frequency(freq)

        if self.grid_voltage_model:
            voltage = self.grid_voltage_model.get_voltage_ll()
            self.inverter.set_grid_voltage(voltage)

        self._last_telemetry: BESSTelemetry = self.controller.step(dt)

        self._maybe_save_state(dt)

    # =========================================================
    # ---- SOC Persistence
    # =========================================================

    def _maybe_save_state(self, dt: float) -> None:
        if self.state_path is None:
            return

        self._since_state_save_s += dt

        if self._since_state_save_s >= self.state_save_interval_s:
            self._since_state_save_s = 0.0
            self.save_state()

    def save_state(self) -> bool:
        """Persist SOC now. No-op when persistence is disabled."""
        if self.state_path is None:
            return False

        return save_state(
            self.state_path,
            self.battery.state_snapshot(),
            asset_id=self.asset_id,
        )

    def get_telemetry(self) -> BESSTelemetry:
        return self._last_telemetry

    def apply_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        for name, value in commands.items():
            self.controller.apply_command(name, value)
        return commands

    def init_applied_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        self.controller.init_applied_commands(commands)
        return commands