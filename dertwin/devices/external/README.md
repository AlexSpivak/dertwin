# External Simulation Models for DER Systems

This package provides deterministic **external world models** for simulating distributed energy resources (DER) such as PV inverters, battery energy storage systems (BESS), combined heat and power units (CHP), and grid-connected sites. It includes ambient conditions, irradiance, grid frequency and voltage, and site-level power flow models.

All models are designed to be **deterministic**, fully **simulation-time driven**, and **modular**, allowing flexible integration into DER simulations.

---

## Table of Contents

- [Features](#features)
- [Modules](#modules)
  - [ambient_temperature.py](#ambient_temperaturepy)
  - [solar.py](#solarpy)
  - [clouds.py](#cloudspy)
  - [irradiance.py](#irradiancepy)
  - [timeseries.py](#timeseriespy)
  - [grid_frequency.py](#grid_frequencypy)
  - [grid_voltage.py](#grid_voltagepy)
  - [power_flow.py](#power_flowpy)
  - [external_models.py](#external_modelspy)
- [Usage Example](#usage-example)

---

## Features

- **Ambient conditions:** Deterministic daily temperature profiles
- **Solar irradiance:** Real solar geometry for a date and location — correct sunrise, sunset and seasonal peak — with a stochastic cloud model on top, or the original fixed-shape sinusoidal curve, or GHI replayed from a CSV
- **Grid frequency & voltage:** Drift, noise, events, and deterministic disturbances
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

### `solar.py`

Solar geometry and clear-sky irradiance. Standard library only — no numpy, no
pandas, no pvlib.

**Pipeline:**

```
solar_position(utc_time, lat, lon)          NOAA / Spencer (1971)
  -> clear_sky_ghi(position, utc_time)      Ineichen-Perez (2002), or Haurwitz (1945)
  -> erbs_split(ghi, position, utc_time)    Erbs (1982): GHI -> DNI + DHI
  -> plane_of_array_irradiance(...)         Liu & Jordan (1963) isotropic sky
```

Each step is a plain function on floats, so it is testable in isolation and
costs nothing per tick.

**Measured accuracy** against pvlib's SPA over all of 2025 at 59.3 N, 30-minute
resolution (`tests/devices/external/test_pvlib_agreement.py`):

| Quantity | Error |
|---|---|
| Solar declination | ≤ 0.54° — *identical* to pvlib's own `declination_spencer71`; the residual is inherent to the Spencer-71 series |
| Solar elevation | ≤ 0.55° |
| Solar azimuth | ≤ 0.44° |
| Equation of time | ≤ 0.02 min |
| Annual clear-sky GHI, cost of the position error | 0.03 % |
| Annual clear-sky GHI, this Ineichen vs pvlib's | 1.4 % |

Refraction is not modelled, so elevation is geometric: worth up to half a
degree at the horizon and almost no energy.

Two notes on Ineichen-Perez. The `exp(0.01·AM^1.8)` Perez enhancement from the
original paper is **on** by default here, where pvlib leaves it off: without it
the model reads ~30% low at a high-latitude winter sun (62 vs 87 W/m² at
Stockholm in December, against Haurwitz). It does misbehave within a degree of
the horizon, so GHI is clamped to the extraterrestrial horizontal irradiance —
a hard physical ceiling.

### `clouds.py`

**Classes:** `AR1CloudModel`, `ClearSkyCloudModel`, `SeriesCloudModel`

Cloud cover as a clear-sky index `kt` in `[kt_min, 1]`, the fraction of
clear-sky GHI that reaches the ground. Two timescales, because real weather has
both:

```
z  <- phi·z + sqrt(1 - phi²)·sigma_day·N(0,1)     once per UTC day   (AR(1))
kt_day = kt_min + (1 - kt_min)·expit(logit(m) + z)
x  <- a·x + sqrt(1 - a²)·intraday_sigma·N(0,1)    every step, a = exp(-dt/tau)  (OU)
kt = clamp(kt_day + x, kt_min, 1)
```

The daily index is drawn in logit space, which keeps it bounded without
clipping artefacts, and `phi` alone controls how long overcast spells last.
Both processes start in their stationary distribution, so there is no warm-up
transient. By symmetry the **median** daily index is exactly `mean_kt`.

`sigma_day` is in logit space. The default `0.9` gives a daily index with a
standard deviation of about 0.17 and genuinely overcast days a few percent of
the time; `0.45` would keep every day between 0.45 and 0.77, which is no
climate on earth.

Clouds are **on by default** when a clear-sky model is configured. Set
`cloud.seed` for reproducible runs, or `cloud.enabled: false` for a permanently
cloudless sky.

`model: "markov"` (a 3-state regime chain) is reserved and currently raises
`NotImplementedError`.

### `irradiance.py`

**Classes:** `IrradianceModel`, `ClearSkyIrradianceModel`,
`SeriesIrradianceModel`; factory `build_irradiance_model`

Three models, selected by `irradiance.model`:

| `model` | Behaviour |
|---|---|
| `"sinusoidal"` | The original fixed-shape day: a sine between a configured sunrise and sunset, identical every day of the year everywhere on earth. **The default**, so existing configs are untouched. |
| `"clearsky"` | Real solar geometry for the date and location × a cloud index. Selected automatically when `latitude` is given. |
| `"series"` | GHI replayed from a CSV. |

All three publish `IrradianceComponents` — GHI, DNI, DHI and the sun's
position. Plane-of-array transposition is deliberately **not** done here: one
irradiance model is shared by every PV asset on the site and each array has its
own tilt and azimuth, so transposition belongs to the array (see
`dertwin/devices/pv/README.md`).

`IrradianceModel` keeps its original `update(sim_time, dt)` signature and has
no solar geometry, so `IrradianceComponents.has_solar_geometry` is `False` and a
tilted array falls back to treating irradiance as already in-plane, with one
warning.

A `clearsky` or `series` model needs a date, so it requires a top-level
`sim_start_utc` and raises without one — otherwise it would silently simulate
January 1970.

**Key methods:**
```python
advance(utc_time: datetime, sim_time: float, dt: float)
get_components() -> IrradianceComponents
get_irradiance() -> float          # GHI, W/m²
```

### `timeseries.py`

**Class:** `TimeSeries`

CSV time series for driving an external model from recorded data. Linear or
step interpolation, timestamps parsed as ISO 8601 (bare, `Z`, or an offset),
rows may be unsorted. `value_at` returns `None` outside the covered range —
one warning, then each caller decides what that means (zero for irradiance).

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
- `base_load_supplier(t)` → site load in kW (always positive)
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
- Site power
- Grid frequency
- Grid voltage
- Ambient temperature
- Irradiance

**Key methods:**
```python
update(utc_time: datetime, sim_time: float, dt: float)
build_power_model(devices_by_type, config=None)
build_default()
from_config(config: Dict, data_root=None, sim_start_utc_configured=False)
```

`update` takes both clocks: `utc_time` is the absolute instant, used by the
date-aware irradiance model, and `sim_time` is seconds since the start of the
run, used by the event schedules in the grid frequency and voltage models.

`build_power_model` automatically wires up `pv_supplier`, `bess_supplier`, and `chp_supplier` based on the device types present in the site, summing `total_active_power` from PV, `active_power` from BESS, and `actual_power_kw` from CHP.

**Usage:**
```python
from dertwin.devices.external.external_models import ExternalModels

external = ExternalModels.build_default()
external.update(utc_time=datetime(2025, 10, 31, 1, tzinfo=timezone.utc),
                sim_time=3600, dt=60)
```

## Usage Example
```python
from datetime import datetime, timedelta, timezone

from dertwin.devices.external.ambient_temperature import AmbientTemperatureModel
from dertwin.devices.external.clouds import AR1CloudModel
from dertwin.devices.external.external_models import ExternalModels
from dertwin.devices.external.irradiance import ClearSkyIrradianceModel

# Stockholm, late October
irradiance = ClearSkyIrradianceModel(
    latitude=59.3293,
    longitude=18.0686,
    altitude_m=44.0,
    linke_turbidity=3.0,
    cloud_model=AR1CloudModel(mean_kt=0.48, seed=59),
)
atm = AmbientTemperatureModel(mean_temp_c=8, amplitude_c=4, peak_hour=15)

external = ExternalModels(
    ambient_temperature_model=atm,
    irradiance_model=irradiance,
)

start = datetime(2025, 10, 31, tzinfo=timezone.utc)

for t in range(0, 86400, 600):  # 1 day, 10-minute steps
    external.update(start + timedelta(seconds=t), sim_time=t, dt=600)
    components = irradiance.get_components()
    print(
        f"{(start + timedelta(seconds=t)):%H:%M}  "
        f"elev={components.position.elevation_deg:6.2f} deg  "
        f"GHI={components.ghi:6.1f} W/m2  "
        f"kt={components.clear_sky_index:.2f}  "
        f"T={atm.get_temperature():.1f} C"
    )
```

## Deterministic Simulation Notes
- All models are simulation-time driven; internal time is always UTC.
- Stochastic models are seedable: set `cloud.seed`, `grid_frequency.seed` and
  `grid_voltage.seed` for a reproducible run.
- The update sequence guarantees world → devices → telemetry.
- Supports both constant and stochastic/deterministic variations.
- Provides configurable defaults for quick prototyping.