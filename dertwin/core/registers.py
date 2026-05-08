from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import yaml


# ==========================================================
# ENUMS
# ==========================================================

class RegisterDirection(str, Enum):
    READ = "read"
    WRITE = "write"


class RegisterEndian(str, Enum):
    BIG = "big"
    LITTLE = "little"


# ==========================================================
# REGISTER DEFINITION
# ==========================================================

@dataclass(frozen=True)
class RegisterDefinition:
    name: str
    internal_name: str
    address: int
    func: int
    direction: RegisterDirection
    type: str
    count: int
    scale: float = 1.0
    unit: str = ""
    options: Optional[Dict[int, str]] = None
    description: Optional[str] = None
    endian: RegisterEndian = RegisterEndian.BIG

    @property
    def key(self) -> Tuple[int, int, RegisterDirection]:
        """
        Unique identifier inside protocol space.
        """
        return self.address, self.func, self.direction

    @property
    def is_little_endian(self) -> bool:
        return self.endian == RegisterEndian.LITTLE


class RegisterMap:
    """
    Immutable container for device register definitions.
    Provides validation and fast lookup.
    """

    def __init__(self, registers: List[RegisterDefinition]):
        self._registers = registers

        # Lookup tables
        self._by_key: Dict[Tuple[int, int, RegisterDirection], RegisterDefinition] = {}
        self._by_name: Dict[str, RegisterDefinition] = {}
        self._reads: Dict[int, RegisterDefinition] = {}
        self._writes: Dict[int, RegisterDefinition] = {}

        self._build_indexes()

    @classmethod
    def from_yaml(cls, path: Path) -> "RegisterMap":
        if not path.exists():
            raise FileNotFoundError(f"Register map not found: {path}")

        with open(path, "r") as f:
            data = yaml.safe_load(f)

        raw_registers = data.get("registers", [])
        definitions: List[RegisterDefinition] = []

        for entry in raw_registers:
            definitions.append(cls._parse_entry(entry))

        return cls(definitions)

    @staticmethod
    def _parse_entry(entry: Dict) -> RegisterDefinition:
        try:
            direction = RegisterDirection(entry["direction"])
        except ValueError:
            raise ValueError(f"Invalid register direction: {entry['direction']}")

        if "count" not in entry:
            raise ValueError(f"Missing 'count' for register: {entry['name']}")

        # Parse endian — default to BIG for backward compatibility
        raw_endian = entry.get("endian", "big")
        try:
            endian = RegisterEndian(raw_endian)
        except ValueError:
            raise ValueError(
                f"Invalid endian value '{raw_endian}' for register '{entry['name']}'. "
                f"Must be 'big' or 'little'."
            )

        return RegisterDefinition(
            name=entry["name"],
            internal_name=entry["internal_name"],
            address=entry["address"],
            func=entry["func"],
            direction=direction,
            type=entry["type"],
            count=entry["count"],
            scale=entry.get("scale", 1.0),
            unit=entry.get("unit", ""),
            options=entry.get("options"),
            description=entry.get("description"),
            endian=endian,  # NEW
        )

    def _build_indexes(self):
        for reg in self._registers:

            # Check duplicate key
            if reg.key in self._by_key:
                raise ValueError(
                    f"Duplicate register definition: {reg.key}"
                )

            # Check duplicate name
            if reg.name in self._by_name:
                raise ValueError(
                    f"Duplicate register name: {reg.name}"
                )

            self._by_key[reg.key] = reg
            self._by_name[reg.name] = reg

            # Simple address-based lookup (per direction)
            if reg.direction == RegisterDirection.READ:
                if reg.address in self._reads:
                    raise ValueError(
                        f"Overlapping READ register at address {reg.address}"
                    )
                self._reads[reg.address] = reg

            if reg.direction == RegisterDirection.WRITE:
                if reg.address in self._writes:
                    raise ValueError(
                        f"Overlapping WRITE register at address {reg.address}"
                    )
                self._writes[reg.address] = reg

    def get_by_name(self, name: str) -> RegisterDefinition:
        return self._by_name[name]

    def get(self, address: int, func: int, direction: RegisterDirection) -> Optional[RegisterDefinition]:
        return self._by_key.get((address, func, direction))

    def read_register(self, address: int) -> Optional[RegisterDefinition]:
        return self._reads.get(address)

    def write_register(self, address: int) -> Optional[RegisterDefinition]:
        return self._writes.get(address)

    @property
    def all(self) -> List[RegisterDefinition]:
        return list(self._registers)

    @property
    def reads(self) -> List[RegisterDefinition]:
        return [r for r in self._registers if r.direction == RegisterDirection.READ]

    @property
    def writes(self) -> List[RegisterDefinition]:
        return [r for r in self._registers if r.direction == RegisterDirection.WRITE]
