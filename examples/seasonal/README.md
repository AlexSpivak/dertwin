# Seasonal examples — Stockholm and Kyiv

Two sites, same code, same date, different config. Nothing in `dertwin/` knows
about either place: the latitude, longitude, tilt, turbidity, load shape and
cloud statistics are all config.

- [`configs/stockholm_site_config.json`](../../configs/stockholm_site_config.json)
  — 59.33 N, 40 kW DC on a 30 kW inverter at a 45° tilt, 120 kWh battery
- [`configs/kyiv_site_config.json`](../../configs/kyiv_site_config.json)
  — 50.45 N, 36 kW DC on a 30 kW inverter at a 35° tilt, 100 kWh battery

## Print a day

```bash
python examples/seasonal/plot_day.py stockholm --clear
```

Standard library only — no matplotlib — so it works straight after a clone. It
runs the engine in deterministic mode, so nothing binds a port and nothing
sleeps in real time.

```
  --date YYYY-MM-DD   UTC date (default 2025-10-31)
  --clear             disable the cloud model
  --interval MINUTES  minutes between printed rows (default 60)
  --step SECONDS      simulation timestep (default 60)
```

Pass `--clear` whenever you are comparing the two sites. With clouds on you are
comparing two climates *and* two random draws, not two latitudes.

## What the geography does

Clear-sky, from the two configs:

| | Stockholm 59.33 N | Kyiv 50.45 N |
|---|---|---|
| Daylight, 31 Oct | 8.72 h | 9.68 h |
| Daylight, 21 Jun | 18.28 h | 16.23 h |
| Daylight, 21 Dec | 5.73 h | 7.78 h |
| GHI peak, 31 Oct | 274 W/m² | 411 W/m² |
| GHI peak, 21 Jun | 839 W/m² | 905 W/m² |
| GHI peak, 21 Dec | 108 W/m² | 239 W/m² |

Nine degrees of latitude costs Stockholm an hour of daylight and a third of the
irradiance on the same late-October day, and more than half of it at the winter
solstice. Midsummer is the other way round: two hours more daylight, which is
why the summer *yields* are close despite the lower peak.

Before stage 2 of this work the irradiance model had no latitude at all — every
day of the year, everywhere on earth, was 12.0 h long and peaked at 1000 W/m².

## What the configs do, which is not the same thing

PV yield from these two configs is **not** a controlled comparison of
geography: the arrays are deliberately sized and tilted differently, as they
would be in reality.

| Clear-sky PV yield | Stockholm | Kyiv |
|---|---|---|
| 31 Oct | 138 kWh | 131 kWh |
| 21 Jun | 286 kWh | 256 kWh |
| 21 Dec | 52 kWh | 74 kWh |

Stockholm edges ahead in October and June — a steeper 45° tilt matched to a low
sun, 40 kW of DC against Kyiv's 36 kW, and a very long midsummer day — and
falls well behind in December, where no tilt rescues five and a half hours of
grazing sun. Read the table above for the resource and this one for the plant.

## Driving them with an EMS

These configs are simulators: the battery sits idle because nothing is
dispatching it, and SOC moves only by self-discharge. To exercise it, run a site
and point an EMS at the ports (55101–55103 for Stockholm, 55201–55203 for Kyiv):

```bash
dertwin --config configs/stockholm_site_config.json
```

See [`examples/simple`](../simple), [`examples/full`](../full) and
[`examples/mixed`](../mixed) for EMS clients.

Both sites persist SOC to `state/` (via `state_root` in the config), so a
restart resumes where it left off. Delete the file to cold-start at
`initial_soc`.
