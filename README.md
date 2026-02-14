# DER Twin
```markdown
Digital Twin infrastructure for modern energy systems.
```
**DER Twin** is a lightweight Digital Twin simulator for Distributed Energy Resources (DER), built for protocol testing, integration validation, and control algorithm development.

It simulates energy devices (BESS, inverters, grid frequency, meters, etc.) and exposes them via industrial protocols like Modbus TCP.

Designed for engineers building and validating modern energy control systems.

---

## 🚀 Why DER Twin?

Modern energy systems require:

- Realistic device simulations
- Protocol-level validation
- Fast integration testing
- Control algorithm sandboxing

DER Twin allows you to run multiple simulated devices locally and interact with them as if they were real hardware.

---

## 🧱 Current Features

- Async Modbus TCP server (built on pymodbus)
- Config-driven register mapping
- Device simulation loop (high-frequency updates)
- Command handling via holding registers
- Centralized structured logging
- Multi-device support (multiple ports)

---

## 📦 Project Structure

```
configs/ # YAML and JSON configuration
dertwin/
├── devices/ # Device simulation logic
├── protocol/ # Modbus server implementation
├── logging_config.py
├── main.py
tests/ # Unit tests
```
## 🛠 Installation

```bash
git clone https://github.com/<your-username>/dertwin.git
cd dertwin
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## ▶️ Running the Simulator
```commandline
python -m dertwin.main
```
You should see logs like:
```
Modbus device started | port=5021 | unit_id=1
Server listening.
```

## ⚙️ Configuration
Device register mappings are config-driven.

Each register definition includes:

- name
- address
- type (uint16, int16, uint32, int32)
- scale
- count
- function code

Example:
```yaml
  # System Info
  - address: 32000
    name: service_voltage
    func: 0x04
    type: uint16
    count: 1
    scale: 0.1
    unit: V
```

## 🧪 Testing
```commandline
pytest
```

## 📈 Roadmap (v1)

- Scenario engine (event-based simulation)
- REST API
- Web UI dashboard
- Docker image
- IEC 61850 (future)
- MQTT integration

## 🧠 Use Cases

- SCADA integration testing
- EMS algorithm validation
- Frequency response simulation
- DER fleet orchestration prototyping
- Protocol compliance testing

## 📜 License
MIT License

## 👤 Author
Oleksandr Spivak