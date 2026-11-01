# dertwin.core

Core simulation engine for deterministic Distributed Energy Resource (DER) digital twins.

This package provides the **simulation clock**, **engine orchestration**, **device abstraction**, and **register map infrastructure** that power the DERTwin runtime.

It is responsible for deterministic execution order, real-time coordination, and protocol-safe register definitions.

---

## Overview

The core package provides:

- 🕒 Deterministic simulation clock
- ⚙️ Ordered execution engine
- 🔌 Abstract simulated device interface
- 🗂 Register map loader and validator (YAML-based)

The design guarantees:
```markdown
external world → devices → telemetry
```

This ensures reproducible and causally correct simulation behavior.

---

# Modules

---

## clock.py

### `SimulationClock`

Controls simulation time progression.

### Features

- Fixed simulation step (e.g. 100ms)
- Optional real-time mode
- Absolute UTC anchor, so date-aware models know what day it is
- Deterministic progression
- Async-compatible

### Parameters

| Parameter | Type | Description |
|-----------|------|------------|
| `step` | float | Simulation timestep in seconds |
| `real_time` | bool | If True, wall-clock synchronized |
| `start_utc` | datetime \| str \| None | Absolute UTC instant time zero maps to. ISO 8601 accepted (bare, `Z`, or an offset). Defaults to the Unix epoch. |

### Behavior

- In real-time mode: waits to match wall time
- In deterministic mode: advances instantly
- `time` always represents simulation time, in seconds from the start
- `now_utc()` is the absolute instant: `start_utc + time`

Simulation time is **UTC throughout**. Defaulting `start_utc` to the Unix epoch
is what keeps `start_time_h` meaning "hours past midnight" for configs written
before absolute time existed.

### Example

```python
from datetime import datetime, timezone

clock = SimulationClock(
    step=0.1, real_time=True, start_utc="2025-10-31T00:00:00Z"
)

await clock.tick()
print(clock.time)      # 0.1
print(clock.now_utc())  # 2025-10-31 00:00:00.100000+00:00
```

`parse_utc(value)` is the shared ISO 8601 parser; a value with no zone is taken
as UTC.

## device.py

### `SimulatedDevice` (Abstract Base Class)

Defines the required interface for all simulated devices.

### Required Methods
```python
update(dt: float) -> None
get_telemetry() -> TelemetryBase
apply_commands(commands: Dict[str, float]) -> Dict[str, float]
init_applied_commands(commands: Dict[str, float]) -> Dict[str, float]
```
### Responsibilities
- Advance internal state
- Provide telemetry snapshot
- Accept control commands
- Return validated/normalized command results

All device simulator implementations (BESS, PV, Meter, etc.) must inherit from this class.

## engine.py

### `SimulationEngine`
The deterministic simulation orchestrator.

### Execution Order Per Tick
```markdown
1. external_models.update(utc_time, sim_time, dt)
2. device_controller.step(dt)
3. clock.tick()
```
This guarantees that:
- External conditions update first
- Devices react to updated world state
- Time advances last

### Constructor
```python
SimulationEngine(
    devices: List[DeviceController],
    clock: SimulationClock,
    external_models: Optional[ExternalModels] = None
)
```

### Methods
`run()`

Runs continuous real-time simulation. Only allowed when clock.real_time == True

`step_once`

Runs exactly one deterministic step.
Safe for:
- Testing
- CI pipelines
- Fast deterministic simulations

`stop()`

Stops real-time loop.

## registers.py
Provides a strongly-validated, immutable register definition system.

Designed for protocol-safe Modbus mapping.

---
### `RegisterDefinition`
Immutable definition of a single register.

**Fields**

| Field           | Description              |
| --------------- | ------------------------ |
| `name`          | External name            |
| `internal_name` | Internal telemetry field |
| `address`       | Register address         |
| `func`          | Modbus function code     |
| `direction`     | READ or WRITE            |
| `type`          | Data type                |
| `count`         | Register length          |
| `scale`         | Scaling factor           |
| `unit`          | Engineering unit         |
| `options`       | Enum mapping             |
| `description`   | Optional description     |

Each register is uniquely identified by:
```
(address, function, direction)
```

### `RegisterMap`
Immutable definition of a single register.

### Features

- YAML loading
- Duplicate detection
- Address overlap validation
- Fast lookup by:
  - name
  - address
  - (address, func, direction)

### Load From YAML
```python
from pathlib import Path

reg_map = RegisterMap.from_yaml(Path("bess_registers.yaml"))
```

### Lookup Examples
```python
reg_map.get_by_name("active_power")

reg_map.read_register(40001)

reg_map.get(address=40001, func=3, direction=RegisterDirection.READ)
```

### Validation Guarantees
- No duplicate names
- No duplicate keys
- No overlapping addresses per direction
- Strict direction validation

---

## Determinism & Safety
The core engine is designed to ensure:
- No hidden time sources
- No wall-clock dependency in deterministic mode
- Explicit execution ordering
- Clear separation of:
  - world state 
  - device state 
  - telemetry state 
  - protocol layer

This makes the engine suitable for:
- Digital twin development
- Hardware-in-the-loop testing
- Protocol validation
- CI regression tests 
- Controller development

---

## Example Minimal Deterministic Run
```python
clock = SimulationClock(step=0.1, real_time=False)

engine = SimulationEngine(
    devices=[...],
    clock=clock,
    external_models=None
)

await engine.step_once()
```

---

## Design Principles

- Deterministic first
- Explicit state transitions
- Clear causality
- Protocol safety
- Strict validation
- Async-ready
- Production-oriented