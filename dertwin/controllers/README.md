# dertwin.controllers

Controllers package for orchestrating devices, protocols, and site runtime in DERTwin simulations.

This package provides:

- Device-level control abstraction (`DeviceController`)
- Full site orchestration and lifecycle management (`SiteController`)
- Integration with protocols (Modbus TCP and Modbus RTU)
- Coordination with external world models and simulation engine
- Runtime asset add/remove for live scenario scripting and EMS-in-the-loop testing

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
- Write telemetry to all protocols (routes by register function code: FC02 → discrete inputs, FC04 → input registers)
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
5. Retrieve telemetry (`get_telemetry()`) and write back to protocols — discrete inputs go to datastore 2, analog telemetry goes to datastore 4

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
- External models (ambient temperature, irradiance, grid voltage/frequency, and site power flow)

### Responsibilities
- Build full site from configuration
- Instantiate devices, controllers, and protocols
- Wire external models to devices
- Start and stop the simulation runtime
- Add or remove assets at runtime (e.g. live demos, scenario scripting, EMS-driven rosters)
- Manage asyncio tasks for real-time execution

### Constructor
```python
SiteController(config: Dict)
```
- `config`: Dict containing site configuration (assets, step size, register map locations, real-time flag, external model config)

### Lifecycle Methods

**`build()`**
- Instantiates devices based on `config["assets"]`
- Creates non-meter devices first, so the energy meter can observe them via the site power model when it's created
- Builds device controllers and attaches protocols via `_create_protocol()`
- Constructs external models — including the `SitePowerModel`, which aggregates load, PV, BESS, and CHP generation
- Initializes simulation engine
- Preserves config order: `site.controllers` appears in the same order as the config's `assets` list

**`start()`** *(async)*
- Launches protocol servers (TCP and RTU) for all assets registered during `build()`
- Starts real-time engine loop (if enabled)
- Runs site runtime asynchronously until cancelled

