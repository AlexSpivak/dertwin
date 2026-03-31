# DER Twin

Digital Twin infrastructure for modern energy systems.

**DER Twin** is a lightweight simulator for Distributed Energy Resources (DER) — BESS, PV inverters, energy meters, and grid models — exposed via Modbus TCP and Modbus RTU. Use it for EMS development, protocol testing, integration validation, and control algorithm sandboxing without touching real hardware.

---

## ⚡ Quickstart

### Option A — pip install

```bash
pip install dertwin
```

Bring your own site config and register maps:

```bash
dertwin -c path/to/your/config.json
```

You should see:
```
INFO | Building site: my-site
INFO | Starting Modbus TCP server | 0.0.0.0:55001 | unit=1
INFO | Simulation engine started | step=0.100s
```

The simulator is now accepting Modbus TCP connections on the ports defined in your config.

### Option B — Run from source

```bash
git clone https://github.com/AlexSpivak/dertwin.git
cd dertwin
python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m dertwin.main -c configs/simple_config.json
```

### Option C — Run with Docker

```bash
git clone https://github.com/AlexSpivak/dertwin.git
cd dertwin
python generate_compose.py configs/simple_config.json
docker compose up --build
```

`generate_compose.py` reads the config and generates a `docker-compose.yml` with the correct ports automatically. No manual port configuration needed.

---

## 🔌 Connect an EMS

With the simulator running, start the example EMS from a second terminal:

```bash
cd examples
python main_simple.py
```

You'll see the EMS connecting over Modbus and cycling the BESS between 40–60% SOC:

```
[EMS] Connected to BESS
[EMS] Starting in CHARGE mode
[EMS] STATUS=1 | SOC= 42.30% | P=  -20.00 kW | MODE=charge
[EMS] STATUS=1 | SOC= 44.10% | P=  -20.00 kW | MODE=charge
...
[EMS] Reached 60% → switching to DISCHARGE
```

For a full multi-device site (dual BESS + PV + energy meter + external models):

```bash
python -m dertwin.main -c configs/full_site_config.json
# in another terminal:
python examples/main_full.py
```

