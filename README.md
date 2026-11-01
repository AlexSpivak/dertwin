# DER Twin

Digital Twin infrastructure for modern energy systems.

**DER Twin** is a lightweight simulator for Distributed Energy Resources (DER) — BESS, PV inverters, CHP units, energy meters, and grid models — exposed via Modbus TCP and Modbus RTU. Use it for EMS development, protocol testing, integration validation, and control algorithm sandboxing without touching real hardware.

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

`generate_compose.py` reads the config and generates a `docker-compose.yml` with the correct ports automatically. No manual port configuration needed. For mixed-protocol configs with RTU, see [Docker with RTU](#docker-with-rtu) below.

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

For a full multi-device site (dual BESS + PV + CHP + energy meter + external models):

```bash
python -m dertwin.main -c configs/full_site_config.json
# in another terminal:
python examples/main_full.py
```

You'll see the CHP go through its realistic startup sequence (~2 minutes) before reaching `RUNNING` and dispatching power, while the BESS units cycle and the energy meter aggregates the full site balance.

For a mixed-protocol site (BESS on TCP + PV and meter on RTU), see [Mixed Protocol Example](#-mixed-protocol-example-tcp--rtu) below.

---

## 🔁 Dynamic Asset Membership

In addition to config-driven site definitions, DERTwin's `SiteController` supports adding and removing assets at runtime. Use this for scenario scripts ("at t=300s, a new PV inverter comes online"), live demos, and EMS-driven sites where the asset roster is decided by an external controller.

```python
from dertwin.controllers.site_controller import SiteController
import asyncio

# Build an empty site
site = SiteController({
    "site_name": "demo",
    "step": 0.1,
    "real_time": True,
    "register_map_root": "configs/register_maps",
    "assets": [],
})
site.build()
asyncio.create_task(site.start())

# Add an asset live — protocol server starts immediately, site power model picks it up next tick
await site.add_asset({
    "asset_id": "bess-01",
    "type": "bess",
    "ip": "127.0.0.1",
    "port": 55001,
    "unit_id": 1,
    "capacity_kwh": 200.0,
    "initial_soc": 50.0,
})

# ...later, remove it
await site.remove_asset("bess-01")
```

Key properties:
- The site power model captures device lists by reference, so dynamically added generators show up in the energy meter's grid balance on the next tick — no rebuild required.
- `add_asset` is idempotent: re-registering an existing `asset_id` is a no-op.
- `remove_asset` cancels the protocol task and shuts down the server cleanly; the engine keeps running for the remaining assets.
- See [`dertwin/controllers/README.md`](dertwin/controllers/README.md) for the full API and the two supported configuration shapes (legacy `protocols: [...]` for multi-protocol use, flat spec for single-TCP-endpoint runtime use).

---

## 🧱 Features

- Async Modbus TCP and RTU servers built on `pymodbus`
- Mixed-protocol support — TCP and RTU devices on the same site
- Full 16-bit Modbus address space on all four datastores (discrete inputs, coils, input registers, holding registers)
- Per-register function code routing — FC02 discrete inputs for binary flags, FC03/04 for analog telemetry
- Per-register endianness — big-endian default, little-endian for Sungrow/Carlo Gavazzi-style devices
- Realistic CHP simulation — MWM TEM Evolution-compatible state machine, configurable startup timings, thermal physics, heat output
- Config-driven site topology — add devices by editing JSON
- Runtime asset management — `add_asset` / `remove_asset` for live scenario scripting and EMS-driven rosters
- Hard SOC limits on BESS assets — SOC lands exactly on the limit, then power reads zero while the setpoint stays as written
- Optional SOC persistence across restarts
- Location- and date-aware clear-sky irradiance — correct sunrise, sunset and seasonal peak for any latitude and day of the year
- Stochastic cloud cover with persistent day-to-day weather regimes, seedable and reproducible
- Plane-of-array irradiance for tilted, oriented PV arrays, with DC/AC ratio and inverter clipping
- Irradiance from a CSV, for replaying measured data
- Stochastic site load — daily profile, weekday/weekend factor, slow mean-reverting drift and fast noise, all seedable
- Ambient temperature, grid frequency, and grid voltage models
- Multi-device support across independent ports
- External model events (voltage sags, frequency deviations)
- Simulation start time control (`start_time_h`) — start at noon, peak load, etc.
- Docker support with auto-generated Compose files and RTU-over-TCP bridging
- Deterministic simulation with seeded random models
- Fully tested with `pytest`

---

## 📦 Repo Structure

```
dertwin/
├── configs/
│   ├── register_maps/              # Modbus register definitions (YAML)
│   ├── simple_config.json          # Single BESS — good starting point
│   ├── demo_config.json            # BESS + PV + meter
│   ├── full_site_config.json       # BESS + BESS + PV + CHP + meter + external models
│   └── mixed_protocol_config.json  # BESS (TCP) + PV (RTU) + meter (RTU)
├── dertwin/
│   ├── core/                # Clock, engine, register map loader
│   ├── controllers/         # Site and device orchestration (incl. dynamic add/remove)
│   ├── devices/             # BESS, PV, CHP, energy meter, external models
│   ├── protocol/            # Modbus TCP + RTU servers
│   ├── telemetry/           # Telemetry dataclasses
│   └── main.py
├── examples/
│   ├── simple/              # Single BESS EMS example
│   ├── full/                # Multi-device EMS example with CHP (TCP)
│   ├── mixed/               # Mixed-protocol EMS example (TCP + RTU)
│   └── protocol/            # Shared Modbus TCP and RTU clients
├── tests/                   # Full test suite
├── generate_compose.py      # Docker Compose generator
├── docker-entrypoint.sh     # Container entrypoint (socat + RTU bridge)
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
    },
    {
      "type": "chp",
      "rated_kw": 4000.0,
      "heat_to_power_ratio": 1.0,
      "min_load_percent": 30.0,
      "max_load_percent": 110.0,
      "protocols": [{ "kind": "modbus_tcp", "ip": "0.0.0.0", "port": 55002, "unit_id": 1, "register_map": "chp_modbus.yaml" }]
    }
  ]
}
```

**`real_time: true`** — engine runs its own loop, use for `dertwin` CLI and EMS examples
**`real_time: false`** — caller drives the clock via `step_once()`, use for tests
**`sim_start_utc`** — absolute UTC instant the simulation starts from, ISO 8601 (e.g. `"2025-10-31T00:00:00Z"`). Internal time is always UTC. Required by the clear-sky irradiance model, which has no sun position without a date. Omit it and the clock sits at the Unix epoch, so `start_time_h` keeps its original meaning.
**`start_time_h`** — offsets the clock on startup (e.g. `12.0` for noon), relative to `sim_start_utc`'s date. All external models start from this time.
**`data_root`** — path for CSV inputs (`irradiance.series_csv`), resolved relative to the config file. Defaults to the working directory.
**`register_map_root`** — path to register map directory, resolved relative to the working directory where you run `dertwin`
**`state_root`** — path for device state files (`state_file` on a BESS asset), resolved relative to the config file. Defaults to the working directory.
**`data_root`** — path for CSV inputs (`load.profile.csv`, irradiance series), resolved relative to the config file. Defaults to the working directory.
**`ip: "0.0.0.0"`** — required when running inside Docker so port mapping works. Use `127.0.0.1` for local-only.

Unknown asset keys are rejected at build time with a "did you mean" hint, so a typo fails loudly instead of being silently ignored.

### Supported asset types

| `type` | Class | Notable parameters |
|---|---|---|
| `bess` | `BESSSimulator` | `capacity_kwh`, `initial_soc`, `max_charge_kw`, `max_discharge_kw`, `ramp_rate_kw_per_s`, `soc_min_percent`, `soc_max_percent`, `limit_mode`, `round_trip_eff`, `charge_efficiency`, `discharge_efficiency`, `self_discharge_percent_per_day`, `state_file` |
| `inverter` | `PVSimulator` | `rated_kw` / `ac_rating_kw`, `dc_capacity_kw`, `dc_ac_ratio`, `tilt_deg`, `azimuth_deg`, `albedo`, `derate` / `losses`, `module_efficiency`, `area_m2`, `temp_coefficient`, `noct_c` |
| `chp` | `CHPSimulator` | `rated_kw`, `heat_to_power_ratio`, `min_load_percent`, `max_load_percent` |
| `energy_meter` | `EnergyMeterSimulator` | (no parameters — observes the site power model) |

### BESS SOC limits, efficiency and persistence

The SOC window is defended with a **hard stop**: in the step that would cross a
limit, power is clamped so SOC lands exactly on it, and from then on reported
active power is exactly `0` while the setpoint keeps pushing that way. The
setpoint register keeps whatever the EMS wrote, so dispatch resumes by itself
once the pack has headroom — and the energy counters, the site power balance
and the meter all follow the actual (clamped) power, not the setpoint.

```json
{
  "type": "bess",
  "capacity_kwh": 100.0,
  "initial_soc": 60.0,
  "soc_min_percent": 10.0,
  "soc_max_percent": 95.0,
  "limit_mode": "hard",
  "max_charge_kw": 50.0,
  "max_discharge_kw": 50.0,
  "round_trip_eff": 0.92,
  "self_discharge_percent_per_day": 0.0,
  "state_file": "state/bess-1.json",
  "state_save_interval_s": 60.0
}
```

**`soc_min_percent` / `soc_max_percent`** — the hard window. Omit them and it
follows `soc_limits.lower_2` / `soc_limits.upper_2`, which is also what the
Modbus `soc_lower_limit_2` / `soc_upper_limit_2` commands write, so an EMS can
move the hard limits at runtime.
**`limit_mode`** — `"hard"` (default) or `"taper"` for the pre-0.2 linear
derating ramp between the `_1` and `_2` thresholds.
**`round_trip_eff`** — split as `sqrt()` across charge and discharge.
`charge_efficiency` / `discharge_efficiency` override it per direction.
**`state_file`** — opt-in SOC persistence. Omit it and every start is a cold
start at `initial_soc`. When set, SOC and the lifetime counters are written
atomically on an interval and on shutdown, and restored on the next start; the
first boot, a missing file or an unusable one falls back to `initial_soc` and
logs why.

### Solar irradiance

Three models, selected by `external_models.irradiance.model`:

| `model` | Behaviour |
|---|---|
| `sinusoidal` | The original fixed-shape day: a sine between a configured sunrise and sunset, identical every day of the year everywhere on earth. **The default**, so existing configs are untouched. |
| `clearsky` | Real solar geometry for the date and location, times a stochastic cloud index. Selected automatically as soon as you give a `latitude`. |
| `series` | GHI replayed from a CSV. |

```json
{
  "sim_start_utc": "2025-10-31T00:00:00Z",
  "external_models": {
    "irradiance": {
      "latitude": 59.3293,
      "longitude": 18.0686,
      "timezone": "Europe/Stockholm",
      "altitude_m": 44.0,
      "clearsky_model": "ineichen",
      "linke_turbidity": 3.0,
      "cloud": {
        "enabled": true,
        "model": "ar1",
        "mean_kt": 0.48,
        "phi": 0.65,
        "sigma_day": 0.9,
        "kt_min": 0.08,
        "intraday_sigma": 0.07,
        "intraday_tau_s": 900.0,
        "seed": 59
      }
    }
  },
  "assets": [
    {
      "type": "inverter",
      "ac_rating_kw": 30.0,
      "dc_capacity_kw": 36.0,
      "tilt_deg": 45.0,
      "azimuth_deg": 180.0,
      "derate": 0.86
    }
  ]
}
```

**`latitude` / `longitude`** — degrees, longitude positive east. Giving a
latitude is what switches the model on.
**`timezone`** — label only, for logs and examples. Internal time is always UTC.
**`clearsky_model`** — `ineichen` (default, adds altitude and turbidity),
`haurwitz` (elevation only), or `pvlib` (needs `pip install "dertwin[pvlib]"`).
**`linke_turbidity`** — ~2 very clear, ~3 clear continental, ~5–6 hazy summer.
**`cloud`** — day-to-day weather regimes as an AR(1) process plus intraday
Ornstein-Uhlenbeck variability. `phi` sets how long overcast spells last,
`mean_kt` the long-run clear-sky fraction. On by default once a location is
given; set `seed` for a reproducible run or `enabled: false` for a cloudless
sky.
**`series_csv`** — replay recorded data. With `series_column: "kt"` it drives
the cloud index and keeps the clear-sky curve; otherwise it is absolute GHI in
W/m².

What this buys, at Stockholm (59.33 N):

| Date | Daylight | Clear-sky GHI peak |
|---|---|---|
| 21 Jun | 18.3 h | ~819 W/m² |
| 31 Oct | 8.7 h | ~256 W/m² |
| 21 Dec | 5.7 h | ~93 W/m² |

The `sinusoidal` model gives all three 12.0 h and 1000 W/m².

### PV array orientation

`tilt_deg` and `azimuth_deg` (clockwise from true north; 180 is due south)
enable plane-of-array transposition. Without a tilt, irradiance is taken as
already in-plane — the pre-0.2 behaviour. `dc_capacity_kw` (or `dc_ac_ratio`)
oversizes the array relative to `ac_rating_kw` so the inverter actually clips,
as real plants do; leave both out and DC/AC stays 1.0 and nothing ever clips.
See [`dertwin/devices/pv/README.md`](dertwin/devices/pv/README.md).
### Site load

The site load is multiplicative, so each piece can be configured without
fighting the others:

```
load_kw = base_kw × profile(hour) × weekday × drift × (1 + noise)
```

```json
{
  "external_models": {
    "load": {
      "base_kw": 50.0,
      "profile": {
        "model": "hourly",
        "hourly": [0.55, 0.52, 0.50, 0.50, 0.52, 0.60, 0.75, 0.95,
                   1.05, 1.05, 1.00, 0.98, 0.95, 0.95, 0.98, 1.05,
                   1.20, 1.35, 1.30, 1.15, 1.00, 0.85, 0.70, 0.60],
        "interpolate": true,
        "normalize": true
      },
      "weekday_factor": 1.0,
      "weekend_factor": 0.75,
      "start_weekday": "monday",
      "drift": { "sigma": 0.12, "tau_s": 10800.0, "min_factor": 0.7, "max_factor": 1.3 },
      "noise": { "sigma": 0.02, "tau_s": 30.0 },
      "min_kw": 0.0,
      "max_kw": null,
      "seed": 7
    }
  }
}
```

**`base_kw`** — the *average* load. The profile is normalised to a daily mean of
1.0, so changing the shape never moves the average.
**`profile.model`** — `flat` (default, the pre-0.2 constant), `hourly` (24
factors), `parametric` (a night baseline with morning and evening bumps), or
`csv` (an hour-of-day table at any resolution; a `kw` column sets `base_kw`
from its mean).
Hourly factors sit at their **hour centres** and interpolate circularly, so
there is no discontinuity at midnight.
**`weekend_factor`** — applied on Saturday and Sunday. The clock is an offset
from the start of the run rather than a date, so `start_weekday` places the week
and it advances at each simulated midnight.
**`drift`** — slow mean-reverting random walk, the "this week is busier than
usual" term. `sigma` is the standard deviation of the multiplicative factor and
`tau_s` its correlation time; bounded by `min_factor` / `max_factor`.
**`noise`** — the same process on a timescale of seconds.
**`seed`** — one seed drives both random terms. Drift and noise are off unless
configured.

The legacy `external_models.power.base_load_w` still works and is still a flat
load; a `load` block supersedes it, and a `load` block with no `base_kw`
inherits its value.
### Migrating from 0.1.x

Every existing config key still works and no config in `configs/` needed
editing. Irradiance in particular is unchanged until you ask for more: the
`sinusoidal` model stays the default, and the date- and location-aware one
switches on only once you give a `latitude`.

Two BESS behaviours did change:

- **SOC limits no longer taper.** `soc_limits.lower_2` / `upper_2` are now hard
  stops that SOC lands on exactly; `lower_1` / `upper_1` are accepted and
  ignored. Set `"limit_mode": "taper"` per asset to keep the old ramp.
- **`round_trip_eff` now has an effect.** The pre-0.2 model multiplied by the
  efficiency on both the charge and discharge leg, so the realised round-trip
  efficiency was always 1.0 no matter what was configured. Charging now stores
  less than arrives at AC and discharging drains more than reaches AC, so a
  cycle costs what the config says it costs.

Also fixed: `service_current` now reports amps (it reported kW into an amps
register), and reactive/apparent power are derived from the clamped power
instead of the pre-clamp setpoint.

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
| `address` | yes | Modbus register address (0–65535) |
| `type` | yes | `uint16`, `int16`, `uint32`, `int32` |
| `scale` | yes | Multiplier applied on read, divisor applied on write |
| `count` | yes | Number of registers (1 for 16-bit, 2 for 32-bit) |
| `func` | yes | Function code: `0x02` discrete input read, `0x03` holding read, `0x04` input read, `0x06` single write, `0x10` multi-register write |
| `direction` | yes | `read` or `write` |
| `endian` | no | `big` (default) or `little` — for devices like Sungrow BESS and Carlo Gavazzi meters that use little-endian 32-bit register layout |
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
INFO | Starting Modbus RTU server | port=/tmp/dertwin_pv | baudrate=9600 | unit=1
INFO | Starting Modbus RTU server | port=/tmp/dertwin_meter | baudrate=9600 | unit=1
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
  [PV]    P= 16.40 kW (producing)
  [METER] Grid= +23.57 kW (importing) | Freq=50.000 Hz | Import=0.1 kWh | Export=0.0 kWh
```

The key point: socat creates a **pair** of linked pseudo-terminals for each connection. The simulator opens one end (`/tmp/dertwin_pv`) and the EMS client opens the other (`/tmp/dertwin_pv_client`). Both sides must use different ends of the pair.

If RTU serial ports are unavailable, the EMS will still run with BESS-only control — PV and meter telemetry will show as unavailable.

---

## 🐳 Docker

### TCP-only configs

For configs that only use Modbus TCP, Docker setup is straightforward:

```bash
python generate_compose.py configs/full_site_config.json
docker compose up --build
```

TCP ports are mapped automatically from the config. Connect your EMS to `localhost:<port>`.

### Docker with RTU

RTU serial devices use pseudo-terminals (`/dev/pts/`) which exist only inside the container's kernel namespace and can't be accessed from the host via volume mounts. The entrypoint solves this by bridging each RTU serial port to a TCP port inside the container using a Python asyncio relay. From the host, `socat` converts the TCP connection back into a local PTY that the EMS opens as a normal serial port.

**Step 1** — generate the compose file and start the container:

```bash
python generate_compose.py configs/mixed_protocol_config.json
docker compose up --build
```

You should see:
```
[entrypoint] Creating serial pair: /tmp/dertwin_pv <-> /tmp/dertwin_pv_bridge
[entrypoint] Bridging /tmp/dertwin_pv_bridge -> TCP port 56001
[bridge] /tmp/dertwin_pv_bridge <-> TCP :56001
[entrypoint] Creating serial pair: /tmp/dertwin_meter <-> /tmp/dertwin_meter_bridge
[entrypoint] Bridging /tmp/dertwin_meter_bridge -> TCP port 56002
[bridge] /tmp/dertwin_meter_bridge <-> TCP :56002
[entrypoint] RTU bridges ready
[entrypoint] Starting DERTwin simulator
INFO | Starting Modbus TCP server | 0.0.0.0:55001 | unit=1
INFO | Starting Modbus RTU server | port=/tmp/dertwin_pv | baudrate=9600 | unit=1
INFO | Starting Modbus RTU server | port=/tmp/dertwin_meter | baudrate=9600 | unit=1
```

`generate_compose.py` detects RTU ports in the config and automatically exposes the bridge TCP ports (56001, 56002, ...) alongside the regular Modbus TCP ports in the generated `docker-compose.yml`.

**Step 2** — in a separate terminal on the host, create local PTY endpoints (requires `socat`):

```bash
socat pty,raw,echo=0,link=/tmp/dertwin_pv_client tcp:localhost:56001 &
socat pty,raw,echo=0,link=/tmp/dertwin_meter_client tcp:localhost:56002 &
```

This creates `/tmp/dertwin_pv_client` and `/tmp/dertwin_meter_client` on the host — the same paths the EMS expects.

**Step 3** — run the EMS (same command as the local setup):

```bash
cd examples
python main_mixed.py
```

The EMS uses the exact same config for both local and Docker setups — the serial paths (`/tmp/dertwin_pv_client`, `/tmp/dertwin_meter_client`) are identical. The only difference is how those paths are created: locally via direct socat pairs, or via Docker with TCP bridging.

### Override config at runtime

```bash
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

The test suite covers device physics (BESS, PV, CHP, energy meter), solar geometry and clear-sky irradiance against published reference values, cloud-model statistics, the statistics of the stochastic site load, register encoding with per-register endianness, FC02 discrete input routing, external models, protocol parity (TCP and RTU), mixed-protocol engine integration, runtime asset add/remove, and full end-to-end site integration via Modbus. See `tests/` for structure.

The built-in solar model is also cross-checked against `pvlib`, which is
optional, so that suite skips unless you install it:

```bash
pip install "dertwin[pvlib]"
```

---

## 📈 Roadmap

- [ ] Scenario engine — scripted event sequences
- [x] Dynamic asset add/remove at runtime
- [x] CHP support with realistic state machine
- [x] Modbus FC02 discrete input support
- [x] Per-register endianness (big/little)
- [x] Modbus RTU support
- [x] Mixed-protocol sites (TCP + RTU)
- [x] Docker RTU-over-TCP bridging
- [x] Published PyPI package

---

## 🧠 Use Cases

- EMS algorithm development and validation
- SCADA/HMI integration testing
- Protocol compliance testing (TCP and RTU)
- DER fleet orchestration prototyping
- Frequency and voltage response simulation
- Mixed-protocol site simulation
- CHP dispatch and startup-sequence testing against EMS
- Live scenario scripting — bring assets online and offline during a simulation
- EMS-in-the-loop tests where the asset roster is decided by the controller under test

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