# Protocol Package – Modbus Simulation

The `protocol` package provides **communication protocol support** for DER devices within DERTwin.  
It implements **Modbus TCP** and **Modbus RTU** simulation, enabling devices to expose telemetry and receive commands through standard Modbus registers over either transport.

---

## Overview

The package provides:

- **ModbusTCPSimulator** – asynchronous Modbus TCP server for a single device.
- **ModbusRTUSimulator** – asynchronous Modbus RTU server for a single device over serial.
- Shared functions to encode, write, and read telemetry and command registers.
- Integration with `DeviceController` for deterministic, transport-agnostic simulation.

Both simulators share the same register datastore (`ModbusServerContext`) and the same encode/decode functions. The only difference is the transport layer — TCP socket vs. serial port.

It abstracts low-level Modbus register handling while ensuring:

- Proper scaling and type conversion (`uint16`, `int16`, `uint32`, `int32`)
- Separation of **read (telemetry)** vs **write (command)** registers
- Deterministic communication for simulation-time driven execution
- Graceful shutdown handling for both transports

---

## Modules

### `modbus.py`

#### `ModbusTCPSimulator`

Asynchronous Modbus TCP server for a single device.

**Initialization:**

```python
from dertwin.protocol.modbus import ModbusTCPSimulator

tcp = ModbusTCPSimulator(address="127.0.0.1", port=5020, unit_id=1)
```

**Methods:**
- `run_server()` – Start the async Modbus TCP server.
- `shutdown()` – Stop the server and cancel background task.

**Usage Example:**

```python
import asyncio
from dertwin.protocol.modbus import ModbusTCPSimulator

tcp = ModbusTCPSimulator("127.0.0.1", 5020, unit_id=1)

async def run():
    await tcp.run_server()
    await asyncio.sleep(10)
    await tcp.shutdown()

asyncio.run(run())
```

---

#### `ModbusRTUSimulator`

Asynchronous Modbus RTU server for a single device over a serial port.

**Initialization:**

```python
from dertwin.protocol.modbus import ModbusRTUSimulator

rtu = ModbusRTUSimulator(
    port="/dev/ttyUSB0",
    unit_id=1,
    baudrate=9600,
    parity="N",
    stopbits=1,
)
```

**Parameters:**

| Parameter | Default | Description |
|---|---|---|
| `port` | *(required)* | Serial port path (e.g. `/dev/ttyUSB0` or a socat virtual port) |
| `unit_id` | *(required)* | Modbus slave identifier (1–247) |
| `baudrate` | `9600` | Serial baud rate |
| `bytesize` | `8` | Data bits (5–8) |
| `parity` | `"N"` | `"N"` (none), `"E"` (even), `"O"` (odd) |
| `stopbits` | `1` | Stop bits (1 or 2) |
| `timeout` | `1.0` | Serial read timeout in seconds |

**Methods:**
- `run_server()` – Start the async Modbus RTU serial server.
- `shutdown()` – Stop the server and cancel background task.

**Usage Example:**

```python
import asyncio
from dertwin.protocol.modbus import ModbusRTUSimulator

rtu = ModbusRTUSimulator(port="/dev/ttyUSB0", unit_id=1, baudrate=9600)

async def run():
    await rtu.run_server()
    await asyncio.sleep(30)
    await rtu.shutdown()

asyncio.run(run())
```

**Testing without hardware:**

For development and CI, use `socat` to create virtual serial port pairs:

```bash
socat -d -d pty,raw,echo=0,link=/tmp/sim_port pty,raw,echo=0,link=/tmp/client_port &
```

Point the simulator at `/tmp/sim_port` and the client at `/tmp/client_port`.

---

### Telemetry and Command Functions

These functions work identically with both TCP and RTU simulator contexts:

1. `encode_value(value, data_type, scale, count)` – Converts a floating-point value into 16-bit or 32-bit register values.
2. `write_telemetry_registers(registers, context, unit_id, telemetry)` – Write device telemetry to input registers (function code 0x04).
3. `write_command_registers(registers, context, unit_id, commands)` – Write command values to holding registers (function code 0x03).
4. `collect_write_instructions(registers, context, unit_id)` – Read command registers from the Modbus context for the device.

These functions ensure:
- Proper scaling between internal floating-point units and Modbus registers
- Correct handling of signed/unsigned 16-bit and 32-bit types
- Logging of register writes and reads for simulation debugging

---

## Configuration

Protocols are configured per-asset in the site JSON config:

**Modbus TCP:**
```json
{
  "kind": "modbus_tcp",
  "ip": "0.0.0.0",
  "port": 55001,
  "unit_id": 1,
  "register_map": "bess_modbus.yaml"
}
```

**Modbus RTU:**
```json
{
  "kind": "modbus_rtu",
  "port": "/tmp/dertwin_device",
  "baudrate": 9600,
  "parity": "N",
  "stopbits": 1,
  "unit_id": 1,
  "register_map": "bess_modbus.yaml"
}
```

A single device can expose both protocols simultaneously by listing multiple entries in its `protocols` array. A site can mix TCP and RTU devices freely.

---

## Integration with DERTwin

`DeviceController` and `SiteController` use both simulators interchangeably to:
- Apply external commands to devices
- Expose telemetry for monitoring
- Run asynchronously alongside simulation engine and external models

`DeviceController` is transport-agnostic — it accesses `.context` and `.unit_id` on any protocol object, regardless of whether it's TCP or RTU.

**Simulation flow with Modbus:**
```markdown
External Models update → DeviceController step → Device Simulation → Telemetry → Modbus registers → Clock tick
```
This guarantees deterministic behavior while providing a realistic protocol interface.

---

## Notes
- Designed for **simulation**, to complete emulation of real device behavior by populating registers with simulated values.
- Supports multiple devices with separate unit_ids.
- Both TCP and RTU shutdown methods handle startup failures gracefully — a failed serial port bind won't crash `SiteController.stop()`.
- Fully async and compatible with asyncio loops in DERTwin.