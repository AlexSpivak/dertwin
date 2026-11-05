# BESS Package

The bess package implements a complete Battery Energy Storage System model, including physical behavior, AC control, orchestration, and simulator integration.
It is structured in clear layers to separate physics, control dynamics, and protocol interaction.

## Files Overview
### battery.py

**DC-side battery physics model**

Responsible for deterministic energy behavior:
- Energy tracking (kWh)
- State of Charge (SOC)
- Charge / discharge efficiency
- SOC-based power limits (hard stop, or legacy linear derating)
- Temperature-based power limits, with a configurable derate curve
- Optional self-discharge
- Thermal model, optionally with a managed container (HVAC setpoint band)
- Cycle counting
- State of health tracking
- Persistable state snapshot

The battery model:
- Does not know about ramping
- Does not know about protocols
- Only applies physical constraints

#### SOC limits

`limit_mode` selects how the SOC window is defended.

**`"hard"` (default)** — no tapering anywhere. In the step that would cross a
limit, power is clamped so SOC lands *exactly* on it; from then on power is
exactly `0.0` while the setpoint keeps pushing that way. The allowed power is
computed from the energy headroom, not from a scale factor:

```
charge:     P_allowed = -min(|P|, (E_max - E) / (eta_c · dt))
discharge:  P_allowed = +min( P,  (E - E_min) · eta_d / dt)
```

`soc_min_percent` / `soc_max_percent` set the window. When they are omitted it
follows `soc_limits.lower_2` / `soc_limits.upper_2`, which is also what the
Modbus `soc_lower_limit_2` / `soc_upper_limit_2` commands write — so an EMS can
move the hard limits at runtime. The `_1` thresholds are accepted and ignored.

**`"taper"`** — the pre-0.2 behaviour: a linear charge/discharge derating ramp
between the `_1` and `_2` thresholds, with the hard cutoff at `_2`.

#### Efficiency

A configured `round_trip_eff` is actually realised, because charging stores
less than arrives at AC and discharging drains more than reaches AC:

```
charge:     dE = +P_ac · eta_c · dt
discharge:  dE = -P_ac / eta_d · dt
```

`eta_c = eta_d = sqrt(round_trip_eff)` unless `charge_efficiency` /
`discharge_efficiency` are given, which override it per direction. Before 0.2
both legs multiplied, so the realised round-trip efficiency was silently 1.0
whatever was configured.

Energy counters (`total_charge_energy`, `total_discharge_energy`) are
battery-side — what actually moved in and out of the pack — which is what the
cycle counter and SOH are defined against.

#### Self-discharge

`self_discharge_percent_per_day` is a passive loss applied every step, floored
at the hard discharge limit: a BMS defends its reserve rather than letting idle
losses walk through it. Off by default.

#### Thermal model and capability

The cell is one lumped node. Every step adds Joule heat `I²R·dt` and exchanges
with ambient across `thermal_conductance_w_per_k`, over
`thermal_capacity_j_per_k`, giving a time constant `tau = C_th / G` — 2500 s
with the auto-scaled defaults, for any pack size. Ambient comes from the
external `AmbientTemperatureModel` when the site has one, and is refreshed
every tick.

`thermal.derate` is the capability curve: a ladder of `(upper bound °C,
factor)` bands plus an `above_factor`, defaulting to the curve this model has
always had (`≤ 0 → 0.5`, `≤ 10 → 0.8`, `≤ 40 → 1.0`, `≤ 50 → 0.8`,
`≤ 60 → 0.5`, above → `0.0`). The factor scales charge and discharge alike and
reaches the `available_*_power` registers through `get_power_limits()`.

**Unmanaged** (`thermal.managed = false`, the default): the exchange term only
cools (`max(0, T − T_amb)`) and the result is floored at ambient, so an idle
pack sits exactly at ambient — and a pack on a 4 °C site sits in the `0.8×`
cold derate.

**Managed** (`thermal.managed = true`): two changes, both deliberate.

