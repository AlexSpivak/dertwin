# CHP Package

The `chp` package implements a complete **Combined Heat and Power** simulation, including engine physics, power dispatch, command handling, and simulator integration.
It is structured in clear layers to separate physics, control dynamics, and protocol interaction.

The model is designed to be **generic across CHP units** rather than tied to a specific manufacturer. It is heavily inspired by the MWM TEM Evolution controller (TCG series gas engines), so the state machine register values and command semantics map cleanly to that family of controllers. Adapting to other CHP controllers is a register-map change, not a model change.

---

## Files Overview

### `engine.py`

**Engine physics and state machine**

Owns deterministic engine behavior:
- Unit state machine (`UnitState` enum matching MWM register `30279`):
  - `READY` → `STARTING` → `WARMUP` → `IDLE` → `SYNCHRONIZING` → `RUNNING` → `STOPPING`
- Configurable startup timings (`StartupTimings` dataclass)
- Engine speed dynamics (cranking, sync, nominal)
- Thermal model:
  - Coolant inlet / outlet temperatures
  - Oil temperature
  - Exhaust gas temperature
  - Intake air temperature
- Pressure model:
  - Oil pressure
  - Charge pressure
- Operating hour and start counter accumulation
- Fault state tracking (sticky until acknowledged)

The engine model:
- Does not know about electrical or heat power output
- Does not know about protocols
- Only owns physics and state transitions

### `chp.py`

**Composition layer — engine + electrical + heat**

Coordinates engine behavior with electrical and thermal output:
- Power setpoint handling (percent of rated)
- Setpoint clamping (`min_load_percent` / `max_load_percent`)
- Ramp rate limiting (%/s)
- Derating support via `permitted_power_percent`
- Electrical power output (`actual_power_percent × rated_kw`)
- Heat power output (`electrical_power × heat_to_power_ratio`)
- Telemetry composition

On each simulation step:
- Computes effective target based on engine state (zero unless running)
- Applies derating limit (`min(setpoint, permitted_power)`)
- Applies ramp limit
- Feeds the load factor back to the engine for thermal modelling
- Emits a fully-populated `CHPTelemetry` snapshot

`CHPModel` acts as the physical plant abstraction.

### `controller.py`

**Device-level command and telemetry bridge**

Responsible for:
- Receiving Modbus commands and translating them into engine actions
- Per-command change detection (no command re-triggers another command's side effect)
- Stateless command handling (`remote_acknowledgment` always executes)
- Synchronizing startup state without mutating defaults

Command map:
| Command | Effect |
|---|---|
| `start_stop` (0 / 1) | Request engine stop / start |
| `power_setpoint_percent` (0–110) | Dispatch active power as % of rated |
| `remote_acknowledgment` (`0x10E1`) | Clear active fault (must use magic value) |

This layer separates:
- Protocol logic
- Device state management
- Physical simulation

### `simulator.py`

**Runtime integration wrapper**

Wraps the controller into a runnable `SimulatedDevice`.

Provides:
- Execution loop integration with `DeviceController`
- Time stepping via `update(dt)`
- Configurable startup timings, rated capacity, and heat-to-power ratio
- External model integration (`AmbientTemperatureModel`, etc.)
- Compatibility properties (`is_running`, `electrical_power_kw`, `heat_power_kw`, `fault_code`)
- Telemetry publishing

This is the entry point used by site-level simulations.

---

## Configuration

Per-asset CHP configuration in site JSON:

```json
{
  "type": "chp",
  "rated_kw": 4000.0,
  "heat_to_power_ratio": 1.0,
  "ramp_rate_percent_per_s": 5.0,
  "min_load_percent": 30.0,
  "max_load_percent": 110.0,
  "protocols": [
    {
      "kind": "modbus_tcp",
      "ip": "0.0.0.0",
      "port": 55001,
      "unit_id": 1,
      "register_map": "chp_modbus.yaml"
    }
  ]
}
```

| Parameter | Default | Description |
|---|---|---|
| `rated_kw` | `4000.0` | Rated electrical power output |
| `heat_to_power_ratio` | `1.0` | Heat output as multiple of electrical (typical 0.9–1.2 for gas CHP) |
| `ramp_rate_percent_per_s` | `5.0` | Max change in power output, % of rated per second |
| `min_load_percent` | `30.0` | Minimum stable load — setpoints below this clamp here, not zero |
| `max_load_percent` | `110.0` | Maximum sustained load (overload capability) |

---

## State Machine Semantics

The `UnitState` enum maps directly to register `30279` values from MWM TEM Evolution:

```
0  = FAULT             Engine in fault, blocks all dispatch
1  = READY             Engine off, ready to start
2  = STARTING          Cranking + ignition
3  = IDLE              Warming up at idle speed
4  = SYNCHRONIZING     Matching grid phase, breaker open
5  = RUNNING           Engine running, breaker closed, accepting setpoints
6  = STOPPING          Ramping down, breaker open, cooling
```

Typical lifecycle:
```
READY → STARTING → WARMUP → IDLE → SYNCHRONIZING → RUNNING → STOPPING → READY
```

Power dispatch (`set_power_setpoint_percent`) is only honored when the unit is in `RUNNING` with no active fault.

---

## Heat Output

Heat output uses a simple proportional model:
```
heat_power_kw = electrical_power_kw × heat_to_power_ratio
```

For gas-engine CHPs, `heat_to_power_ratio` of `0.9–1.2` is typical (slightly more thermal than electrical recovery). The model does **not** simulate:
- Thermal storage
- Hot water flow rate or temperature
- Heat exchanger dynamics
- Thermal startup delays

If your scenario needs any of these, extend `CHPModel` rather than the engine — the engine's thermal model only tracks engine internals (coolant, oil, exhaust), not the building-side heat circuit.

---

## Minimum Load Behavior

Real CHP units cannot operate below their minimum stable load while synchronized — they would trip. The model reflects this:

- `set_power_setpoint_percent(10)` with `min_load_percent=30` → target is set to **30%**, not 0%
- `set_power_setpoint_percent(0)` → target is **0%** (explicit "no dispatch")
- Negative values → treated as 0%

This matches the EMS's expected interface: to take the unit off-dispatch, write 0; to keep it running at minimum, write a value below the minimum.

---

## Architectural Principles

The CHP package follows strict separation of concerns:
- `EngineModel` → state machine and engine physics
- `CHPModel` → power composition and ramping
- `Controller` → command lifecycle management
- `Simulator` → runtime execution

This structure ensures:
- Realistic startup sequence (2–5 minutes by default, configurable per asset)
- Deterministic physical behavior
- Fault gating that blocks dispatch
- Clean separation between engine physics and electrical/thermal output
- Extensibility for future features (e.g. grid frequency droop, voltage regulation, thermal storage)

This package represents a complete, modular CHP simulation stack suitable for integration into multi-device site controllers and EMS integration testing scenarios.