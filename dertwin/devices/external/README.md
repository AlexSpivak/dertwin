# External Simulation Models for DER Systems

This package provides deterministic **external world models** for simulating distributed energy resources (DER) such as PV inverters, battery energy storage systems (BESS), and grid-connected sites. It includes ambient conditions, irradiance, grid frequency and voltage, and site-level power flow models.  

All models are designed to be **deterministic**, fully **simulation-time driven**, and **modular**, allowing flexible integration into DER simulations.

---

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Modules](#modules)
  - [ambient_temperature.py](#ambient_temperaturepy)
  - [irradiance.py](#irradiancepy)
  - [grid_frequency.py](#grid_frequencypy)
  - [grid_voltage.py](#grid_voltagepy)
  - [power_flow.py](#power_flowpy)
  - [external_models.py](#external_modelspy)
- [Usage Example](#usage-example)
- [License](#license)

---

## Features

- **Ambient conditions:** Deterministic daily temperature profiles
- **Solar irradiance:** Clear-sky sinusoidal irradiance curves
- **Grid frequency & voltage:** Drift, noise, events, and deterministic disturbances
- **Site power flow:** Aggregates load, PV, and BESS contributions
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

**Constant variant**: ConstantGridFrequencyModel returns a fixed frequency.

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

**Constant variant**: ConstantGridVoltageModel returns a fixed voltage.

### `power_flow.py`

**Class:** `SitePowerModel`

Simulates **site-level power balance** including load, PV generation, and BESS.

**Features:**
- Aggregates base load, PV, and BESS contributions
- Computes net grid power
- Tracks import/export energy over time
- Fully deterministic

**Key methods:**
```python
update(dt: float)
get_sim_time() -> float
```

**Usage:**
```python
model = SitePowerModel(
    base_load_supplier=lambda t: 5.0,
    pv_supplier=lambda: 10.0,
    bess_supplier=lambda: -2.0
)
model.update(dt=60)
grid_power = model.grid_power_kw
```

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
update(sim_time: float, dt: float)
build_power_model(devices_by_type, config=None)
build_default()
from_config(config: Dict)
```

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
    print(f"Time {t/3600:.1f}h: Temp={atm.get_temperature():.2f}°C")
```

## Deterministic Simulation Notes
- All models are simulation-time driven.
- The update sequence guarantees world → devices → telemetry.
- Supports both constant and stochastic/deterministic variations.
- Provides configurable defaults for quick prototyping.
