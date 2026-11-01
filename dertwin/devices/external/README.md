# External Simulation Models for DER Systems

This package provides deterministic **external world models** for simulating distributed energy resources (DER) such as PV inverters, battery energy storage systems (BESS), combined heat and power units (CHP), and grid-connected sites. It includes ambient conditions, irradiance, grid frequency and voltage, and site-level power flow models.

All models are designed to be **deterministic**, fully **simulation-time driven**, and **modular**, allowing flexible integration into DER simulations.

---

## Table of Contents

- [Features](#features)
- [Modules](#modules)
  - [ambient_temperature.py](#ambient_temperaturepy)
  - [irradiance.py](#irradiancepy)
  - [load.py](#loadpy)
  - [stochastic.py](#stochasticpy)
  - [grid_frequency.py](#grid_frequencypy)
  - [grid_voltage.py](#grid_voltagepy)
  - [power_flow.py](#power_flowpy)
  - [external_models.py](#external_modelspy)
- [Usage Example](#usage-example)

---

## Features

- **Ambient conditions:** Deterministic daily temperature profiles
- **Solar irradiance:** Clear-sky sinusoidal irradiance curves
- **Grid frequency & voltage:** Drift, noise, events, and deterministic disturbances
- **Site load:** Daily profile, weekday/weekend factor, slow mean-reverting drift and fast noise — seedable
- **Site power flow:** Aggregates load, PV, BESS, and CHP contributions
- **Deterministic update cycle:** External models update before devices in simulation
- **Configurable defaults:** Parameters such as nominal voltage, frequency, irradiance, and temperature

---

## Modules

### `ambient_temperature.py`

**Class:** `AmbientTemperatureModel`

Simulates daily ambient temperature with a **deterministic cosine curve**.

**Features:**
- 24h periodic variation
- Configurable mean, amplitude, and peak hour
- Fully deterministic, no randomness

**Key methods:**
```python
update(sim_time: float, dt: float)
get_temperature() -> float
```
**Example:**
```python
atm = AmbientTemperatureModel(mean_temp_c=25, amplitude_c=5, peak_hour=14)
atm.update(sim_time=36000, dt=60)
temperature = atm.get_temperature()
```

### `irradiance.py`

**Class:** `IrradianceModel`
Simulates daily clear-sky irradiance.

**Features:**
- Sinusoidal daily irradiance curve
- Configurable peak irradiance, sunrise, and sunset hours
- Deterministic 24h cycle

**Key methods:**
```python
update(sim_time: float, dt: float)
get_irradiance() -> float
set_irradiance(value: float)
```

### `load.py`

**Class:** `SiteLoadModel`; profiles `FlatProfile`, `HourlyProfile`,
`TabulatedProfile`, `ParametricProfile`; factory `build_load_model`

The site load. It used to be `lambda t: base_load` — five kilowatts by default,
the same at 03:00 as at 18:00 and the same on a Sunday as on a Tuesday. An EMS
tested against that never sees the thing it exists to handle.

The load is multiplicative, which is what lets the pieces be configured
without fighting each other:

```
load_kw = base_kw · profile(hour) · weekday · drift · (1 + noise)
```

| Term | What it is |
|---|---|
| `profile` | The daily shape. Normalised to a daily mean of 1.0, so it only ever changes the shape and `base_kw` keeps meaning *average* load. |
| `weekday` | One of two factors, chosen by the day of the week. |
| `drift` | Slow mean-reverting random walk (Ornstein-Uhlenbeck, `tau` in hours) — the "this week is busier than usual" term. Bounded by `min_factor` / `max_factor`. |
| `noise` | The same process on a timescale of seconds — individual equipment switching. |

Both random terms draw from one stream, so a single `seed` makes the whole load
reproducible. Both are off by default.

#### Daily shapes

| `profile.model` | Input |
|---|---|
| `flat` | Nothing. The pre-0.2 constant. **The default.** |
| `hourly` | 24 factors, one per hour |
| `parametric` | A night baseline with a morning and an evening Gaussian bump |
| `csv` | An hour-of-day table at any resolution |

Hourly factors are placed at their **hour centres** and interpolated
circularly: hourly figures are averages over the hour, so centring them is what
reproduces the intended shape, and wrapping means there is never a
discontinuity at midnight. With `interpolate: false` the nearest point wins,
which makes hour 8's value apply across the whole of hour 8.

A CSV of absolute `kw` is a shape *plus* a level: it is normalised to a mean of
1.0 and the level becomes the default `base_kw`, so an explicit `base_kw` still
rescales it.

#### Day of the week

The clock is an offset from the start of the run, not a date, so the week is
placed by `start_weekday` (a name or 0–6 with Monday = 0) and advances at each
simulated midnight. `advance()` also accepts an optional absolute UTC instant,
and when one is given the real calendar wins — so this composes with an
absolute clock rather than duplicating it.

**Key methods:**
```python
advance(sim_time: float, dt: float, utc_time: datetime | None = None)
get_power_kw() -> float
weekday_at(sim_time, utc_time=None) -> int     # Monday = 0
is_weekend(sim_time, utc_time=None) -> bool
```

The model is an external world model, not a device: no registers, no protocol
of its own. It reaches the meter the way the constant always did, through the
site power balance.

### `stochastic.py`

**Class:** `OrnsteinUhlenbeck`

Mean-reverting noise with a stationary `N(0, sigma²)` distribution, using the
exact discrete-time solution rather than an Euler step:

```
a = exp(-dt / tau)
x <- a·x + sqrt(1 - a²) · sigma · N(0, 1)
```

That matters here because the simulation step is configurable: an Euler
discretisation would quietly change the amplitude of the noise when you switch
from 0.1 s to 60 s steps. The test suite pins the stationary spread across six
orders of magnitude of `dt`.

The process starts drawn from its stationary distribution, so there is no
warm-up transient, and a disabled process (`sigma = 0`) draws nothing from the
random stream — so enabling it cannot shift an unrelated process's path.

### `grid_frequency.py`

**Class:** `GridFrequencyModel` & `ConstantGridFrequencyModel`

Simulates grid frequency variations with **drift, noise, and events**.

**Features:**
- Nominal frequency with configurable noise and drift
- Frequency events: step or ramp
- Safe bounds (min/max)
- Deterministic, simulation-time driven

**Key methods:**
```python
update(sim_time: float, dt: float)
get_frequency() -> float
add_event(event: FrequencyEvent)
clear_events()
```

**Constant variant**: `ConstantGridFrequencyModel` returns a fixed frequency.

### `grid_voltage.py`

**Class:** `GridVoltageModel` & `ConstantGridVoltageModel`

Simulates L-L RMS grid voltage with drift, noise, and sag/swell events.

**Features:**
- Nominal voltage with configurable noise and drift
- Voltage events: step or ramp
- Safe voltage limits
- Fully deterministic

**Key methods:**
```python
update(sim_time: float, dt: float)
get_voltage_ll() -> float
get_voltage_ln() -> float
add_event(event: VoltageEvent)
clear_events()
```

**Constant variant**: `ConstantGridVoltageModel` returns a fixed voltage.

### `power_flow.py`

**Class:** `SitePowerModel`

Simulates **site-level power balance** including load, PV, BESS, and CHP generation.

**Features:**
- Aggregates base load, PV, BESS, and CHP contributions
- Computes net grid power (positive = import, negative = export)
- Tracks import/export energy over time
- Fully deterministic
- All generator suppliers are optional — works with any subset of device types

**Key methods:**
```python
update(dt: float)
get_sim_time() -> float
```

**Usage:**
```python
model = SitePowerModel(
    base_load_supplier=lambda t: 50.0,
    pv_supplier=lambda: 10.0,
    bess_supplier=lambda: -2.0,      # negative = charging, positive = discharging
    chp_supplier=lambda: 30.0,
)
model.update(dt=60)
grid_power = model.grid_power_kw   # 50 - 10 - (-2) - 30 = 12 kW import
```

**Sign conventions:**
- `base_load_supplier(t)` → site load in kW (always positive). When wired by
  `build_power_model` this ignores `t` and reads the current value from
  `SiteLoadModel`, which `ExternalModels.update` has already advanced.
- `pv_supplier()` → PV generation in kW (always non-negative)
- `bess_supplier()` → BESS power in kW; **positive = discharge** (acts as generator), **negative = charge** (acts as load)
- `chp_supplier()` → CHP generation in kW (always non-negative)

The energy meter observes the resulting `grid_power_kw` — it does not need to know which device types contributed to it.

### `external_models.py`

**Class:** `ExternalModels`

Aggregates all external models in a single update point. Ensures deterministic causality:
```markdown
world → devices → telemetry
```

**Supported models:**
- Site load
- Site power
- Grid frequency
- Grid voltage
- Ambient temperature
- Irradiance

**Key methods:**
```python
update(sim_time: float, dt: float)
build_power_model(devices_by_type, config=None, load_model=None)
build_default()
from_config(config: Dict, data_root=None)
```

`update` advances the load **before** the power balance, because the balance
reads the load through `base_load_supplier`. Advancing it here, against the
engine's own simulation time, is also what keeps the load's daily profile in
phase with the irradiance and ambient temperature models — `SitePowerModel`
keeps a private clock that starts at zero and ignores `start_time_h`, so a
supplier keyed off that argument would have sat hours out of phase.

`build_power_model` automatically wires up `pv_supplier`, `bess_supplier`, and `chp_supplier` based on the device types present in the site, summing `total_active_power` from PV, `active_power` from BESS, and `actual_power_kw` from CHP.

**Usage:**
```python
from dertwin.devices.external.external_models import ExternalModels

external = ExternalModels.build_default()
external.update(sim_time=3600, dt=60)
```

## Usage Example
```python
from dertwin.devices.external.ambient_temperature import AmbientTemperatureModel
from dertwin.devices.external.external_models import ExternalModels

# Create ambient temperature model
atm = AmbientTemperatureModel(mean_temp_c=22, amplitude_c=5, peak_hour=15)

# Aggregate into external models
external = ExternalModels(ambient_temperature_model=atm)

# Simulate
for t in range(0, 86400, 60):  # 1 day, 1-minute steps
    external.update(sim_time=t, dt=60)
    print(f"Time {t/3600:.1f}h: Temp={atm.get_temperature():.2f} C")
```

## Deterministic Simulation Notes
- All models are simulation-time driven.
- The update sequence guarantees world → devices → telemetry.
- Supports both constant and stochastic/deterministic variations.
- Provides configurable defaults for quick prototyping.