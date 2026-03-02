# Protocol Package – Modbus Simulation

The `protocol` package provides **communication protocol support** for DER devices within DERTwin.  
Currently, it implements **Modbus TCP simulation**, enabling devices to expose telemetry and receive commands through standard Modbus registers.

---

## Overview

The package provides:

- **ModbusSimulator** – asynchronous Modbus TCP server for a single device.
- Functions to encode, write, and read telemetry and command registers.
- Integration with `DeviceController` for deterministic simulation.

It abstracts low-level Modbus register handling while ensuring:

- Proper scaling and type conversion (`uint16`, `int16`, `uint32`, `int32`)
- Separation of **read (telemetry)** vs **write (command)** registers
- Deterministic communication for simulation-time driven execution

---

## Modules

### `modbus.py`

#### `ModbusSimulator`

**Class:** `ModbusSimulator`

Asynchronous Modbus TCP server for a single device.

**Initialization:**

```python
modbus = ModbusSimulator(address="127.0.0.1", port=5020, unit_id=1)
```
**Methods:**
- `run_server()` – Start the async Modbus TCP server.
- `shutdown()` – Stop the server and cancel background task.

**Usage Example:**
```python
import asyncio
from dertwin.protocol.modbus import ModbusSimulator

modbus = ModbusSimulator("127.0.0.1", 5020, unit_id=1)

async def run():
    await modbus.run_server()
    await asyncio.sleep(10)  # server runs for 10s
    await modbus.shutdown()

asyncio.run(run())
```

---
### Telemetry and Command Functions

These functions allow controllers to map device state to Modbus registers:

1. `encode_value(value, data_type, scale, count)` – Converts a floating-point value into 16-bit or 32-bit register values.
2. `write_telemetry_registers(registers, context, unit_id, telemetry)` – Write device telemetry to readable Modbus registers.
3. `write_command_registers(registers, context, unit_id, commands)` – Write command values to holding registers for the device.
4. `collect_write_instructions(registers, context, unit_id)` – Read command registers from the Modbus context for the device.

These functions ensure:
- Proper scaling between internal floating-point units and Modbus registers
- Correct handling of signed/unsigned 16-bit and 32-bit types
- Logging of register writes and reads for simulation debugging

---

## Integration with DERTwin
`DeviceController` and `SiteController` use `ModbusSimulator` to:
- Apply external commands to devices
- Expose telemetry for monitoring
- Run asynchronously alongside simulation engine and external models

**Simulation flow with Modbus:**
```markdown
External Models update → DeviceController step → Device Simulation → Telemetry → ModbusSimulator registers → Clock tick
```
This guarantees deterministic behavior while providing a realistic protocol interface.

---

## Notes
- Designed for **simulation**, to complete emulation of real device behaviour by populating registers with simulated values.
- Supports multiple devices with separate unit_ids.
- Fully async and compatible with asyncio loops in DERTwin