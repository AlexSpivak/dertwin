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
- Battery thermal management — a managed container holds the cells inside a setpoint band, so a cold site keeps full capability; configurable temperature derate curve and optional HVAC auxiliary draw on the meter
- Location- and date-aware clear-sky irradiance — correct sunrise, sunset and seasonal peak for any latitude and day of the year
- Stochastic cloud cover with persistent day-to-day weather regimes — seeded, a date's weather is reproducible *per date*, so restarts do not replay the same sky
- Plane-of-array irradiance for tilted, oriented PV arrays, with DC/AC ratio and inverter clipping
- Irradiance from a CSV, for replaying measured data
- Stochastic site load — daily profile, weekday/weekend factor, slow mean-reverting drift keyed on simulated time, and fast noise, all seedable
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
│   ├── mixed_protocol_config.json  # BESS (TCP) + PV (RTU) + meter (RTU)
│   ├── stockholm_site_config.json  # Northern Europe, 59.33 N — clear-sky PV, clouds, stochastic load
│   └── kyiv_site_config.json       # Ukraine, 50.45 N — same code, different place
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
│   ├── seasonal/            # Print a simulated day from a location-aware config
│   └── protocol/            # Shared Modbus TCP and RTU clients
├── state/                   # BESS SOC state files (gitignored)
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

### Location-aware example sites

Two complete sites, config-only — nothing in `dertwin/` knows about either
place, and a test guards that:

| Config | Place | Plant |
|---|---|---|
| [`configs/stockholm_site_config.json`](configs/stockholm_site_config.json) | Stockholm, 59.33 N | 40 kW DC / 30 kW AC at 45°, 120 kWh BESS |
| [`configs/kyiv_site_config.json`](configs/kyiv_site_config.json) | Kyiv, 50.45 N | 36 kW DC / 30 kW AC at 35°, 100 kWh BESS |

Print a simulated day from either, with no dependencies beyond the standard
library:

```bash
python examples/seasonal/plot_day.py stockholm --clear
```

```
    UTC   elev     GHI     POA      PV    load     grid    SOC
           deg    W/m2    W/m2      kW      kW       kW      %
  ------------------------------------------------------------
  08:00   10.7   163.4   435.3   16.01   51.57    35.66   55.0
  10:00   16.6   269.1   666.3   23.62   52.57    28.97   55.0
  12:00   14.6   233.6   590.1   21.00   55.84    34.78   54.9
  14:00    5.6    81.8   213.7    7.95   47.51    39.40   54.9

  GHI              ▁▂▄▅▆▇▇████▇▇▆▅▄▂▁                    peak 274.1
```

Clear-sky resource on the same three dates — this is what the geography buys:

| | Stockholm | Kyiv |
|---|---|---|
| Daylight, 31 Oct | 8.72 h | 9.68 h |
| Daylight, 21 Jun | 18.28 h | 16.23 h |
| Daylight, 21 Dec | 5.73 h | 7.78 h |
| GHI peak, 31 Oct | 274 W/m² | 411 W/m² |
| GHI peak, 21 Jun | 839 W/m² | 905 W/m² |
| GHI peak, 21 Dec | 108 W/m² | 239 W/m² |

See [`examples/seasonal/README.md`](examples/seasonal/README.md) for what the
two *plants* then do with that resource, which is a different question.

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

### BESS thermal management

Cell temperature derates capability. The curve is a ladder of bands, and the
defaults are the ones the model has always had — `≤ 0 °C → 0.5×`,
`≤ 10 → 0.8×`, `≤ 40 → 1.0×`, `≤ 50 → 0.8×`, `≤ 60 → 0.5×`, above → `0.0×`.
It scales both charge and discharge, and lands in `available_charging_power` /
`available_discharging_power`.

An **unmanaged** pack sits at ambient whenever it is idle, so a site that spends
the winter between 4 °C and 14 °C spends it at `0.8×` nameplate. A **managed**
pack sits in a container whose HVAC holds it inside a setpoint band:

> **In one line:** the HVAC auxiliary draw is accounted **only at the PCC** — in
> `grid_power_kw`, the import/export counters and the energy meter — and **not**
> in the site load model or any load-meter telemetry; and a pack that starts
> outside the band **snaps to the nearest edge on its first step**, it does not
> ramp in.

```json
{
  "type": "bess",
  "capacity_kwh": 100.0,
  "thermal": {
    "managed": true,
    "heat_setpoint_c": 15.0,
    "cool_setpoint_c": 30.0,
    "aux_power_kw": 1.5,
    "time_constant_s": 2500.0,
    "derate": {
      "bands": [[0.0, 0.5], [10.0, 0.8], [40.0, 1.0], [50.0, 0.8], [60.0, 0.5]],
      "above_factor": 0.0
    }
  }
}
```

