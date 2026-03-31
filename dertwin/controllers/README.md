# dertwin.controllers

Controllers package for orchestrating devices, protocols, and site runtime in DERTwin simulations.

This package provides:

- Device-level control abstraction (`DeviceController`)
- Full site orchestration and lifecycle management (`SiteController`)
- Integration with protocols (Modbus TCP and Modbus RTU)
- Coordination with external world models and simulation engine

---

## Modules

---

## device_controller.py

### `DeviceController`

Manages a single simulated device, applying commands and pushing telemetry to protocols.

`DeviceController` is transport-agnostic — it interacts with any protocol object that exposes `.context` and `.unit_id`. The same controller code works identically with `ModbusTCPSimulator`, `ModbusRTUSimulator`, or both attached simultaneously.

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
- `protocols`: List of protocol objects (e.g., `ModbusTCPSimulator`, `ModbusRTUSimulator`) associated with this device
- `register_map`: Provides register definitions for mapping telemetry and commands

### Step Flow
1. Collect commands from protocols using `collect_write_instructions`
2. Initialize device on first step using `init_applied_commands`
3. Apply commands if changed since last step
4. Step device simulation (`update(dt)`)
5. Retrieve telemetry (`get_telemetry()`) and write back to protocols

### Example Usage

**Single protocol (TCP):**
```python
tcp = ModbusTCPSimulator(address="127.0.0.1", port=5020, unit_id=1)

controller = DeviceController(
    device=my_bess,
    protocols=[tcp],
    register_map=bess_register_map,
)

controller.step(dt=0.1)
```

**Single protocol (RTU):**
```python
rtu = ModbusRTUSimulator(port="/dev/ttyUSB0", unit_id=1, baudrate=9600)

controller = DeviceController(
    device=my_pv,
    protocols=[rtu],
    register_map=pv_register_map,
)

controller.step(dt=0.1)
```

**Dual protocol (TCP + RTU on the same device):**
```python
tcp = ModbusTCPSimulator(address="127.0.0.1", port=5020, unit_id=1)
rtu = ModbusRTUSimulator(port="/dev/ttyUSB0", unit_id=1)

controller = DeviceController(
    device=my_bess,
    protocols=[tcp, rtu],
    register_map=bess_register_map,
)

# Telemetry is written to both contexts; commands are collected from both
controller.step(dt=0.1)
```

---

## site_controller.py

### `SiteController`

High-level site runtime orchestrator. Manages:
- Simulation engine
- Devices and their controllers
- Protocol servers (TCP and RTU)
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
- Builds device controllers and attaches protocols via `_create_protocol()`
- Constructs external models
- Initializes simulation engine

`start()`
- Launches protocol servers (TCP and RTU)
- Starts real-time engine loop (if enabled)
- Runs site runtime asynchronously

`stop()`
- Stops engine loop
- Shuts down protocols gracefully (handles failed RTU serial binds without crashing)
- Cancels pending asyncio tasks

### Protocol Creation

`_create_protocol(proto_cfg)` routes protocol config blocks to the correct simulator class:

| `kind` | Class | Key Parameters |
|---|---|---|
| `modbus_tcp` | `ModbusTCPSimulator` | `ip`, `port`, `unit_id` |
| `modbus_rtu` | `ModbusRTUSimulator` | `port` (serial path), `unit_id`, `baudrate`, `parity`, `stopbits`, `bytesize`, `timeout` |

Unknown `kind` values raise `ValueError`.

### Example Usage

**TCP-only site:**
```python
site = SiteController(config=my_site_config)
site.build()
await site.start()

# ... run simulation ...

await site.stop()
```

**Mixed-protocol site config:**
```json
{
  "assets": [
    {
      "type": "bess",
      "protocols": [{ "kind": "modbus_tcp", "ip": "0.0.0.0", "port": 55001, "unit_id": 1, "register_map": "bess_modbus.yaml" }]
    },
    {
      "type": "inverter",
      "protocols": [{ "kind": "modbus_rtu", "port": "/tmp/dertwin_pv", "baudrate": 9600, "unit_id": 2, "register_map": "pv_inverter_modbus.yaml" }]
    },
    {
      "type": "energy_meter",
      "protocols": [{ "kind": "modbus_rtu", "port": "/tmp/dertwin_meter", "baudrate": 9600, "unit_id": 3, "register_map": "energy_meter_modbus.yaml" }]
    }
  ]
}
```

**Dual-protocol device config (TCP + RTU on one asset):**
```json
{
  "type": "bess",
  "protocols": [
    { "kind": "modbus_tcp", "ip": "0.0.0.0", "port": 55001, "unit_id": 1, "register_map": "bess_modbus.yaml" },
    { "kind": "modbus_rtu", "port": "/tmp/dertwin_bess", "baudrate": 9600, "unit_id": 1, "register_map": "bess_modbus.yaml" }
  ]
}
```

### Device Creation
- BESS → `BESSSimulator`
- PV → `PVSimulator`
- Energy Meter → `EnergyMeterSimulator`

Unknown or unsupported asset types raise `ValueError`.

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
Telemetry flows from devices → controllers → protocols (TCP, RTU, or both).

---

## Protocols

Currently supported:
- `modbus_tcp` via `ModbusTCPSimulator`
- `modbus_rtu` via `ModbusRTUSimulator`

Both share the same register datastore (`ModbusServerContext`) and the same encode/decode functions. `DeviceController` is transport-agnostic — adding a new protocol requires implementing the `.context` / `.unit_id` / `run_server()` / `shutdown()` interface and adding a routing branch in `SiteController._create_protocol()`.

---

## Design Principles
- Deterministic execution per simulation tick
- Transport-agnostic controller layer
- Clear separation of device, protocol, and site layers
- Graceful shutdown — failed protocol binds don't crash the site
- Async-safe for real-time operation
- Config-driven and extensible