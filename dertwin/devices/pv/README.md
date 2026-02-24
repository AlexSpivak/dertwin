# PV Package

The `pv` package implements a photovoltaic inverter simulation including DC irradiance response, AC conversion behavior, ramp dynamics, and simulator integration.

It is structured in layered components to separate generation physics, inverter dynamics, orchestration, and runtime integration.

## Files Overview
### panel.py

**DC-side photovoltaic generation model**

Responsible for deterministic solar power behavior:
- Irradiance-to-power conversion
- Rated power limiting
- Temperature derating
- DC-side clipping
- Deterministic power curve

The PV array model:
- Does not handle AC ramping
- Does not manage grid interaction
- Does not expose protocols
- Only converts environmental input into available DC power

### inverter.py

**AC-side inverter dynamics model**

Responsible for grid-facing behavior:
- Target power tracking
- Active power ramp-rate limiting
- Output clamping to rated AC power
- Reactive power calculation
- Apparent power calculation

The inverter model:
- Does not know about irradiance physics
- Does not manage environmental inputs
- Only controls AC output behavior

### pv.py

**Physical orchestration layer**

Coordinates DC generation and AC conversion into a single plant model.

On each simulation step:
- Receives irradiance input
- Computes available DC power
- Applies inverter ramp constraints
- Produces physically consistent AC output
- Integrates daily energy production
- Generates composed telemetry

This ensures:
- Ramp limits are respected
- AC output never exceeds available DC power
- Telemetry reflects realistic inverter behavior 

`PVModel` represents the full photovoltaic plant abstraction.

### controller.py

**Device-level command and telemetry bridge**

Responsible for:
- Receiving external curtailment or control commands
- Detecting command changes
- Applying commands to inverter model
- Exposing telemetry to external protocols
- Maintaining safe startup state

Separates:
- Protocol logic
- Device lifecycle
- Physical simulation

### simulator.py

**Runtime integration wrapper**

Wraps the controller into a runnable simulated device.

Provides:
- Time stepping
- Execution loop integration
- Startup synchronization
- Telemetry publishing
- Exposing Modbus-accessible values

This is the entry point used by site-level simulations.

## Architectural Principles

The PV package follows layered separation:
- PVArrayModel → DC generation physics
- InverterModel → AC dynamics and ramping
- PVModel → Physical coordination
- Controller → Command lifecycle
- Simulator → Runtime execution

This structure ensures:
- Deterministic solar response
- Proper ramp-rate enforcement
- Realistic inverter clipping
- Clear separation between environment and grid behavior
- Extensibility for future features (curtailment modes, voltage support, etc.)

The PV package represents a modular and extensible photovoltaic plant simulator suitable for multi-device site integration.