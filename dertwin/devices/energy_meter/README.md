# Energy Meter Package

The energy_meter package implements a passive Point-of-Common-Coupling (PCC) measurement device.

It observes system-level power balance and grid state and applies realistic measurement behavior such as power factor drift and reactive power calculation.

Unlike generation or storage devices, the energy meter contains no physical control logic.

## Files Overview
### model.py

**Measurement model**

Responsible for realistic telemetry behavior:
- Active power passthrough
- Reactive power calculation
- Power factor drift modeling
- Three-phase power splitting
- Frequency reporting
- Energy value passthrough

The measurement model:
- Does not compute power balance
- Does not simulate grid physics
- Does not modify system state
- Only transforms system state into realistic telemetry

It represents the behavior of a real-world PCC meter.

### simulator.py

**Runtime integration wrapper**

Integrates the measurement model into the simulation engine.

Responsibilities:
- Receiving system state from SitePowerModel
- Querying grid frequency model
- Producing telemetry snapshots
- Exposing Modbus-accessible values
- Ignoring external control commands

The energy meter is intentionally passive.

It does not:
- Accept commands
- Modify grid behavior
- Influence power balance

It strictly observes.

## System Dependencies

The energy meter depends on:

- SitePowerModel → provides deterministic power balance
- GridFrequencyModel → provides frequency

It does not compute either.

This separation ensures:
- Clean system layering
- Deterministic behavior
- No duplicated physics
- Clear ownership of grid and power logic

## Architectural Principles

The Energy Meter package follows minimal layered design:
- SitePowerModel → system physics (external)
- EnergyMeterModel → measurement realism
- Simulator → runtime integration

This structure ensures:
- Strict separation between physics and measurement
- Deterministic system behavior
- Passive observation model
- Compatibility with protocol layers

The energy meter represents a realistic PCC measurement abstraction suitable for integration into multi-device site simulations.