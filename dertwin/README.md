# DERTwin – Distributed Energy Resource Twin Simulator

DERTwin is a **modular, deterministic simulator** for distributed energy resources (DER), including:

- Battery Energy Storage Systems (BESS)  
- PV inverters  
- Passive energy meters  
- Grid-connected sites  

It provides a full **simulation stack** with:

- Deterministic physical models  
- Command and telemetry lifecycle management  
- Modbus TCP protocol exposure  
- Site-level orchestration with external models (ambient temperature, irradiance, grid frequency/voltage, and site power flow)

This repository is intended for **DER research, control testing, and EMS-in-the-loop experiments**.

---

## Key Features

- **Simulation-time determinism:** All models are fully time-driven; predictable and repeatable behavior.  
- **Modular device simulation:** Each DER device (BESS, PV, energy meter) is separated into physical model, controller, and simulator wrapper.  
- **Protocol integration:** Devices expose telemetry and receive commands via **Modbus TCP**.  
- **Site orchestration:** `SiteController` coordinates multiple devices, external models, and protocol servers.  
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
5. Telemetry is written to Modbus registers  
6. Simulation clock advances  

---

## Packages Overview

### `core/`

- **clock.py:** SimulationClock for deterministic time stepping  
- **engine.py:** SimulationEngine for orchestrating devices and external models  
- **device.py:** Abstract base class for simulated devices  
- **registers.py:** RegisterMap & RegisterDefinition, defines read/write registers

### `controllers/`

- **device_controller.py:** Bridges commands and telemetry between devices and protocols  
- **site_controller.py:** Orchestrates site runtime, protocols, external models, and simulation engine

### `devices/`

- **bess/** – Full BESS simulation stack  
- **pv/** – PV inverter simulator (irradiance-driven power output)  
- **energy_meter/** – Passive PCC measurement device  
- **external/** – External world models: ambient temperature, irradiance, grid frequency/voltage, site power flow

### `telemetry/`

- Provides base classes for telemetry data (`TelemetryBase`)  
- Standardizes device telemetry reporting  
- Supports conversion to dictionaries for protocol layers  
- Ensures consistency across devices (BESS, PV, meters)  

### `protocol/`

- **modbus.py:** Modbus TCP simulation for devices, with:
  - `ModbusSimulator` class
  - Telemetry and command register handling  
  - Integration with DeviceController for deterministic device-protocol interactions

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
  - [`bess`](devices/bess/README.md) - detailed bess package simulation and physics overview
  - [`energy_meter`](devices/energy_meter/README.md) - detailed energy meter simulation and observation model overview
  - [`pv`](devices/pv/README.md) - detailed PV and inverter simulation and physics overview
  - [`external`](devices/external/README.md) - detailed external package overview on simulation of grid, physical processes and power flow behaviour
- [`prorocol`](protocol/README.md) - detailed overview on modbus protocol integration overview
- [`telemetry`](telemetry/README.md) - data structures overview
