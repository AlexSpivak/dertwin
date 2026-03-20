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

---

## Architecture Boundaries

DER Twin has a strict separation between layers. Please keep it that way:

- **Protocol layer** — exposes registers over Modbus (or future protocols). Never modifies device state directly.
- **Controller layer** — bridges protocol commands to device logic. Owns the read/write mapping.
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