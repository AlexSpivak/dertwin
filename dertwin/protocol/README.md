# Protocol Package – Modbus Simulation

The `protocol` package provides **communication protocol support** for DER devices within DERTwin.
It implements **Modbus TCP** and **Modbus RTU** simulation, enabling devices to expose telemetry and receive commands through standard Modbus registers over either transport.

---

## Overview

The package provides:

- **ModbusTCPSimulator** – asynchronous Modbus TCP server for a single device.
- **ModbusRTUSimulator** – asynchronous Modbus RTU server for a single device over serial.
- **`encoding.py`** – endian-aware `encode_value` / `decode_value` functions.
- **`modbus_helpers.py`** – register read/write helpers used by `DeviceController`.
- Integration with `DeviceController` for deterministic, transport-agnostic simulation.

Both simulators share the same register datastore (`ModbusServerContext`) and the same encode/decode functions. The only difference is the transport layer — TCP socket vs. serial port.

The package abstracts low-level Modbus register handling while ensuring:

- Proper scaling and type conversion (`uint16`, `int16`, `uint32`, `int32`)
- Per-register endianness — big-endian (default, standard Modbus) or little-endian (Sungrow, Carlo Gavazzi)
- Separation of **read (telemetry)** vs **write (command)** registers
- Per-register function code routing (`FC02` discrete inputs vs `FC03/FC04` registers)
- Full 16-bit Modbus address space — all four datastores (`di`, `co`, `ir`, `hr`) hold 65 536 addresses each, matching real Modicon device capability
- Deterministic communication for simulation-time driven execution
- Graceful shutdown handling for both transports

---

## Modules

### `modbus.py`

#### `ModbusTCPSimulator`

Asynchronous Modbus TCP server for a single device.

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

**Testing without hardware:**

For development and CI, use `socat` to create virtual serial port pairs:

```bash
socat -d -d pty,raw,echo=0,link=/tmp/sim_port pty,raw,echo=0,link=/tmp/client_port &
```

Point the simulator at `/tmp/sim_port` and your EMS client at `/tmp/client_port`.

---

### `encoding.py`

Endian-aware encode/decode functions for Modbus register values.

```python
from dertwin.protocol.encoding import encode_value, decode_value
from dertwin.core.registers import RegisterEndian
```

#### `encode_value(value, data_type, scale, count, endian)`

Converts a physical float value into a list of 16-bit Modbus register words.

| Argument | Type | Description |
|---|---|---|
| `value` | `float` | Physical value (e.g. `50.0` kW) |
| `data_type` | `str` | `uint16`, `int16`, `uint32`, `int32` |
| `scale` | `float` | Register scale factor (`physical = raw × scale`) |
| `count` | `int` | Number of registers (1 for 16-bit, 2 for 32-bit) |
| `endian` | `RegisterEndian` | `BIG` (default) or `LITTLE` |

```python
# Big-endian (default — standard Modbus)
words = encode_value(500.0, "int32", 0.1, 2, RegisterEndian.BIG)
# → [0x0000, 0x1388]  (high word first)

# Little-endian (Sungrow BESS, Carlo Gavazzi meters)
words = encode_value(500.0, "int32", 0.1, 2, RegisterEndian.LITTLE)
# → [0x1388, 0x0000]  (low word first)
```

#### `decode_value(registers, data_type, scale, endian)`

Decodes a list of Modbus register words back into a physical float value.

```python
value = decode_value([0x1388, 0x0000], "int32", 0.1, RegisterEndian.LITTLE)
# → 500.0
```

---

### `modbus_helpers.py`

Register read/write helpers used by `DeviceController`. All functions are endian-aware — they read the `endian` field from each `RegisterDefinition` automatically.

```python
from dertwin.protocol.modbus_helpers import (
    write_telemetry_registers,
    write_command_registers,
    collect_write_instructions,
)
```

#### `write_telemetry_registers(context, unit_id, telemetry, register_map)`

Writes device telemetry into the Modbus datastore. Routes each register to the correct datastore by its function code:

| Function code | Datastore | Used for |
|---|---|---|
| `FC02` | Discrete inputs | Single-bit boolean flags (fault, running, breaker closed) |
| `FC03` | Holding registers | Read-back of writeable values (rare) |
| `FC04` | Input registers | Multi-byte analog telemetry (default) |

