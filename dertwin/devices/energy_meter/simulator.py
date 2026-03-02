from typing import Dict

from dertwin.core.device import SimulatedDevice
from dertwin.devices.energy_meter.model import EnergyMeterModel
from dertwin.devices.external.grid_voltage import GridVoltageModel
from dertwin.devices.external.power_flow import SitePowerModel
from dertwin.devices.external.grid_frequency import GridFrequencyModel
from dertwin.telemetry.energy_meter import EnergyMeterTelemetry


class EnergyMeterSimulator(SimulatedDevice):
    """
    Passive PCC energy meter simulator.

    Observes:
        - SitePowerModel (power balance)
        - GridFrequencyModel (frequency)
        - GridVoltageModel (voltage)
    """

    def __init__(
        self,
        power_model: SitePowerModel,
        grid_model: GridFrequencyModel,
        grid_voltage_model: GridVoltageModel,
        seed: int | None = None,
    ):
        self.power_model = power_model
        self.grid_model = grid_model
        self.grid_voltage_model = grid_voltage_model

        self.model = EnergyMeterModel(seed=seed)

        self._last_telemetry: EnergyMeterTelemetry = EnergyMeterTelemetry.zero()

    # --------------------------------------------------
    # Simulation Step
    # --------------------------------------------------
    def update(self, dt: float) -> None:

        frequency = self.grid_model.get_frequency()

        voltage_ll = self.grid_voltage_model.get_voltage_ll()

        self._last_telemetry = self.model.measure(
            grid_power_kw=self.power_model.grid_power_kw,
            import_energy_kwh=self.power_model.import_energy_kwh,
            export_energy_kwh=self.power_model.export_energy_kwh,
            grid_frequency=frequency,
            voltage_ll=voltage_ll,
        )

    # --------------------------------------------------
    # Telemetry
    # --------------------------------------------------
    def get_telemetry(self) -> EnergyMeterTelemetry:
        return self._last_telemetry

    # --------------------------------------------------
    # No Control Capability
    # --------------------------------------------------
    def apply_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        return {}

    def init_applied_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        return {}