**`add_asset(spec: Dict)`** *(async)*
- Registers a new device at runtime, after `build()`, before or after `start()`
- The site power model picks up the new device automatically on the next tick — no rebuild required
- If the site is already running, the new asset's protocol server is started immediately
- Idempotent: re-registering an existing `asset_id` is a no-op
- Accepts the flat spec shape (see [Configuration Shapes](#configuration-shapes))

**`remove_asset(asset_id: str)`** *(async)*
- Cancels the asset's protocol task, shuts down its protocol server, removes its device from the site power model
- Engine keeps running for the remaining assets
- Removing an unknown `asset_id` is a no-op

**`stop()`** *(async)*
- Stops engine loop
- Shuts down all protocols gracefully (failed RTU serial binds don't crash the site)
- Cancels pending asyncio tasks

### Configuration Shapes

`SiteController` accepts two asset-declaration shapes. Both work in `build()`; only the flat shape is accepted by `add_asset()`.

**Legacy shape (multi-protocol library API):**

Each asset declares a `protocols: [...]` list. An asset can expose any number of protocols simultaneously — TCP, RTU, or both.

```json
{
  "type": "bess",
  "protocols": [
    { "kind": "modbus_tcp", "ip": "0.0.0.0", "port": 55001, "unit_id": 1, "register_map": "bess_modbus.yaml" },
    { "kind": "modbus_rtu", "port": "/tmp/dertwin_bess", "unit_id": 1 }
  ]
}
```

This is the path used by config-driven `build()` for full-flexibility site definitions and library consumers that need dual-protocol device exposure.

**Flat spec shape (runtime / single-TCP-endpoint):**

A single asset corresponds to a single Modbus TCP endpoint. Endpoint fields are top-level.

```json
{
  "asset_id": "bess-01",
  "type": "bess",
  "ip": "0.0.0.0",
  "port": 55001,
  "unit_id": 1,
  "capacity_kwh": 100.0,
  "initial_soc": 60.0
}
```

This is the path used by `add_asset()` for runtime registrations (e.g. EMS-driven dynamic asset rosters). It can also appear directly in `build()`'s assets list as a more compact alternative to the legacy shape.

In both shapes:
- `register_map` is optional. If omitted, a default register map is selected by `type` (e.g. `bess_modbus.yaml` for `bess`).
- `unit_id` defaults to `1`.
- `asset_id` is auto-derived from `type` and registration order if omitted; explicit `asset_id` is required if you want to call `remove_asset()` on it later.

### Protocol Creation

`_create_protocol(proto_cfg)` routes legacy protocol config blocks to the correct simulator class:

| `kind` | Class | Key Parameters |
|---|---|---|
| `modbus_tcp` | `ModbusTCPSimulator` | `ip`, `port`, `unit_id` |
| `modbus_rtu` | `ModbusRTUSimulator` | `port` (serial path), `unit_id`, `baudrate`, `parity`, `stopbits`, `bytesize`, `timeout` |

Unknown `kind` values raise `ValueError`.

### Device Creation

`_create_device(spec)` routes asset config to the correct simulator class:

| `type` | Class | Key Parameters |
|---|---|---|
| `bess` | `BESSSimulator` | `capacity_kwh`, `initial_soc`, `max_charge_kw`, `max_discharge_kw`, `ramp_rate_kw_per_s` |
| `inverter` | `PVSimulator` | `rated_kw`, `module_efficiency`, `area_m2` |
| `chp` | `CHPSimulator` | `rated_kw`, `heat_to_power_ratio`, `min_load_percent`, `max_load_percent` |
| `energy_meter` | `EnergyMeterSimulator` | (no parameters — observes external models) |

Unknown or unsupported asset types raise `ValueError`.

### Example Usage

**Static config build:**
```python
site = SiteController(config=my_site_config)
site.build()
await site.start()

# ... run simulation ...

await site.stop()
```

**Runtime add/remove (in-process scenario scripting):**
```python
site = SiteController(config={"site_name": "demo", "assets": []})
site.build()
runtime = asyncio.create_task(site.start())

await site.add_asset({
    "asset_id": "bess-01", "type": "bess",
    "ip": "127.0.0.1", "port": 55001, "unit_id": 1,
    "capacity_kwh": 200.0, "initial_soc": 50.0,
})

await site.add_asset({
    "asset_id": "pv-01", "type": "inverter",
    "ip": "127.0.0.1", "port": 55002, "unit_id": 1,
    "rated_kw": 20.0,
})

# ... assets are now exposed via Modbus and contribute to the site power model ...

await site.remove_asset("pv-01")

# ... pv-01 is gone; bess-01 keeps running ...

await site.stop()
```

**Mixed-protocol site config (legacy shape):**
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

**Full-stack site with CHP:**
```json
{
  "assets": [
    {
      "type": "bess",
      "capacity_kwh": 100.0,
      "protocols": [{ "kind": "modbus_tcp", "ip": "0.0.0.0", "port": 55001, "unit_id": 1, "register_map": "bess_modbus.yaml" }]
    },
    {
      "type": "inverter",
      "rated_kw": 30.0,
      "protocols": [{ "kind": "modbus_tcp", "ip": "0.0.0.0", "port": 55002, "unit_id": 1, "register_map": "pv_inverter_modbus.yaml" }]
    },
    {
      "type": "chp",
      "rated_kw": 4000.0,
      "heat_to_power_ratio": 1.0,
      "protocols": [{ "kind": "modbus_tcp", "ip": "0.0.0.0", "port": 55003, "unit_id": 1, "register_map": "chp_modbus.yaml" }]
    },
    {
      "type": "energy_meter",
      "protocols": [{ "kind": "modbus_tcp", "ip": "0.0.0.0", "port": 55004, "unit_id": 1, "register_map": "energy_meter_modbus.yaml" }]
    }
  ]
}
```

**Dual-protocol device (TCP + RTU on one asset):**
```json
{
  "type": "bess",
  "protocols": [
    { "kind": "modbus_tcp", "ip": "0.0.0.0", "port": 55001, "unit_id": 1, "register_map": "bess_modbus.yaml" },
    { "kind": "modbus_rtu", "port": "/tmp/dertwin_bess", "baudrate": 9600, "unit_id": 1, "register_map": "bess_modbus.yaml" }
  ]
}
```

---

## Integration with Core

`SiteController` integrates tightly with:
- `SimulationEngine` from `dertwin.core.engine`
- `SimulationClock` from `dertwin.core.clock`
- `ExternalModels` from `dertwin.devices.external.external_models`
- `DeviceController` wraps `SimulatedDevice` implementations

Execution order per tick:
```
external_models.update() → DeviceController.step() → clock.tick()
```

Telemetry flows from devices → controllers → protocols (TCP, RTU, or both).

`SitePowerModel` aggregates load, PV, BESS, and CHP generation into a single grid power balance. The energy meter observes this balance — it does not need to know which device types contributed to it. Dynamic adds and removes flow through automatically because the power model captures the device-type lists by reference rather than snapshotting them at build time.

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
- Dynamic membership — add and remove assets without restarting the site
- Config-driven and extensible