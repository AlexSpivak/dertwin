from abc import ABC, abstractmethod
from typing import Dict


class SimulatedDevice(ABC):

    @abstractmethod
    def update(self, dt: float) -> None:
        pass

    @abstractmethod
    def get_telemetry(self) -> Dict[str, float]:
        pass

    @abstractmethod
    def apply_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        pass
