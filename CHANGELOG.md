# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.3] — 2026-11-05

### Added

- **A live irradiance series.** `irradiance.series_refresh_s` turns
  `series_csv` from a file read once at build time into a live input: dertwin
  re-reads it on that interval of simulated time, so an external fetcher — an
  Open-Meteo sidecar, a plant's historian, anything that can write a CSV — can
  drive the simulated sky from real weather. Only the file is re-read; nothing
  else in the config is. The re-read costs one `stat` per interval unless the
  file has actually moved, and a snapshot whose file changed while it was being
  read is thrown away and retried, so a writer that appends in place cannot
  feed the simulation half of two versions. The documented contract asks for a
  temporary file and an atomic `rename`, under which a torn read cannot happen
  at all.

- **A stochastic fallback when the series goes stale.**
  `irradiance.series_stale_after_s` is how long the last row keeps being served
  once simulated time has passed it — an hour by default in live mode, because
  a fetcher writing hourly rows is always a little behind. Past that, or with a
  file that is missing or unreadable, the **stochastic cloud model** takes over.
  Not a dark sky and not a cloudless one: a dark sky tells an EMS the plant has
  failed and a cloudless one tells it the plant is at nameplate, while the
  stochastic model tells it the weather is ordinary, which is the only honest
  answer when the measurement is gone. A file that is not there at startup is
  now a warning rather than a failure to start, and is picked up when it
  appears. Each transition is one log line, in both directions.

- **A handover with no step in it.** What crosses a switch is the clear-sky
  index, not W/m², and it is carried by one rule in both directions: the last
  index reported is held and relaxed away at the cloud model's own
  `intraday_tau_s`. Into the fallback that is exact and needs no new
  machinery — the AR(1) model's intraday perturbation is seeded with the
  difference from the day's regime, which is the part of the model that already
  decays at that rate, and the day's regime is deliberately left alone so a
  measurement ending on a bright minute cannot turn the whole day clear. Coming
  back, an explicit offset with the same time constant decays onto the
  measurement. The switch tick is a step of zero either way, and every step
  after it is one the stochastic model could have produced itself.
  `series_handover_tau_s: 0` turns it off and makes each switch a plain jump.
  Because the index is what carries over, the beam/diffuse split is taken from
  the GHI being reported and not from the file's: Erbs is strongly non-linear
  in the clear-sky index, so splitting the file's GHI and scaling the
  components afterwards would hold GHI continuous while stepping plane-of-array
  irradiance, and so PV power, at the switch. Measured `dni_w_m2` / `dhi_w_m2`
  are believed instead and scaled with GHI, which keeps the file's beam
  fraction and its closure.

- **Optional measured beam and diffuse columns.** `dni_w_m2` and `dhi_w_m2`
  (names configurable) are used when the header carries them. Both given are
  taken as they stand and are *not* adjusted to close with GHI — measured
  triples rarely close, and quietly fixing one of three measured numbers would
  hide a data problem. One given derives the other from GHI; neither falls back
  to the Erbs split, as before.

- **`irradiance.series_max_gap_s`.** A gap inside the series wider than this is
  missing data — and so falls back — rather than something to draw a straight
  line across. `0`, the default, bridges any gap, which is what interpolation
  has always done.

- **Defined behaviour for awkward rows.** Duplicate timestamps resolve to the
  last row in the file, so a correction appended later overrides the row it
  corrects. Out-of-order rows are sorted on read. A blank cell is a row that
  column does not have, so a half-filled DNI column still leaves a complete
  GHI series. In live mode an unparseable timestamp or value is counted, logged
  and skipped: one torn line cannot take the simulation with it. A file missing
  its header or its driving column is unusable, and the previous snapshot is
  kept rather than discarded.

### Unchanged

- **With none of the new keys, behaviour is identical to 0.2.2.**
  `series_refresh_s` defaults to `0`, which is the one-shot read the key has
  always had, and with no staleness configured no fallback model is constructed
  at all — so nothing draws from the random streams and nothing can reach the
  output but the file. A missing or malformed file in one-shot mode is still an
  error at build time, and an instant outside the series is still dark for a
  GHI series and clear sky for a `kt` one. Every shipped configuration runs
  untouched.

### Not in this release

- **Ambient temperature from the series.** The columns would be easy; the model
  behind them is not. Site ambient feeds the BESS thermal model added in 0.2.2,
  whose HVAC has no hysteresis, so a temperature that steps on every re-read
  would chatter the heater and the cooler against each other. Worth doing after
  the thermal model has a deadband.

## [0.2.2] — 2026-11-05

### Added

- **Battery thermal management.** A `thermal` block on a BESS asset puts the
  pack in a container whose HVAC holds the cell inside
  `[heat_setpoint_c, cool_setpoint_c]` (15 °C and 30 °C by default). Inside the
  band the cell relaxes toward ambient with an explicit time constant,
  `thermal.time_constant_s`, which defaults to the pack's own `C_th / G` —
  2500 s with the auto-scaled thermal defaults, whatever the pack size. It is
  never allowed to leave the band, so a site whose ambient spends the winter in
  the cold derate keeps full nameplate capability instead of `0.8×`. Managed
  packs also exchange heat with ambient in *both* directions: an unmanaged cell
  is floored at ambient and so can never be below it, while a heated one can,
  and has to be let back down when ambient climbs.

