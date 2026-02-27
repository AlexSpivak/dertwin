from typing import Optional

from dertwin.devices.external.power_flow import SitePowerModel
from dertwin.devices.external.grid_frequency import GridFrequencyModel
from dertwin.devices.external.grid_voltage import GridVoltageModel


class ExternalModels:
    """
    Aggregates and advances all external world models.

    This provides a single deterministic update point for:

        - Power flow (site import/export)
        - Grid frequency
        - Grid voltage
        - Irradiance
        - Ambient temperature

    SimulationEngine calls update() exactly once per tick BEFORE devices step.

    This guarantees deterministic causality:
        world → devices → telemetry
    """

    def __init__(
        self,
        power_model: Optional[SitePowerModel] = None,
        grid_frequency_model: Optional[GridFrequencyModel] = None,
        grid_voltage_model: Optional[GridVoltageModel] = None,
        ambient_temperature_model=None,
        irradiance_model=None,
    ):
        self.power_model = power_model
        self.grid_frequency_model = grid_frequency_model
        self.grid_voltage_model = grid_voltage_model
        self.ambient_temperature_model = ambient_temperature_model
        self.irradiance_model = irradiance_model

    # ---------------------------------------------------------
    # STEP ALL EXTERNAL MODELS
    # ---------------------------------------------------------

    def update(self, sim_time: float, dt: float) -> None:
        """
        Advance all external world models.

        Called once per simulation tick BEFORE devices update.
        """

        # Power balance must run first so meter sees correct values
        if self.power_model:
            self.power_model.update(dt)

        # Grid electrical state
        if self.grid_frequency_model:
            self.grid_frequency_model.update(sim_time, dt)

        if self.grid_voltage_model:
            self.grid_voltage_model.update(sim_time, dt)

        # Future extensions
        if self.ambient_temperature_model:
            self.ambient_temperature_model.update(sim_time, dt)

        if self.irradiance_model:
            self.irradiance_model.update(sim_time, dt)