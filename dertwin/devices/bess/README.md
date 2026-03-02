# BESS Package

The bess package implements a complete Battery Energy Storage System model, including physical behavior, AC control, orchestration, and simulator integration.
It is structured in clear layers to separate physics, control dynamics, and protocol interaction.

## Files Overview
### battery.py

**DC-side battery physics model**

Responsible for deterministic energy behavior:
- Energy tracking (kWh)
- State of Charge (SOC)
- Charge / discharge efficiency
- SOC-based power limits:
  - Hard cutoffs
  - Linear derating regions
- Thermal model
- Cycle counting
- State of health tracking

The battery model:
- Does not know about ramping
- Does not know about protocols
- Only applies physical constraints

### inverter.py
**AC-side power control model**

Responsible for grid-facing behavior:
- Target power handling
- Charge / discharge power clamping
- Ramp-rate limiting (kW/s)
- Active power tracking
- Reactive power calculation
- Apparent power calculation
- Grid frequency reporting

The inverter model:
- Does not know about SOC
- Does not enforce battery limits
- Only manages AC dynamics

### bess.py

**Physical orchestration layer**

Coordinates battery and inverter behavior into a single physical plant.

On each simulation step:

- Receives inverter target power
- Applies battery SOC limits
- Updates inverter ramped output
- Integrates battery energy
- Produces composed telemetry

This layer ensures:
- SOC protection happens before ramp execution
- Actual power is always physically consistent
- Telemetry represents real internal state

`BESSModel` acts as the physical plant abstraction.

### controller.py

**Device-level command and telemetry bridge**

Responsible for:
- Receiving external commands
- Detecting command changes
- Applying commands to the physical model
- Synchronizing startup state without mutating defaults
- Exposing telemetry to external protocols

This layer separates:
- Protocol logic
- Device state management
- Physical simulation

### simulator.py

**Runtime integration wrapper**

Wraps the controller into a runnable simulated device.

Provides:
- Execution loop
- Time stepping
- Exposing Modbus-accessible values
- Startup initialization
- Telemetry publishing

This is the entry point used by site-level simulations.

## Architectural Principles

The BESS package follows strict separation of concerns:
- BatteryModel → DC physics and limits
- InverterModel → AC control and ramp behavior
- BESSModel → Physical coordination
- Controller → Command lifecycle management
- Simulator → Runtime execution

This structure ensures:
- Deterministic physical behavior
- Clear ownership of limits and ramp logic
- Safe startup initialization
- Clean separation between physics and protocol layers
- Extensibility for future control modes (remote, grid-forming, etc.)

This package represents a complete, modular BESS simulation stack suitable for integration into multi-device site controllers.