- **Optional HVAC auxiliary draw.** `thermal.aux_power_kw` is the container's
  own consumption while heating or cooling is active, `0` by default. It is
  published as `hvac_power_kw` on the BESS and enters `SitePowerModel` through
  a new `aux_load_supplier`, on the **load** side of the balance exactly as the
  site load is — so it reaches grid power, the import counter and the meter,
  and is never netted off the BESS `active_power`. A discharging managed pack
  still reports what its inverter delivered.

- **Configurable temperature derate curve.** `thermal.derate.bands` is a ladder
  of `[upper bound °C, factor]` pairs with `derate.above_factor` above the last
  bound. The defaults are the previously hardcoded curve (`≤ 0 → 0.5`,
  `≤ 10 → 0.8`, `≤ 40 → 1.0`, `≤ 50 → 0.8`, `≤ 60 → 0.5`, above → `0.0`), so
  no existing configuration moves.

- Config validation for the new block: an inverted setpoint band, a cool
  setpoint above the 80 °C cell limit, a negative `aux_power_kw`, a
  non-positive time constant, and a derate ladder whose bounds do not increase
  or whose factors fall outside `[0, 1]` are all rejected at build time with a
  message naming the key.

- A golden test that steps a default 0.2.2 battery and one carrying the 0.2.1
  thermal code verbatim side by side for a simulated day — idle, discharge,
  charge, idle, under an ambient that sweeps 4–14 °C — comparing applied power,
  SOC, cell temperature, the capability window and the lifetime counters on
  every step.

### Unchanged

- **With no `thermal` block, behaviour is identical to 0.2.1.** `managed`
  defaults to `false`, `aux_power_kw` to `0`, and the derate curve to the one
  the model already had. Every shipped configuration runs untouched.
- Cell temperature is still reported in `battery_temperature` and still capped
  at 80 °C, and ambient still comes from the external temperature model, every
  tick.

## [0.2.1] — 2026-11-04

### Fixed

- **The weather of a simulated date no longer depends on when the process
  started.** The AR(1) cloud regime was seeded once at engine start and stepped
  from there, so every boot replayed the same first day: a deployment that
  restarts a couple of times a day got the same opening sky every time and
  practically never saw a clear day. The daily clear-sky index is now keyed on
  the UTC calendar date — with a `cloud.seed` it is a function of
  `(seed, date)`, so a restart, the hour it comes back at, and the date the run
  starts on cannot change it. Day-to-day persistence `phi` and the long-run
  mean `mean_kt` are unchanged: the regime is read out of a keyed AR(1) chain,
  not drawn independently per date.

- **The site load's slow drift had the same defect** and is fixed the same way.
  It started at a stationary draw from the seed, so every boot began at the
  same factor and replayed the same "busy afternoon". The drift factor is now a
  function of `(seed, simulated instant)`, sampled on a fixed internal lattice
  of `tau_s / 16`, which also makes `sigma` and `tau_s` independent of the
  simulator's step rather than merely insensitive to it. Keying on an absolute
  instant needs a top-level `sim_start_utc`; without one the clock itself
  restarts at zero and the drift restarts with it.

### Changed

- Each random term now draws from its own stream, derived from the one
  configured `seed`, so enabling or disabling a term no longer shifts another
  term's numbers. Seeded runs therefore produce different numbers than 0.2.0 —
  the statistics (`phi`, `mean_kt`, `sigma_day`, drift and noise spreads) are
  unchanged.
- The cloud model's day catch-up loop and its ten-thousand-day cap are gone. A
  date is one lookup whatever date it is, so a clock that jumps far ahead is now
  exact instead of resynced.
- The intraday cloud perturbation and the load's fast noise stay free-running,
  on purpose: both are zero-mean with a correlation time of minutes or less,
  and stepping them with the engine's own `dt` is what keeps their spread
  independent of the step size. A restart redraws them; it does not move the
  day's regime or the drift.

### Added

- `KeyedAR1` and `KeyedOrnsteinUhlenbeck` in
  `dertwin/devices/external/stochastic.py`: stationary processes whose value at
  a step, or at an instant, is a pure function of `(seed, step)` with no state
  carried between calls. Cost per evaluation is set by `phi` alone and does not
  grow with the step number.
- Tests for the restart contract: a mid-day restart of the engine reporting the
  same day and drift, a boot on any hour of the day agreeing, a run started 100
  days earlier agreeing about a later day, a jump of 10,000 days needing no
  catch-up, and five years of keyed days keeping the configured lag-1
  autocorrelation and mean within stated tolerances.

### Unchanged

- `cloud.series_csv` and `series_column: "kt"` replay: a recorded series was
  always a function of the date.
- Unseeded runs are still random per run. Within one process, a given date
  still reports one regime.
- Every configuration key, and every default.

## [0.2.0] — 2026-11-01

### Added

- Location- and date-aware clear-sky irradiance (Ineichen/Perez and Haurwitz,
  with an optional pvlib backend) and a stochastic cloud model with persistent
  day-to-day weather regimes.
- Stochastic site load: daily profile (flat, hourly, parametric or CSV),
  weekday/weekend factor, slow mean-reverting drift and fast noise.
- Hard SOC limits, realised round-trip efficiency and optional SOC persistence
  for the BESS.
- Two location-aware example site configurations, differing only by
  configuration.

[0.2.3]: https://github.com/AlexSpivak/dertwin/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/AlexSpivak/dertwin/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/AlexSpivak/dertwin/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/AlexSpivak/dertwin/releases/tag/v0.2.0
