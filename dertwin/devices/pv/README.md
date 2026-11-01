# PV Package

The `pv` package implements a photovoltaic inverter simulation including DC irradiance response, AC conversion behavior, ramp dynamics, and simulator integration.

It is structured in layered components to separate generation physics, inverter dynamics, orchestration, and runtime integration.

## Files Overview
### panel.py

**DC-side photovoltaic array model**

Responsible for:
- In-plane irradiance, including plane-of-array transposition for a tilted,
  oriented array
- Irradiance-based DC production
- Cell temperature (NOCT approximation) and temperature derating
- System loss derate

#### DC sizing

Two paths, because the capacity form is what people actually have on a
datasheet and the area form is what the model shipped with:

| Input | Formula |
|---|---|
| `dc_capacity_kw` | `dc_capacity_kw · (POA / 1000) · temp_factor · derate` |
| `area_m2`, `module_efficiency` | `POA · area_m2 · module_efficiency · temp_factor · derate` |

They are the same nameplate when `dc_capacity_kw == area_m2 · module_efficiency`,
so the two agree exactly for an equivalent array.

#### Tilt and azimuth

Transposition happens only when `tilt_deg` is set **and** the irradiance source
knows where the sun is. Otherwise the incoming value is taken as already
in-plane — which is what the legacy sinusoidal irradiance model implies, and
what the model did before tilt existed. Configuring `tilt_deg` against
`irradiance.model: "sinusoidal"` logs one warning and falls back.

`azimuth_deg` is degrees clockwise from true north: 180 is due south. With
`tilt_deg` set and no azimuth, the array faces the equator; with an azimuth and
no tilt, it adopts a latitude tilt (capped at 60°).

At 59 N a 45° south-facing array collects over 30% more than a horizontal one
on a clear late-October day, which is exactly why steep tilts are used at high
latitudes — and why a model without tilt understates a northern winter.

### inverter.py

**AC-side inverter dynamics model**

Responsible for grid-facing behavior:
- Target power tracking
- Active power ramp-rate limiting
- Output clamping to rated AC power
- Reactive power calculation
- Apparent power calculation

The inverter model:
- Does not know about irradiance physics
- Does not manage environmental inputs
- Only controls AC output behavior

### pv.py

**Physical orchestration layer**

Coordinates DC generation and AC conversion into a single plant model.

On each simulation step:
- Receives irradiance input
- Computes available DC power
- Applies inverter ramp constraints
- Produces physically consistent AC output
- Integrates daily energy production
- Generates composed telemetry

This ensures:
- Ramp limits are respected
- AC output never exceeds available DC power
- Telemetry reflects realistic inverter behavior 

`PVModel` represents the full photovoltaic plant abstraction.

`today_energy_kwh` is zeroed at the UTC day boundary, which the PV simulator
detects from the instant the irradiance model was last advanced to — so
`today_output_energy` means today, not "since the process started".

### controller.py

**Device-level command and telemetry bridge**

Responsible for:
- Receiving external curtailment or control commands
- Detecting command changes
- Applying commands to inverter model
- Exposing telemetry to external protocols
- Maintaining safe startup state

Separates:
- Protocol logic
- Device lifecycle
- Physical simulation

### simulator.py

**Runtime integration wrapper**

Wraps the controller into a runnable simulated device.

Provides:
- Time stepping
- Execution loop integration
- Startup synchronization
- Telemetry publishing
- Exposing Modbus-accessible values

This is the entry point used by site-level simulations.

#### DC/AC sizing precedence

Highest first:

1. `dc_capacity_kw` — explicit STC DC nameplate
2. `area_m2 × module_efficiency` — the original formulation
3. `ac_rating_kw × dc_ac_ratio`
4. `ac_rating_kw` — DC/AC = 1.0

`rated_kw` is the AC rating and still works as the only sizing input, in which
case DC/AC stays 1.0 and the array can never reach its own rating, let alone
clip. Real plants run 1.15–1.35 and clip for hours in summer, so set
`dc_ac_ratio` or `dc_capacity_kw` for anything realistic.

Losses: `losses` is an itemised map whose values multiply out, and it wins over
a single `derate`.

## Architectural Principles

The PV package follows layered separation:
- PVArrayModel → DC generation physics
- InverterModel → AC dynamics and ramping
- PVModel → Physical coordination
- Controller → Command lifecycle
- Simulator → Runtime execution

This structure ensures:
- Deterministic solar response
- Proper ramp-rate enforcement
- Realistic inverter clipping
- Clear separation between environment and grid behavior
- Extensibility for future features (curtailment modes, voltage support, etc.)

The PV package represents a modular and extensible photovoltaic plant simulator suitable for multi-device site integration.