**`managed`** — `false` by default, and with the whole `thermal` block absent
the battery behaves exactly as it did in 0.2.1.

**`heat_setpoint_c` / `cool_setpoint_c`** — the band, 15 °C and 30 °C by
default. Inside it nothing is held: the cell relaxes toward ambient, in either
direction, with time constant `time_constant_s`. It is never allowed to leave
the band, so with the default band inside the full-capability zone a managed
pack never derates on temperature. The HVAC is ideal within the band — it
supplies or removes exactly the heat needed to hold the nearest edge — so a
pack that *starts* outside the band (a restored state file, an `ambient_temp_c`
below the heat setpoint) is brought to that edge immediately rather than over a
ramp. `heat_setpoint_c` above `cool_setpoint_c` is a config error.

**`time_constant_s`** — the relaxation time constant, in seconds. Omit it and
it is the pack's own `C_th / G`, which is **2500 s (≈42 min)** with the
auto-scaled thermal defaults, whatever the pack size.

**`aux_power_kw`** — the container's own draw while heating or cooling is
actually active, `0` by default. It is reported as `hvac_power_kw` on the BESS
and enters the site power balance **on the load side**, exactly as the site
load does, so it shows in grid power, the import counter and the meter. It is
*not* netted off the BESS active power: a discharging managed pack still
reports what its inverter delivered, and the container's draw is accounted
separately. With `base_load` 7 kW, a 1.5 kW container and an idle pack the
meter reads 8.5 kW.

**`derate.bands`** — the capability ladder as `[upper bound °C, factor]` pairs,
in increasing order of temperature; the factor of the first band whose bound
the cell temperature does not exceed wins. **`derate.above_factor`** applies
above the last bound. Omit `derate` and the curve is unchanged, so no existing
config shifts.

Cell temperature is always reported in `battery_temperature`, managed or not,
and is still capped at 80 °C.

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
sky. With a `seed`, each day's regime is **keyed on the UTC calendar date**
— see below.

#### Weather, restarts and the calendar

With `cloud.seed` set, the daily clear-sky index for a simulated UTC date is a
function of **(seed, date)** and of nothing else. That means:

- restarting the process does not change today's weather, however often it
  restarts and whatever hour it comes back at;
- starting the run on a different date does not change any other date — day
  2026-06-10 looks the same whether the run began that morning or a hundred
  days earlier;
- consecutive dates still have the configured persistence `phi` and long-run
  mean `mean_kt`. The regime is read out of a keyed AR(1) chain rather than
  drawn independently per date, which is what keeps overcast spells lasting
  several days instead of collapsing to coin flips.

Before 0.2.1 the chain was seeded once at engine start and stepped from there,
so every boot replayed the same first day. A deployment restarted twice a day
got the same opening weather every time, and clear days practically never
came round.

The intraday variability is deliberately *not* keyed: it is advanced with the
engine's own timestep, which is what keeps its spread independent of the step
size, and a restart draws it afresh. So a restart mid-day leaves the day's
regime untouched but does change the minute-to-minute wobble on top of it.

With **no** `cloud.seed`, behaviour is random per run, exactly as in 0.2.0: a
fresh process gets a fresh sky. Within a single process the per-date lookup
still holds, so the same day always reports the same regime.