Only registers present in the `telemetry` dict are written — missing keys are skipped.

#### `write_command_registers(context, unit_id, commands, register_map)`

Writes command values into Modbus holding registers (FC03/06/10 share datastore 3). Only registers explicitly present in `commands` are written — unrelated registers are never overwritten with zero.

#### `collect_write_instructions(register_map, context, unit_id)`

Reads all write-direction registers from the Modbus holding register store and returns a dict of `internal_name → decoded float`. Used by `DeviceController` on each step to detect command changes.

---

## Function Code Support

| Function code | Direction | Datastore | Used in |
|---|---|---|---|
| `0x02` Read Discrete Inputs | Read | `di` (single bits) | CHP fault/warning/breaker flags |
| `0x03` Read Holding Registers | Read | `hr` (16-bit words) | Command readback, occasional telemetry |
| `0x04` Read Input Registers | Read | `ir` (16-bit words) | Default telemetry path |
| `0x06` Write Single Register | Write | `hr` | Single-register commands (start/stop, mode) |
| `0x10` Write Multiple Registers | Write | `hr` | Multi-register commands (32-bit setpoints) |

Discrete inputs (`FC02`) write a single bit (0 or 1) per register address. Truthy values become `1`, falsy values become `0`. They are completely isolated from input registers and holding registers at the same address — writing a discrete input does not affect the corresponding holding or input register slot.

---

## Endianness

Real-world devices often deviate from standard big-endian Modbus byte order for 32-bit registers. The `endian` field on each `RegisterDefinition` controls this per-register:

```yaml
# Standard big-endian (default — no field needed)
- name: total_import_energy
  type: uint32
  count: 2

# Little-endian — Sungrow PowerStack BESS
- name: on_grid_power_setpoint
  type: int32
  count: 2
  endian: little
```

| Value | Wire order | Used by |
|---|---|---|
| `big` | `[high_word, low_word]` | Standard Modbus (default) |
| `little` | `[low_word, high_word]` | Sungrow BESS, Carlo Gavazzi meters |

The generic simulator register maps (`bess_modbus.yaml`, `energy_meter_modbus.yaml`, `pv_inverter_modbus.yaml`, `chp_modbus.yaml`) use big-endian throughout. When integrating a real device that uses little-endian, create a device-specific register map and add `endian: little` only to the affected registers.

---

## Address Space

All four datastores (discrete inputs, coils, input registers, holding registers) cover the **full 16-bit Modbus address space** — addresses `0x0000` through `0xFFFF` (0–65535). This matches what real Modicon-convention devices expose:

- `1xxxx` discrete inputs → datastore `di`
- `0xxxx` coils → datastore `co`
- `3xxxx` input registers → datastore `ir`
- `4xxxx` holding registers → datastore `hr`

Some devices (e.g. MWM TEM Evolution) place registers above address 30 000, which would not fit in a typical 10 000-address simulator block. DERTwin sizes all four blocks to the full address space so any real device's register map works without modification.

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

A single device can expose both protocols simultaneously by listing multiple entries in its `protocols` array. A site can freely mix TCP and RTU devices.

---

## Integration with DERTwin

`DeviceController` and `SiteController` use both simulators interchangeably. `DeviceController` is transport-agnostic — it accesses `.context` and `.unit_id` on any protocol object regardless of transport.

**Simulation flow per tick:**
```
External Models update
    → DeviceController.step(dt)
        → collect_write_instructions()   # read commands from Modbus registers
        → device.apply_commands()        # apply changed commands to device
        → device.update(dt)              # advance physics
        → write_telemetry_registers()    # write new telemetry to Modbus registers
    → Clock advances
```

This guarantees deterministic behavior while providing a realistic protocol interface to any Modbus client.

---

## Notes

- Designed for simulation — populates registers with physically modeled values rather than static fixtures.
- Both TCP and RTU shutdown methods handle startup failures gracefully — a failed serial port bind will not crash `SiteController.stop()`.
- `encode_value` and `decode_value` are pure functions with no side effects — safe to use in tests without a running server.
- Fully async and compatible with `asyncio` event loops in real-time and headless modes.
- Datastore sizing is ~2 MB per device (4 × 65 536 × 8 bytes). Negligible for typical scenarios but worth knowing if simulating many devices simultaneously.