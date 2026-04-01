# Contributing to DER Twin

Thanks for your interest in contributing. DER Twin is an open simulation platform for energy systems — contributions that improve physics realism, add protocols, extend device models, or improve developer experience are all welcome.

---

## Before You Start

Read [`dertwin/README.md`](dertwin/README.md) before touching code. It covers the simulator architecture, the engine and clock design, how devices are modeled, and how the protocol layer is separated from physics. Understanding those boundaries will save you time.

---

## How to Contribute

### Reporting Bugs

Open an issue with:
- What you ran and what config you used
- What you expected to happen
- What actually happened, including logs if relevant
- Python version and OS

### Suggesting Features

Open an issue describing the use case, not just the feature. "I need voltage sag injection to test my EMS fault response" is more useful than "add voltage sag". This helps prioritize and design the right solution.

### Submitting Code

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Add or update tests — PRs without tests will be asked to add them
4. Run the full test suite and make sure everything passes
5. Open a pull request with a clear description of what changed and why

---

## Development Setup

```bash
git clone https://github.com/AlexSpivak/dertwin.git
cd dertwin
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Run tests:
```bash
pytest
```

For RTU-related development, install `socat` to create virtual serial pairs (see [Testing RTU](#testing-rtu) below).

---

## Architecture Boundaries

DER Twin has a strict separation between layers. Please keep it that way:

- **Protocol layer** — exposes registers over Modbus (TCP or RTU). Never modifies device state directly.
- **Controller layer** — bridges protocol commands to device logic. Owns the read/write mapping. Transport-agnostic — works identically with TCP and RTU protocols.
- **Device layer** — owns physics. Never knows about protocols.
- **Engine** — owns time. All devices step together on each tick.
- **External models** — provide environmental inputs (irradiance, temperature, grid). Devices read from them, never write to them.

If a PR mixes these concerns it will be asked to refactor.

---

## Adding a New Device Type

1. Create a simulator class in `dertwin/devices/<your_device>/simulator.py` implementing `step(dt)` and `get_telemetry()`
2. Create a telemetry dataclass in `dertwin/telemetry/`
3. Add a register map YAML in `configs/register_maps/`
4. Wire it into `SiteController._create_device()` in `dertwin/controllers/site_controller.py`
5. Add unit tests in `tests/devices/<your_device>/`
6. Add an integration test in `tests/controllers/test_site_controller.py`

---

## Adding a New Protocol

The protocol layer is designed for extension. Both `ModbusTCPSimulator` and `ModbusRTUSimulator` follow the same interface contract — any protocol that exposes `.context`, `.unit_id`, `run_server()`, and `shutdown()` can be plugged into `DeviceController` without changes.

To add a new protocol:

1. Implement the protocol class in `dertwin/protocol/` with the standard interface: `.context` (register datastore), `.unit_id`, `async run_server()`, `async shutdown()`
2. Add a routing branch in `SiteController._create_protocol()` for the new `kind` string
3. Ensure `shutdown()` handles startup failures gracefully (catch `Exception`, not just `CancelledError`)
4. Add register-level unit tests in `tests/protocol/`
5. Add integration tests in `tests/controllers/` covering the new protocol with `DeviceController` and `SimulationEngine`
6. If applicable, add an EMS client wrapper in `examples/protocol/` following the `SimpleModbusClient` / `SimpleModbusRTUClient` pattern

---

## Testing RTU

RTU tests that only exercise the register datastore (encode/decode, telemetry writes, command collection) don't need serial hardware — they use `/dev/null` as the port and never call `run_server()`.

For end-to-end RTU testing with actual serial communication, use `socat` to create virtual serial port pairs:

```bash
# Install socat
# macOS:
brew install socat
# Ubuntu/Debian:
sudo apt install socat

# Create a virtual serial pair
socat -d -d pty,raw,echo=0,link=/tmp/dertwin_device pty,raw,echo=0,link=/tmp/dertwin_client &
```

This gives you two linked pseudo-terminals — point the simulator at `/tmp/dertwin_device` and the client at `/tmp/dertwin_client`. Both sides must use different ends of the pair.

---

## Code Style

- Python 3.11+
- No external formatter enforced yet, but follow the existing style — snake_case, type hints on public methods, docstrings on classes
- Keep physics logic out of controllers and protocols
- Prefer explicit over clever

---

## Releasing

For maintainers. The release flow:

```bash
# 1. Bump version in pyproject.toml
# 2. Commit the bump
git add pyproject.toml
git commit -m "bump version to 0.x.x"

# 3. Tag and push
git tag v0.x.x
git push origin main
git push origin v0.x.x

# 4. Build and publish to PyPI
python -m build
twine upload dist/*

# 5. Create a GitHub release from the tag with a short changelog
```

---

## Questions

Open an issue or start a discussion on GitHub. There's no mailing list or chat yet.