`series_csv` and `series_column: "kt"` replay are unaffected — a recorded
series is already a function of the date.
**`series_csv`** — replay recorded data. With `series_column: "kt"` it drives
the cloud index and keeps the clear-sky curve; otherwise it is absolute GHI in
W/m². Read once by default; `series_refresh_s` makes it a live input that is
re-read as it changes, with the stochastic cloud model taking over when it goes
stale. See [A live irradiance series](#a-live-irradiance-series).

What this buys, at Stockholm (59.33 N):

| Date | Daylight | Clear-sky GHI peak |
|---|---|---|
| 21 Jun | 18.3 h | ~819 W/m² |
| 31 Oct | 8.7 h | ~256 W/m² |
| 21 Dec | 5.7 h | ~93 W/m² |

The `sinusoidal` model gives all three 12.0 h and 1000 W/m².

### A live irradiance series

`series_csv` is read once at build time by default, which is all a replay of
recorded data needs. Set `series_refresh_s` and the same file becomes a live
input: dertwin re-reads it as it changes, so a separate fetcher — an
Open-Meteo sidecar, a plant's own historian, anything that can write a CSV —
can drive the simulated sky from real weather without dertwin knowing
anything about where the data came from.

```json
"irradiance": {
  "latitude": 50.0,
  "longitude": 10.0,
  "series_csv": "weather/ghi.csv",
  "series_refresh_s": 300.0,
  "series_stale_after_s": 1800.0,
  "cloud": { "mean_kt": 0.55, "seed": 42 }
}
```

The keys, all optional:

| Key | Default | Meaning |
|---|---|---|
| `series_csv` | — | the file to read |
| `series_column` | `ghi_w_m2` | the driving column. `kt` selects the sky-index form |
| `series_time_column` | `timestamp` | the timestamp column |
| `series_interpolate` | `linear` | `linear` between rows, or `step` to hold each row |
| `series_dni_column` | `dni_w_m2` | optional measured beam, used when the header has it |
| `series_dhi_column` | `dhi_w_m2` | optional measured diffuse, likewise |
| `series_refresh_s` | `0` | re-read interval. `0` is one-shot — read once, never again |
| `series_stale_after_s` | `3600` live, never one-shot | how long the last row is held before the series counts as stale |
| `series_max_gap_s` | `0` | a gap wider than this is missing data rather than something to interpolate across. `0` bridges any gap |
| `series_handover_tau_s` | `cloud.intraday_tau_s` | how long a switch between the series and the model takes to complete. `0` makes each switch a plain jump |

#### The file contract

So an external fetcher knows exactly what to write:

- **A header row is required.** Column order does not matter and extra columns
  are ignored, so a file carrying temperature, wind and pressure alongside
  irradiance is fine — dertwin reads the columns it was told about.
- **Timestamps are UTC.** ISO 8601, with a trailing `Z`, an explicit offset, or
  no zone at all, in which case UTC is assumed. Rows may be in any order; they
  are sorted on read.
- **Units.** `ghi_w_m2`, `dni_w_m2` and `dhi_w_m2` are W/m². `kt` is the
  dimensionless clear-sky index in [0, 1] — the fraction of clear-sky
  irradiance reaching the ground.
- **GHI or kt, not both.** With `series_column: "kt"` the series replaces the
  stochastic cloud model and the clear-sky curve stays; otherwise the column
  is absolute GHI and replaces the curve entirely.
- **DNI and DHI are optional and are believed when present.** Give both and
  they are used as they stand, and are *not* adjusted to close with GHI —
  measured triples rarely close, and silently fixing one of three measured
  numbers would hide a data problem. Give one and the other is derived from
  GHI. Give neither and the Erbs correlation splits GHI, as it always has.
- **Duplicate timestamps: the last row in the file wins.** A correction
  appended later therefore overrides the row it corrects.
- **A blank cell is a row that column does not have.** Each column is built
  from the rows where its own cell held a number, so a half-filled DNI column
  still leaves a complete GHI series.
- **Write the file atomically.** Write a temporary file in the same directory
  and `rename` it over the target. That is the one pattern under which a
  reader can never see a half-written file:

  ```python
  tmp = path.with_suffix(".csv.tmp")
  tmp.write_text(body)
  os.replace(tmp, path)          # atomic within a filesystem
  ```

  dertwin stats the file before and after each read and throws away a snapshot
  that moved underneath it, so an in-place append is survivable — but it costs
  a refresh interval each time it is caught, and the rename costs nothing.
- **Growing the file is the normal case.** Rewrite it whole with the new rows
  appended, or keep a rolling window; either works, and only the rows covering
  the simulated instant matter.
- **A malformed row is skipped, not fatal.** In live mode an unparseable
  timestamp or value is counted, logged and dropped, so one torn line cannot
  take the simulation with it. A file missing its header or its driving column
  is a different matter: it is unusable, and the previous snapshot is kept.

Only the file is re-read. Nothing else in the config is, so a fetcher cannot
change the site's latitude, its panels or its battery.

#### When the series goes stale

A live feed will eventually be late — the fetcher dies, the network goes, the
container is rescheduled. `series_stale_after_s` is how long the last row
keeps being served once simulated time has passed it; a fetcher writing hourly
rows is always a little behind, and without the hold every instant between
updates would have no data at all.

Past that, the series is stale and **the stochastic cloud model takes over**.
Not a dark sky and not a cloudless one: both are wrong, and both are wrong in
a way that quietly corrupts whatever is reading the twin. A dark sky tells an
EMS the plant has failed; a cloudless one tells it the plant is at nameplate.
The stochastic model tells it the weather is ordinary, which is the only
honest answer when the measurement is gone. The same applies to a file that
is missing or unreadable at startup: the run begins on the stochastic model
and picks the feed up when it appears, rather than refusing to start.

Each transition is one log line, in both directions:

```
WARNING  weather/ghi.csv: series is stale at 2025-06-22T06:00:00+00:00
         (past the last row); falling back to the stochastic cloud model
INFO     weather/ghi.csv: fresh series data at 2025-06-22T09:12:00+00:00;
         leaving the stochastic cloud model
```

A run that never loses its feed says nothing at all, and one that flaps says
exactly when and how often.

**The switch carries no step of its own.** What crosses a handover is how
cloudy it is — the clear-sky index — not the W/m² a particular sun elevation
happened to deliver, and it is carried by the same rule in both directions:
the last index reported is held and relaxed away at the cloud model's own
intraday correlation time.

Going into the fallback, that needs no new machinery. The AR(1) model's index
is `kt_day + x`, a daily regime plus a mean-reverting perturbation, and the
perturbation already decays at `intraday_tau_s`. Seeding `x` with the
difference between the last measurement and today's regime makes the first
index the model reports *exactly* the last one measured, after which it
relaxes back to the regime at the rate it always does. The day's regime is
left alone deliberately: a measurement that ends on a bright minute must not
turn the whole day clear.

Coming back, the series is data and cannot be seeded, so an explicit offset
with the same time constant decays onto it instead. The alternative — jumping
straight to fresh data — is available as `series_handover_tau_s: 0`, and is
the right choice if you would rather the twin always report the measurement
than always move smoothly.

Either way the switch tick itself is a step of zero, and every step after it
is one the stochastic model could have produced on its own. A short blend over
an arbitrary window would have done the same job for the eye, but it would
have reported a sky neither source produced and added a timescale the
simulation does not otherwise have.

Everything here — the refresh interval, the grace period, the handover — is
measured in **simulated** time, for the same reason the cloud regime is: a run
that is not in real time, or one replayed from the same config, has to behave
the same way twice. In a real-time deployment, which is what a live feed is
for, the two coincide.

**Ambient temperature is not part of this.** The series drives irradiance
only; `ambient_temperature` stays on its own model.

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
`tau_s` its correlation time; bounded by `min_factor` / `max_factor`. Keyed on
simulated time, so a restart does not replay it — see below.
**`noise`** — the same process on a timescale of seconds.
**`seed`** — one seed drives both random terms, through a stream each, so
adding or removing one does not shift the other's numbers. Drift and noise are
off unless configured.

The drift has the same restart property as the cloud regime: with a `seed`, the
drift factor at an instant is a function of **(seed, that instant)**, so a
process that boots at 13:00 reports the drift the process it replaced would
have been reporting. It keeps the exact discrete-time Ornstein-Uhlenbeck
statistics — `sigma` is the standard deviation of the factor and `tau_s` its
correlation time, both independent of the simulator's step — by sampling on a
fixed internal lattice of a sixteenth of `tau_s` rather than on the tick.

This needs a top-level `sim_start_utc`, because that is what gives the clock an
absolute instant to key on. Without one the clock restarts at zero on every
boot, so the drift restarts with it; the model keys on the run clock, which is
the most that clock can offer.

The fast `noise` term stays free-running on purpose: zero-mean with a
correlation time of half a minute, there is nothing in it worth reproducing
across a restart.

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

The test suite covers device physics (BESS, PV, CHP, energy meter), battery thermal management (including a golden day-long comparison that an unmanaged pack still reproduces the 0.2.1 model exactly), solar geometry and clear-sky irradiance against published reference values, cloud-model statistics, per-date determinism of the cloud regime and the load drift across restarts, the statistics of the stochastic site load, register encoding with per-register endianness, FC02 discrete input routing, external models, protocol parity (TCP and RTU), mixed-protocol engine integration, runtime asset add/remove, and full end-to-end site integration via Modbus. See `tests/` for structure.

The built-in solar model is also cross-checked against `pvlib`, which is
optional, so that suite skips unless you install it:

```bash
pip install "dertwin[pvlib]"
```

---

## 📈 Roadmap

- [ ] Scenario engine — scripted event sequences
- [ ] Markov-regime cloud model as an alternative to AR(1)
- [x] Location- and date-aware clear-sky irradiance with cloud cover
- [x] Plane-of-array irradiance, DC/AC ratio and inverter clipping
- [x] Stochastic site load with daily profile and drift
- [x] Hard BESS SOC limits with optional persistence
- [x] BESS thermal management with a configurable temperature derate curve
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