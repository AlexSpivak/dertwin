from typing import Dict

from dertwin.core.device import SimulatedDevice
from dertwin.devices.energy_meter.model import EnergyMeterModel
from dertwin.devices.external.power_flow import SitePowerModel
from dertwin.devices.external.grid_frequency import GridFrequencyModel


class EnergyMeterSimulator(SimulatedDevice):
    """
    Passive PCC energy meter simulator.

    Observes:
        - SitePowerModel (power balance)
        - GridFrequencyModel (frequency)
    """

    def __init__(
        self,
        power_model: SitePowerModel,
        grid_model: GridFrequencyModel,
        seed: int | None = None,
    ):
        self.power_model = power_model
        self.grid_model = grid_model

        self.model = EnergyMeterModel(seed=seed)

        self._last_telemetry: Dict[str, float] = {}

    # --------------------------------------------------
    # Simulation Step
    # --------------------------------------------------
    def update(self, dt: float) -> None:
        self.power_model.update(dt)

        frequency = self.grid_model.get_frequency()

        self._last_telemetry = self.model.measure(
            grid_power_kw=self.power_model.grid_power_kw,
            import_energy_kwh=self.power_model.import_energy_kwh,
            export_energy_kwh=self.power_model.export_energy_kwh,
            grid_frequency_hz=frequency,
        )

    # --------------------------------------------------
    # Telemetry
    # --------------------------------------------------
    def get_telemetry(self) -> Dict[str, float]:
        return self._last_telemetry

    # --------------------------------------------------
    # No Control Capability
    # --------------------------------------------------
    def apply_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        return {}

    def init_applied_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        return {}