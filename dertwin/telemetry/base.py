from dataclasses import dataclass, asdict
from typing import Dict


@dataclass
class TelemetryBase:
    """
    Base telemetry class.

    Provides conversion to protocol dictionary.
    """

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)