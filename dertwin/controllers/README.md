# dertwin.controllers

Controllers package for orchestrating devices, protocols, and site runtime in DERTwin simulations.

This package provides:

- Device-level control abstraction (`DeviceController`)
- Full site orchestration and lifecycle management (`SiteController`)
- Integration with protocols (currently Modbus)
- Coordination with external world models and simulation engine

---

## Modules

---

## device_controller.py

### `DeviceController`

Manages a single simulated device, applying commands and pushing telemetry to protocols.

### Responsibilities

- Collect commands from all attached protocols
- Apply commands to device
- Write telemetry to all protocols
- Step the device simulation forward

### Constructor

```python
DeviceController(
    device: SimulatedDevice,
    protocols: List,
    register_map: RegisterMap
)
```

- `device`: The underlying SimulatedDevice instance
- `protocols`: List of protocol objects (e.g., Modbus) associated with this device
- `register_map`: Provides register definitions for mapping telemetry and commands

### Step Flow
1. Collect commands from protocols using `collect_write_instructions`
2. Initialize device on first step using `init_applied_commands`
3. Apply commands if changed since last step
4. Step device simulation (`update(dt)`)
5. Retrieve telemetry (`get_telemetry()`) and write back to protocols

### Example Usage
```python
modbus = ModbusSimulator(
    address=proto_cfg["ip"],
    port=proto_cfg["port"],
    unit_id=proto_cfg.get("unit_id", 1),
)

controller = DeviceController(
    device=my_bess,
    protocols=[modbus],
    register_map=bess_register_map
)

controller.step(dt=0.1)
```

### site_controller.py
`SiteController`

High-level site runtime orchestrator. Manages:
- Simulation engine
- Devices and their controllers
- Protocol servers
- External models (ambient temperature, irradiance, grid voltage/frequency)

### Responsibilities
- Build full site from configuration
- Instantiate devices, controllers, and protocols
- Wire external models to devices
- Start and stop the simulation runtime
- Manage asyncio tasks for real-time execution

### Constructor
```python
SiteController(config: Dict)
```
- `config`: Dict containing site configuration (assets, step size, register map locations, real-time flag, external model config)

### Lifecycle Methods

`build()`
- Instantiates devices based on `config["assets"]`
- Creates energy meters
- Builds device controllers and attaches protocols
- Constructs external models
- Initializes simulation engine

`start()`
- Launches protocol servers
- Starts real-time engine loop (if enabled)
- Runs site runtime asynchronously

`stop()`
- Stops engine loop
- Shuts down protocols gracefully
- Cancels pending asyncio tasks

### Example Usage
```python
site = SiteController(config=my_site_config)
site.build()
await site.start()

# ... run simulation ...

await site.stop()
```

### Device Creation
- BESS → `BESSSimulator`
- PV → `PVSimulator`
- Energy Meter → `EnergyMeterSimulator`

Unknown or unsupported asset types raise ValueError.

---
## Integration with Core
`SiteController` integrates tightly with:
- `SimulationEngine` from `dertwin.core.engine`
- `SimulationClock` from `dertwin.core.clock`
- `ExternalModels` from `dertwin.devices.external.external_models`
- `DeviceController` wraps `SimulatedDevice` implementations

Execution order per tick:
```markdown
external_models.update() → DeviceController.step() → clock.tick()
```
Telemetry flows from devices → controllers → protocols (e.g., Modbus).

---

## Protocols
Currently supported:
- `modbus_tcp` (via ModbusSimulator)
Other protocols can be added by implementing the required interface for `protocols` in `DeviceController`.

---

## Design Principles
- Deterministic execution per simulation tick
- Clear separation of device, protocol, and site layers
- Async-safe for real-time operation
- Config-driven and extensible