# DER Twin

Digital Twin infrastructure for modern energy systems.

**DER Twin** is a lightweight Digital Twin simulator for Distributed Energy Resources (DER), designed for protocol testing, integration validation, and control algorithm development.  
It simulates energy devices (BESS, PV inverters, grid frequency, energy meters, etc.) and exposes them via industrial protocols like Modbus TCP.

Designed for engineers and researchers building and validating modern energy control systems.

---

## 🚀 Why DER Twin?

Modern energy systems require:

- Realistic device simulations
- Protocol-level validation
- Fast integration testing
- Control algorithm sandboxing

DER Twin allows you to run multiple simulated devices locally and interact with them as if they were real hardware.

---

## 🧱 Features

- Async Modbus TCP server (built on `pymodbus`)
- Config-driven register mapping
- High-frequency device simulation loop
- Command handling via holding registers
- Centralized structured logging
- Multi-device support across multiple ports
- Deterministic simulation for reproducible results

---

## 📦 Repo Structure
```markdown
configs/ # JSON/YAML site configurations + register maps
dertwin/ # Core simulator packages and modules
README.md # Package-level documentation
core/ # Clock, engine, device abstractions, register maps
controllers/ # Device and site orchestration
devices/ # Device simulation models: BESS, PV, Energy Meter, External world
telemetry/ # Telemetry definitions and helpers
protocol/ # Modbus TCP server implementation
main.py # Simulator entry point
logging_config.py # Centralized logging setup
tests/ # Unit tests for all packages
examples/ # (planned) EMS-in-the-loop examples
.gitignore
LICENSE
pyproject.toml
README.md
```

For detailed architecture, design, and per-package usage, see [`dertwin/README.md`](dertwin/README.md).

---

## 🛠 Installation

```bash
git clone https://github.com/<your-username>/dertwin.git
cd dertwin
python -m venv .venv
source .venv/bin/activate
pip install -e .
```
---
## ▶️ Running the Simulator
```bash
python -m dertwin.main -c configs/demo_config.json
```
You should see logs like:
```markdown
2026-03-02 14:42:03 | INFO     | dertwin.logging_config | Logging initialized | level=INFO
2026-03-02 14:42:03 | INFO     | dertwin.controllers.site_controller | Building site: local-dev-site
2026-03-02 14:42:03 | INFO     | dertwin.controllers.site_controller | Starting site runtime
2026-03-02 14:42:03 | INFO     | dertwin.protocol.modbus | Starting Modbus server | 127.0.0.1:55001 | unit=1
```

---
## ⚙️ Configuration

Device register mappings are config-driven.
Each register definition includes:

- name
- internal_name
- address
- type (uint16, int16, uint32, int32)
- scale
- count
- function code (0x03 read, 0x04 input read, etc.)
- Optional unit and description

Example:
```markdown
# System Info
- address: 32000
  name: service_voltage
  internal_name: service_voltage
  func: 0x04
  type: uint16
  count: 1
  scale: 0.1
  unit: V
```

---
## 🧪 Running Tests
```bash
pytest
```
All packages are fully tested under /tests/.

--- 

## 📈 Roadmap (v1)
- Scenario engine (event-driven simulation)
- REST API & Web dashboard
- Dockerized simulator image
- IEC 61850 integration (future)
- MQTT integration
- EMS-in-the-loop examples (/examples/)

---
## 🧠 Use Cases

- SCADA integration testing
- EMS algorithm validation
- DER fleet orchestration prototyping
- Frequency response simulations
- Protocol compliance and conformance testing

---
## 📜 License
MIT License

---
## 👤 Author
Oleksandr Spivak