1. The exchange is **signed**, so the cell relaxes toward ambient in both
   directions. A heated cell is above ambient and has to be allowed to fall
   back when ambient rises; the floor at ambient would have pinned it.
2. The result cannot leave `[heat_setpoint_c, cool_setpoint_c]`. The HVAC is
   ideal inside the band: it supplies or removes exactly the heat needed to
   hold the nearest edge. A pack that starts outside the band is therefore
   brought to that edge in one step rather than over a ramp.

The relaxation uses `thermal.time_constant_s` when set, otherwise `C_th / G`;
the conductance is backed out of it as `G = C_th / tau`, so configuring the
time constant configures the dynamics directly.

`thermal.aux_power_kw` is the container's draw for as long as the band is what
stopped the cell from going where its own dynamics were taking it. It is
published as `hvac_power_kw` and summed into `SitePowerModel` through its
`aux_load_supplier`, on the load side — it is site load, not inverter power, so
it is never netted off `active_power`.

### inverter.py
**AC-side power control model**

Responsible for grid-facing behavior:
- Target power handling
- Charge / discharge power clamping
- Ramp-rate limiting (kW/s)
- Active power tracking
- Reactive power calculation
- Apparent power calculation
- Grid frequency reporting

The inverter model:
- Does not know about SOC
- Does not enforce battery limits
- Only manages AC dynamics

### bess.py

**Physical orchestration layer**

Coordinates battery and inverter behavior into a single physical plant.

On each simulation step:

1. The inverter ramps toward the setpoint — the setpoint itself is never
   rewritten, so dispatch resumes on its own once the pack has headroom again
2. The battery clamps that ramped value: rating, temperature capability and the
   hard SOC window are all applied in one place
3. The inverter is synced to the power the battery actually accepted
4. Composed telemetry is produced

Step 3 matters: without it the next ramp step would start from a value the pack
never delivered, and reactive/apparent power would be derived from it too.

This layer ensures:
- The battery is the single clamp authority
- Reported active, reactive and apparent power all follow actual power
- The site power balance and meter see actual power, not the setpoint
- Telemetry represents real internal state

`BESSModel` acts as the physical plant abstraction.

### controller.py

**Device-level command and telemetry bridge**

Responsible for:
- Receiving external commands
- Detecting command changes
- Applying commands to the physical model
- Synchronizing startup state without mutating defaults
- Exposing telemetry to external protocols

This layer separates:
- Protocol logic
- Device state management
- Physical simulation

### simulator.py

**Runtime integration wrapper**

Wraps the controller into a runnable simulated device.

Provides:
- Execution loop
- Time stepping
- Exposing Modbus-accessible values
- Startup initialization
- Telemetry publishing
- Optional SOC persistence

This is the entry point used by site-level simulations.

### state.py

**Optional SOC persistence**

Opt-in: with no `state_file` nothing is read or written. When set, SOC and the
lifetime counters survive a restart instead of cold-starting at `initial_soc`.

- Written atomically (temp file + `os.replace`) every
  `state_save_interval_s` and once more on `SiteController.stop()`
- Versioned JSON; a file with an unknown `schema` is ignored
- First boot, an unreadable file, bad JSON or missing fields all fall back to
  the configured `initial_soc` and log *why* — first boot at `INFO`, every
  other case at `WARNING`
- A changed `capacity_kwh` preserves SOC and rescales energy, with a `WARNING`

## Architectural Principles

The BESS package follows strict separation of concerns:
- BatteryModel → DC physics, efficiency and the SOC window
- InverterModel → AC control and ramp behavior
- BESSModel → Physical coordination
- Controller → Command lifecycle management
- Simulator → Runtime execution

This structure ensures:
- Deterministic physical behavior
- Clear ownership of limits and ramp logic
- Safe startup initialization
- Clean separation between physics and protocol layers
- Extensibility for future control modes (remote, grid-forming, etc.)

This package represents a complete, modular BESS simulation stack suitable for integration into multi-device site controllers.