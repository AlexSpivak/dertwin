from abc import ABC, abstractmethod
from typing import Dict

from dertwin.telemetry.base import TelemetryBase


class SimulatedDevice(ABC):

    @abstractmethod
    def update(self, dt: float) -> None:
        pass

    @abstractmethod
    def get_telemetry(self) -> TelemetryBase:
        pass

    @abstractmethod
    def apply_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        pass

    @abstractmethod
    def init_applied_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        pass
