# DER Twin

Digital Twin infrastructure for modern energy systems.

**DER Twin** is a lightweight simulator for Distributed Energy Resources (DER) — BESS, PV inverters, energy meters, and grid models — exposed via Modbus TCP. Use it for EMS development, protocol testing, integration validation, and control algorithm sandboxing without touching real hardware.

---

## ⚡ Quickstart

### Option A — Run locally

```bash
git clone https://github.com/AlexSpivak/dertwin.git
cd dertwin
python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m dertwin.main -c configs/simple_config.json
```

You should see:
```
2026-03-05 12:00:00 | INFO | dertwin.controllers.site_controller | Building site: local-dev-site
2026-03-05 12:00:00 | INFO | dertwin.protocol.modbus | Starting Modbus server | 0.0.0.0:55001 | unit=1
2026-03-05 12:00:00 | INFO | dertwin.core.engine | Simulation engine started | step=0.100s
```

The simulator is now accepting Modbus TCP connections on port `55001`.

### Option B — Run with Docker

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

---

## 🧱 Features

- Async Modbus TCP server built on `pymodbus`
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
│   ├── register_maps/       # Modbus register definitions (YAML)
│   ├── simple_config.json   # Single BESS — good starting point
│   ├── demo_config.json     # Full three-device site
│   └── full_site_config.json# Dual BESS + PV + meter + external models
├── dertwin/
│   ├── core/                # Clock, engine, register map loader
│   ├── controllers/         # Site and device orchestration
│   ├── devices/             # BESS, PV, energy meter, external models
│   ├── protocol/            # Modbus TCP server
│   └── telemetry/           # Telemetry dataclasses
├── examples/
│   ├── simple/              # Single BESS EMS example
│   ├── full/                # Multi-device EMS example
│   └── protocol/            # Shared Modbus client
├── tests/                   # Full test suite
├── generate_compose.py      # Docker Compose generator
├── Dockerfile
└── main.py
```

---

## ⚙️ Configuration

Sites are defined in JSON. Each asset declares its type, parameters, and Modbus protocol binding:

```json
{
  "site_name": "my-site",
  "step": 0.1,
  "real_time": true,
  "start_time_h": 12.0,
  "register_map_root": "configs/register_maps",
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

**`real_time: true`** — engine runs its own loop, use for `main.py` and EMS examples  
**`real_time: false`** — caller drives the clock via `step_once()`, use for tests  
**`start_time_h`** — sets simulation clock on startup (e.g. `12.0` for noon). All external models start from this time.  
**`ip: "0.0.0.0"`** — required when running inside Docker so port mapping works. Use `127.0.0.1` for local-only.

Register map fields: `name`, `address`, `type` (uint16/int16/uint32/int32), `scale`, `count`, `func` (0x03/0x04/0x06/0x10).

For detailed architecture and per-package docs, see [`dertwin/README.md`](dertwin/README.md).

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

The test suite covers device physics, register encoding, external models, and full end-to-end site integration via Modbus TCP. See `tests/` for structure.

---

## 📈 Roadmap

- [ ] Scenario engine — scripted event sequences
- [ ] REST API + web dashboard
- [ ] IEC 61850 support
- [ ] MQTT integration
- [ ] Published PyPI package

---

## 🧠 Use Cases

- EMS algorithm development and validation
- SCADA/HMI integration testing
- Protocol compliance testing
- DER fleet orchestration prototyping
- Frequency and voltage response simulation

---

## 🤝 Contributing

Contributions are welcome. Before diving in, read [`dertwin/README.md`](dertwin/README.md) — it covers the simulator architecture, how devices are modeled, the engine and clock design, and how to add new device types or protocols.

---

## 📜 License

MIT License

---

## 👤 Author

Oleksandr Spivak