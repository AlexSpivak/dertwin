# DERTwin – Distributed Energy Resource Twin Simulator

DERTwin is a **modular, deterministic simulator** for distributed energy resources (DER), including:

- Battery Energy Storage Systems (BESS)
- PV inverters
- Combined Heat and Power units (CHP)
- Passive energy meters
- Grid-connected sites

It provides a full **simulation stack** with:

- Deterministic physical models
- Command and telemetry lifecycle management
- Modbus TCP and RTU protocol exposure with full FC02/FC03/FC04/FC06/FC10 support
- Site-level orchestration with external models (ambient temperature, irradiance, grid frequency/voltage, and site power flow)

This repository is intended for **DER research, control testing, and EMS-in-the-loop experiments**.

---

## Key Features

- **Simulation-time determinism:** All models are fully time-driven; predictable and repeatable behavior.
- **Modular device simulation:** Each DER device (BESS, PV, CHP, energy meter) is separated into physical model, controller, and simulator wrapper.
- **Protocol integration:** Devices expose telemetry and receive commands via **Modbus TCP** and **Modbus RTU**. Full address space (0–65535) on all four datastores (discrete inputs, coils, input registers, holding registers). A single site can mix both transports, and a single device can expose both simultaneously.
- **Per-register endianness:** Big-endian (default) or little-endian (Sungrow, Carlo Gavazzi) configurable per register.
- **Transport-agnostic controllers:** `DeviceController` works identically with TCP, RTU, or both — the protocol layer is fully decoupled from device physics.
- **Site orchestration:** `SiteController` coordinates multiple devices, external models, and protocol servers across transports.
- **Realistic CHP state machine:** MWM TEM Evolution-compatible 20-state machine with configurable startup timings, thermal physics, and FC02 discrete input flags.
- **Telemetry abstraction:** Standardized telemetry classes (`TelemetryBase`) for consistent reporting across devices.
- **Clean separation of concerns:** Physical limits, AC/DC coordination, command handling, telemetry, and protocol exposure are clearly layered.
- **Asynchronous runtime:** Supports real-time simulation and step-wise deterministic execution.

---

## Architecture Overview
![Architecture overview.png](Architecture%20overview.png)

**Flow per simulation step:**

1. External models update (world simulation: temperature, irradiance, grid)
2. Device controllers collect commands and step devices
3. Device simulators integrate physical behavior
4. Telemetry objects are populated (`TelemetryBase`)
5. Telemetry is written to Modbus registers (TCP and/or RTU) — routed by function code (FC02 → discrete inputs, FC04 → input registers)
6. Simulation clock advances

---

## Packages Overview

### `core/`

- **clock.py:** SimulationClock for deterministic time stepping
- **engine.py:** SimulationEngine for orchestrating devices and external models
- **device.py:** Abstract base class for simulated devices
- **registers.py:** RegisterMap & RegisterDefinition, defines read/write registers with per-register endianness

### `controllers/`

- **device_controller.py:** Bridges commands and telemetry between devices and protocols. Transport-agnostic — works with TCP, RTU, or both attached to the same device.
- **site_controller.py:** Orchestrates site runtime, protocols, external models, and simulation engine. Routes protocol config to the correct simulator class via `_create_protocol()` and asset config via `_create_device()`.

### `devices/`

- **bess/** – Full BESS simulation stack
- **pv/** – PV inverter simulator (irradiance-driven power output, with curtailment and remote on/off)
- **chp/** – Combined heat and power simulator (gas-engine state machine, realistic startup, configurable timings, heat output)
- **energy_meter/** – Passive PCC measurement device
- **external/** – External world models: ambient temperature, irradiance, grid frequency/voltage, and site power flow (aggregates load, PV, BESS, and CHP)

### `telemetry/`

- Provides base classes for telemetry data (`TelemetryBase`)
- Standardizes device telemetry reporting
- Supports conversion to dictionaries for protocol layers
- Ensures consistency across devices (BESS, PV, CHP, meters)
- CHP telemetry includes FC02 discrete flag fields serialized as single bits

### `protocol/`

- **modbus.py:** Modbus TCP and RTU simulation for devices, with:
  - `ModbusTCPSimulator` – async Modbus TCP server
  - `ModbusRTUSimulator` – async Modbus RTU server over serial
  - Shared telemetry and command register handling (encode, decode, read, write)
  - Per-register function code routing — FC02 discrete inputs, FC03/FC04 input registers, FC06/FC10 holding registers
  - Per-register endianness (big-endian default, little-endian for Sungrow/Carlo Gavazzi-style devices)
  - Integration with DeviceController for deterministic, transport-agnostic device-protocol interactions
  - Graceful shutdown handling for both transports (failed serial binds don't crash the site)

---

## Root Utilities

- **main.py** – Entry point, runs a site simulation from JSON configuration
- **logging_config.py** – Centralized logging configuration

**Example startup:**

```bash
python main.py -c configs/demo_config.json
```

---
## Simulation Workflow
- Real-time mode: Runs asynchronously with actual wall-clock timing
- Deterministic mode: Steps are advanced manually per clock for reproducible simulation

---
## Detailed information
- [`controllers`](controllers/README.md) - detailed controllers package architecture overview
- [`core`](core/README.md) - detailed core package architecture overview
- detailed device simulator overviews:
  - [`bess`](devices/bess/README.md) - detailed BESS package simulation and physics overview
  - [`chp`](devices/chp/README.md) - detailed CHP package simulation, state machine, and thermal physics overview
  - [`energy_meter`](devices/energy_meter/README.md) - detailed energy meter simulation and observation model overview
  - [`pv`](devices/pv/README.md) - detailed PV and inverter simulation and physics overview
  - [`external`](devices/external/README.md) - detailed external package overview on simulation of grid, physical processes and power flow behaviour
- [`protocol`](protocol/README.md) - detailed overview on Modbus TCP and RTU protocol integration including FC02 discrete inputs and per-register endianness
- [`telemetry`](telemetry/README.md) - data structures overview