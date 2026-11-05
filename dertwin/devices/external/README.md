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
  - [load.py](#loadpy)
  - [stochastic.py](#stochasticpy)
  - [timeseries.py](#timeseriespy)
  - [series_feed.py](#series_feedpy)
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
z      = keyed AR(1) at the UTC date's day number   (persistence phi, spread sigma_day)
kt_day = kt_min + (1 - kt_min)·expit(logit(m) + z)
x     <- a·x + sqrt(1 - a²)·intraday_sigma·N(0,1)   every step, a = exp(-dt/tau)  (OU)
kt     = clamp(kt_day + x, kt_min, 1)
```

The daily index is drawn in logit space, which keeps it bounded without
clipping artefacts, and `phi` alone controls how long overcast spells last.
Both processes start in their stationary distribution, so there is no warm-up
transient. By symmetry the **median** daily index is exactly `mean_kt`.

The daily regime is **keyed**, not stepped. `z` for a date is read out of
`KeyedAR1` (see `stochastic.py`) at that date's proleptic day number, so with a
`seed` it is a function of `(seed, date)`: a restart cannot change today's
weather, and the date the run starts on cannot change any other date. An
independent draw per date would be restart-proof too and would quietly set
`phi` to zero; the keyed chain keeps the persistence. One day's regime costs
`window` multiply-adds, set by `phi` alone — 49 at the default `phi = 0.65` —
with no epoch to count from and no catch-up loop for a date far away.

The intraday term is free-running on purpose: it is advanced with the engine's
dt, which is what keeps its spread independent of the step size, and it is a
zero-mean wobble with a correlation time of minutes. A restart mid-day
redraws it and leaves the day's regime alone. Unseeded, the whole model is
random per run, as before.

`sigma_day` is in logit space. The default `0.9` gives a daily index with a
standard deviation of about 0.17 and genuinely overcast days a few percent of
the time; `0.45` would keep every day between 0.45 and 0.77, which is no
climate on earth.

Clouds are **on by default** when a clear-sky model is configured. Set
`cloud.seed` for reproducible runs, or `cloud.enabled: false` for a permanently
cloudless sky.

`model: "markov"` (a 3-state regime chain) is reserved and currently raises
`NotImplementedError`.

`SeriesCloudModel` replays `kt` from a feed. Given a `fallback` — which the
factory supplies whenever the feed can go stale — it hands the sky to the
stochastic model when the feed has nothing for the instant, rather than
reporting the cloudless sky a missing row used to mean. `seed_from_kt` is how
the handover is made continuous: it places the intraday perturbation `x` so
the next index reported is the last one measured, leaving `kt_day` alone, so a
measurement that ends on a bright minute cannot turn the whole day clear.

### `irradiance.py`

**Classes:** `IrradianceModel`, `ClearSkyIrradianceModel`,
`SeriesIrradianceModel`; factory `build_irradiance_model`

Three models, selected by `irradiance.model`:

| `model` | Behaviour |
|---|---|
| `"sinusoidal"` | The original fixed-shape day: a sine between a configured sunrise and sunset, identical every day of the year everywhere on earth. **The default**, so existing configs are untouched. |
| `"clearsky"` | Real solar geometry for the date and location × a cloud index. Selected automatically when `latitude` is given. |
| `"series"` | GHI replayed from a CSV, optionally re-read live, with the stochastic cloud model taking over when it goes stale. |

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

`SeriesIrradianceModel` reads its CSV through a `SeriesFeed`. With no
`fallback` it is the one-shot replay it has always been, dark outside the
series included. With one — supplied by the factory whenever the feed can go
stale — it holds a full `ClearSkyIrradianceModel` and delegates to it while the
series has nothing to give, so the fallback sky is indistinguishable from a run
that never had a series. The switch is made continuous in clear-sky-index
space, not in W/m²: what carries over a handover is how cloudy it is, not the
irradiance a particular sun elevation happened to deliver. Optional
`dni_w_m2` / `dhi_w_m2` columns are believed when the file has them;
`resolve_beam_split` derives whichever is missing and falls back to Erbs when
neither is there.

**Key methods:**
```python
advance(utc_time: datetime, sim_time: float, dt: float)
get_components() -> IrradianceComponents
get_irradiance() -> float          # GHI, W/m²
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
| `drift` | Slow mean-reverting random walk (Ornstein-Uhlenbeck, `tau` in hours) — the "this week is busier than usual" term. Bounded by `min_factor` / `max_factor`. Keyed on simulated time. |
| `noise` | The same process on a timescale of seconds — individual equipment switching. Free-running. |

Both random terms come from one `seed`, through a stream each, so the whole
load is reproducible and neither term's draws shift the other's. Both are off
by default.

The drift is a `KeyedOrnsteinUhlenbeck`: its factor at an instant is a function
of `(seed, instant)`, so a restarted process agrees with the one it replaced
instead of replaying the same afternoon from the seed. The instant is UTC when
the config has a `sim_start_utc`, and seconds since the start of the run
otherwise — in which case a restart resets the clock too, and nothing can be
done about it. Sampling happens on a fixed lattice of `tau_s / 16`, not on the
tick, which is what keeps `sigma` and `tau_s` independent of the simulator's
step.

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

**Classes:** `OrnsteinUhlenbeck`, `KeyedAR1`, `KeyedOrnsteinUhlenbeck`;
helpers `derive_seed`, `keyed_standard_normal`

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

#### Keyed processes

`OrnsteinUhlenbeck` is *free-running*: it carries a state and steps it once per
tick, so its trajectory belongs to the process that started it and a restart
replays it from the seed. For anything slow enough to matter across a restart —
a weather regime, a weekly load drift — that is a bug, not a feature.

`KeyedAR1` is the fix. The value at step `n` is a pure function of
`(seed, n)`, built from the moving-average form of the AR(1) recursion:

```
x_n = phi·x_{n-1} + sqrt(1 - phi²)·sigma·e_n
    = sqrt(1 - phi²)·sigma · Σ_{j≥0} phi^j · e_{n-j}
```

Each innovation `e_k` is a standard normal keyed on `(seed, stream, k)`:
hashed into the seed of a throwaway generator rather than read off a position
in one long stream, so any step can be evaluated directly, at the same cost, in
any order. The sum is truncated after `window` terms, which leaves an error of
`sigma · phi^window`; with a tolerance of `1e-9`,

```
window = ceil(log(tolerance) / log(phi))      # 49 at phi = 0.65, 197 at 0.9
```

So one evaluation costs `window` multiply-adds and one hash per innovation not
already cached, both fixed by `phi`. Nothing scales with `n`: there is no epoch
to iterate from, and step 10 and step 10,000,000 cost the same. A `phi` very
close to 1 is capped at `MAX_KEYED_WINDOW`, which costs truncation accuracy,
never reproducibility.

`KeyedOrnsteinUhlenbeck` is the same idea against a clock. It samples a keyed
AR(1) on a fixed lattice of `tau_s / 16` and holds between lattice points, so
on the lattice it is the exact discrete-time solution — stationary spread
exactly `sigma`, autocorrelation exactly `exp(-lag / tau)` — while the value at
an instant depends on *when* it is asked about rather than on how the caller
got there. Fixing the lattice to the process's own correlation time instead of
to the tick is what makes `sigma` and `tau_s` independent of the simulator's
step.

`derive_seed(seed, stream)` gives each process its own stream from one
configured seed, so a process that draws more or fewer values cannot shift
another's numbers. With `seed=None` it is random, so an unseeded run stays
random per run.
### `timeseries.py`

**Classes:** `TimeSeries`, `SeriesTable`; reader `read_series_table`

CSV time series for driving an external model from recorded data. Linear or
step interpolation, timestamps parsed as ISO 8601 (bare, `Z`, or an offset),
rows may be unsorted. `value_at` returns `None` outside the covered range —
one warning, then each caller decides what that means (zero for irradiance).

Duplicate timestamps resolve to the **last row in the file**: the sort is
stable and `value_at` reads the last of a run of duplicates, so a correction
appended later overrides the row it corrects. `max_gap_s` makes a gap wider
than it missing data rather than something to draw a straight line across;
`0`, the default, bridges any gap.

`SeriesTable` is several named columns over one time axis from a single read,
with a `primary` column a caller cannot do without — an instant with no
primary value has no sample at all. Each column is built from the rows where
its own cell held a number, so a half-filled DNI column still leaves a
complete GHI series. `read_series_table` takes `skip_bad_rows`, which relaxes
*rows* only: an unparseable timestamp or value is counted and dropped, while a
file missing its header or its driving column is still an error, because a
file in the wrong shape is no more usable for being readable.

### `series_feed.py`

**Classes:** `SeriesFeed`, `SourceSwitch`, `DecayingOffset`

The thing between a CSV on disk and a model that wants a value for an instant.
`SeriesFeed` does both jobs: a one-shot read at build time — the default, and
a bad file there is still a configuration error — or, with a positive
`refresh_s`, ownership of the path. In live mode it re-reads on an interval,
notices an external fetcher replacing the file, holds the last row for
`stale_after_s` once simulated time has passed it, and returns `None` when it
has nothing usable. A missing file at startup is then a warning rather than a
failure to start, because the whole point of the fallback is that a run
survives its fetcher not being up yet.

The re-read costs one `stat` per interval unless the signature —
`(mtime_ns, size, inode, device)` — has moved; the inode is in it because an
atomic replacement may well land on the same size and mtime resolution. The
file is stat'ed again after the read and a snapshot that moved underneath it is
dropped and retried, so a writer that appends in place cannot feed the
simulation half of two versions. A writer using a temporary file and `rename`,
which is what the file contract asks for, cannot produce a torn read at all.

Everything is keyed on simulated UTC instants rather than wall-clock seconds,
for the same reason the cloud regime is: a run that is not in real time, or one
replayed from the same config, has to behave the same way twice.

`SourceSwitch` holds which source a model is reporting from and logs one line
per change, in both directions — a run that never loses its feed says nothing
at all, and one that flaps says exactly when and how often. `DecayingOffset`
is the other half of the handover: an offset that relaxes to zero with a time
constant, used in the direction where the model cannot simply be seeded
(coming back to the series, which is data). `tau_s` of `0` disables it and
makes the switch a plain jump.

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
update(utc_time: datetime, sim_time: float, dt: float)
build_power_model(devices_by_type, config=None, load_model=None)
build_default()
from_config(config: Dict, data_root=None, sim_start_utc_configured=False)
```

`update` takes both clocks: `utc_time` is the absolute instant, used by the
date-aware irradiance model, and `sim_time` is seconds since the start of the
run, used by the event schedules in the grid frequency and voltage models.

It advances the load **before** the power balance, because the balance reads
the load through `base_load_supplier`. Advancing it here, against the engine's
own simulation time, is also what keeps the load's daily profile in phase with
the irradiance and ambient temperature models — `SitePowerModel` keeps a
private clock that starts at zero and ignores `start_time_h`, so a supplier
keyed off that argument would have sat hours out of phase.

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
- Stochastic models are seedable: set `cloud.seed`, `load.seed`,
  `grid_frequency.seed` and `grid_voltage.seed` for a reproducible run. The
  cloud regime and the load drift are keyed on simulated time, so they are
  reproducible *per simulated date* and survive a restart; see `clouds.py` and
  `load.py` above.
- The update sequence guarantees world → devices → telemetry.
- Supports both constant and stochastic/deterministic variations.
- Provides configurable defaults for quick prototyping.