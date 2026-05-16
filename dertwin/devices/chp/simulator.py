from typing import Dict, Optional

from dertwin.core.device import SimulatedDevice
from dertwin.devices.chp.engine import EngineModel, StartupTimings, UnitState
from dertwin.devices.chp.chp import CHPModel
from dertwin.devices.chp.controller import CHPController
from dertwin.devices.external.ambient_temperature import AmbientTemperatureModel
from dertwin.devices.external.grid_frequency import GridFrequencyModel
from dertwin.devices.external.grid_voltage import GridVoltageModel
from dertwin.telemetry.chp import CHPTelemetry


class CHPSimulator(SimulatedDevice):
    """
    CHP simulator wrapper exposing SimulatedDevice interface.

    Composes:
        Engine → CHPModel → CHPController

    External model integration optional:
    - ambient_temp_model: drives engine ambient temperature
    - grid_frequency_model: provided for future faults (over/underfrequency)
    - grid_voltage_model: provided for future faults
    """

    def __init__(
        self,
        rated_kw: float = 4000.0,
        heat_to_power_ratio: float = 1.0,
        ramp_rate_percent_per_s: float = 5.0,
        min_load_percent: float = 30.0,
        max_load_percent: float = 110.0,
        ambient_temp_c: float = 20.0,
        startup_timings: Optional[StartupTimings] = None,
        ambient_temp_model: Optional[AmbientTemperatureModel] = None,
        grid_frequency_model: Optional[GridFrequencyModel] = None,
        grid_voltage_model: Optional[GridVoltageModel] = None,
    ):
        super().__init__()

        self.engine = EngineModel(
            ambient_temp_c=ambient_temp_c,
            timings=startup_timings,
        )

        self.chp = CHPModel(
            engine=self.engine,
            rated_kw=rated_kw,
            heat_to_power_ratio=heat_to_power_ratio,
            ramp_rate_percent_per_s=ramp_rate_percent_per_s,
            min_load_percent=min_load_percent,
            max_load_percent=max_load_percent,
        )

        self.controller = CHPController(self.chp)

        self._last_telemetry: CHPTelemetry = CHPTelemetry.zero()

        self.ambient_temp_model = ambient_temp_model
        self.grid_frequency_model = grid_frequency_model
        self.grid_voltage_model = grid_voltage_model

    # =========================================================
    # Compatibility Properties
    # =========================================================

    @property
    def rated_kw(self) -> float:
        return self.chp.rated_kw

    @property
    def state(self) -> UnitState:
        return self.engine.state

    @property
    def electrical_power_kw(self) -> float:
        return self.chp.electrical_power_kw

    @property
    def heat_power_kw(self) -> float:
        return self.chp.heat_power_kw

    @property
    def actual_power_percent(self) -> float:
        return self.chp.actual_power_percent

    @property
    def is_running(self) -> bool:
        return self.engine.is_running

    @property
    def operating_hours(self) -> float:
        return self.engine.operating_hours

    @property
    def start_counter(self) -> int:
        return self.engine.start_counter

    @property
    def fault_code(self) -> int:
        return self.engine.fault_code

    @fault_code.setter
    def fault_code(self, code: int):
        if code != 0:
            self.engine.raise_fault(code)
        else:
            self.engine.acknowledge_fault()

    # =========================================================
    # SimulatedDevice Interface
    # =========================================================

    def update(self, dt: float) -> None:
        if self.ambient_temp_model:
            self.engine.set_ambient_temperature(self.ambient_temp_model.get_temperature())

        self._last_telemetry = self.controller.step(dt)

    def get_telemetry(self) -> CHPTelemetry:
        return self._last_telemetry

    def apply_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        return self.controller.apply_commands(commands)

    def init_applied_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        return self.controller.init_applied_commands(commands)