For a mixed-protocol site (BESS on TCP + PV and meter on RTU), see [Mixed Protocol Example](#-mixed-protocol-example-tcp--rtu) below.

---

## 🧱 Features

- Async Modbus TCP and RTU servers built on `pymodbus`
- Mixed-protocol support — TCP and RTU devices on the same site
- Config-driven site topology — add devices by editing JSON
- Irradiance, ambient temperature, grid frequency, and grid voltage models
- Multi-device support across independent ports
- External model events (voltage sags, frequency deviations)
- Simulation start time control (`start_time_h`) — start at noon, peak load, etc.
- Docker support with auto-generated Compose files
- Deterministic simulation with seeded random models
- Fully tested with `pytest`

---

## 📦 Repo Structure

```
dertwin/
├── configs/
│   ├── register_maps/              # Modbus register definitions (YAML)
│   ├── simple_config.json          # Single BESS — good starting point
│   ├── demo_config.json            # Full three-device site
│   ├── full_site_config.json       # Dual BESS + PV + meter + external models
│   └── mixed_protocol_config.json  # BESS (TCP) + PV (RTU) + meter (RTU)
├── dertwin/
│   ├── core/                # Clock, engine, register map loader
│   ├── controllers/         # Site and device orchestration
│   ├── devices/             # BESS, PV, energy meter, external models
│   ├── protocol/            # Modbus TCP + RTU servers
│   ├── telemetry/           # Telemetry dataclasses
│   └── main.py
├── examples/
│   ├── simple/              # Single BESS EMS example
│   ├── full/                # Multi-device EMS example (TCP)
│   ├── mixed/               # Mixed-protocol EMS example (TCP + RTU)
│   └── protocol/            # Shared Modbus TCP and RTU clients
├── tests/                   # Full test suite
├── generate_compose.py      # Docker Compose generator
└── Dockerfile
```

---

## ⚙️ Configuration

Sites are defined in JSON. Each asset declares its type, parameters, and protocol bindings:

```json
{
  "site_name": "my-site",
  "step": 0.1,
  "real_time": true,
  "start_time_h": 12.0,
  "register_map_root": "register_maps",
  "external_models": {
    "irradiance": { "peak": 1000.0, "sunrise": 6.0, "sunset": 18.0 },
    "grid_frequency": { "nominal_hz": 50.0, "noise_std": 0.002, "seed": 42 }
  },
  "assets": [
    {
      "type": "bess",
      "capacity_kwh": 100.0,
      "initial_soc": 60.0,
      "protocols": [{ "kind": "modbus_tcp", "ip": "0.0.0.0", "port": 55001, "unit_id": 1, "register_map": "bess_modbus.yaml" }]
    }
  ]
}
```

**`real_time: true`** — engine runs its own loop, use for `dertwin` CLI and EMS examples  
**`real_time: false`** — caller drives the clock via `step_once()`, use for tests  
**`start_time_h`** — sets simulation clock on startup (e.g. `12.0` for noon). All external models start from this time.  
**`register_map_root`** — path to register map directory, resolved relative to the working directory where you run `dertwin`  
**`ip: "0.0.0.0"`** — required when running inside Docker so port mapping works. Use `127.0.0.1` for local-only.

### Protocol Configuration

Each asset's `protocols` array supports both Modbus TCP and Modbus RTU. A single device can expose multiple protocols simultaneously.

**Modbus TCP:**
```json
{ "kind": "modbus_tcp", "ip": "0.0.0.0", "port": 55001, "unit_id": 1, "register_map": "bess_modbus.yaml" }
```

**Modbus RTU:**
```json
{ "kind": "modbus_rtu", "port": "/tmp/dertwin_device", "baudrate": 9600, "parity": "N", "stopbits": 1, "unit_id": 1, "register_map": "bess_modbus.yaml" }
```

RTU parameters `baudrate`, `parity`, `stopbits`, `bytesize`, and `timeout` all have sensible defaults (9600/N/1/8/1.0) and can be omitted.

**Register map fields:**

| Field | Required | Description |
|---|---|---|
| `name` | yes | Human-readable label, used in logs and the EMS client |
| `internal_name` | yes | Maps to the device's internal telemetry or command field — must match the attribute name in the corresponding telemetry class (see [`dertwin/telemetry/README.md`](dertwin/telemetry/README.md)) |
| `address` | yes | Modbus register address |
| `type` | yes | `uint16`, `int16`, `uint32`, `int32` |
| `scale` | yes | Multiplier applied on read, divisor applied on write |
| `count` | yes | Number of registers (1 for 16-bit, 2 for 32-bit) |
| `func` | yes | Function code: `0x04` input read, `0x03` holding read, `0x06` single write, `0x10` multi-register write |
| `direction` | yes | `read` or `write` |
| `unit` | no | Physical unit label (V, kW, Hz, etc.) |
| `description` | no | Free-text note |
| `options` | no | Enum mapping for status/mode registers |

`name` and `internal_name` can differ — `name` is what the EMS client sees, `internal_name` is what the device simulator uses internally. For example, `on_grid_power_setpoint` (name) maps to `active_power_setpoint` (internal_name) on the BESS device.

For detailed architecture and per-package docs, see [`dertwin/README.md`](dertwin/README.md).

---

## 🔀 Mixed Protocol Example (TCP + RTU)

This example runs a site with BESS on Modbus TCP and PV + energy meter on Modbus RTU. The EMS controls the BESS over TCP and monitors the RTU devices for observability.

### Prerequisites

Install `socat` to create virtual serial port pairs:

```bash
# macOS
brew install socat

# Ubuntu / Debian
sudo apt install socat
```

### Running the example

**Terminal 1** — create virtual serial pairs and start the simulator:

```bash
# Create virtual serial port pairs (simulator <-> EMS client)
socat -d -d pty,raw,echo=0,link=/tmp/dertwin_pv pty,raw,echo=0,link=/tmp/dertwin_pv_client &
socat -d -d pty,raw,echo=0,link=/tmp/dertwin_meter pty,raw,echo=0,link=/tmp/dertwin_meter_client &

# Start the simulator (from repo root)
dertwin -c configs/mixed_protocol_config.json
```

You should see:
```
INFO | Building site: mixed-protocol-site
INFO | Starting Modbus TCP server | 0.0.0.0:55001 | unit=1
INFO | Starting Modbus RTU server | port=/tmp/dertwin_pv | baudrate=9600 | unit=2
INFO | Starting Modbus RTU server | port=/tmp/dertwin_meter | baudrate=9600 | unit=3
INFO | Simulation engine started | step=0.100s
```

**Terminal 2** — run the mixed-protocol EMS:

```bash
cd examples
python main_mixed.py
```

Expected output:
```
[BESS-1] TCP connected
[PV] RTU connected
[METER] RTU connected
[BESS-1] Starting in CHARGE mode

[EMS] Mixed-protocol EMS running
  [BESS-1] RUN  | SOC= 50.0% | P= -30.00 kW | MODE=charge
  [PV]    P= 18.50 kW (producing)
  [METER] Grid= -8.50 kW (exporting) | Freq=50.002 Hz | Import=0.0 kWh | Export=2.1 kWh
```

The key point: socat creates a **pair** of linked pseudo-terminals for each connection. The simulator opens one end (`/tmp/dertwin_pv`) and the EMS client opens the other (`/tmp/dertwin_pv_client`). Both sides must use different ends of the pair.

If RTU serial ports are unavailable, the EMS will still run with BESS-only control — PV and meter telemetry will show as unavailable.

---

## 🐳 Docker

```bash
# Generate docker-compose.yml from any config
python generate_compose.py configs/full_site_config.json

# Ports are read automatically from the config — no manual editing
docker compose up --build

# Override config at runtime without rebuilding
docker run \
  -v /path/to/my/configs:/app/configs:ro \
  -e CONFIG_PATH=/app/configs/my_site.json \
  -p 55001:55001 \
  dertwin-simulator
```

---

## 🧪 Tests

```bash
pytest
```

The test suite covers device physics, register encoding, external models, protocol parity (TCP and RTU), mixed-protocol engine integration, and full end-to-end site integration via Modbus. See `tests/` for structure.

---

## 📈 Roadmap

- [ ] Scenario engine — scripted event sequences
- [ ] REST API + web dashboard
- [ ] IEC 61850 support
- [ ] MQTT integration
- [x] Modbus RTU support
- [x] Mixed-protocol sites (TCP + RTU)
- [x] Published PyPI package

---

## 🧠 Use Cases

- EMS algorithm development and validation
- SCADA/HMI integration testing
- Protocol compliance testing (TCP and RTU)
- DER fleet orchestration prototyping
- Frequency and voltage response simulation
- Mixed-protocol site simulation

---

## 🤝 Contributing

Contributions are welcome. Before diving in, read [`dertwin/README.md`](dertwin/README.md) — it covers the simulator architecture, how devices are modeled, the engine and clock design, and how to add new device types or protocols.

See [CONTRIBUTING.md](CONTRIBUTING.md) for full guidelines, including how to add new protocols and test RTU without hardware.

---

## 📜 License

MIT License

---

## 👤 Author

Oleksandr Spivak