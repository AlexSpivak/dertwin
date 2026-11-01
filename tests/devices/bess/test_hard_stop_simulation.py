"""
Deterministic end-to-end simulation of the hard SOC stop.

Runs the real engine with a BESS and a site meter, holds a charge setpoint, and
checks that SOC lands exactly on the ceiling, that reported power then reads
exactly 0, that the setpoint register is untouched, and that the site power
balance follows actual power rather than the setpoint.
"""

import pytest

from dertwin.controllers.site_controller import SiteController


BASE_LOAD_KW = 10.0
STEP_S = 1.0


def build_site(**bess_overrides) -> SiteController:
    bess = {
        "type": "bess",
        "asset_id": "bess-0",
        "capacity_kwh": 10.0,
        "initial_soc": 94.0,
        "max_charge_kw": 50.0,
        "max_discharge_kw": 50.0,
        "ramp_rate_kw_per_s": 1000.0,
        "soc_min_percent": 10.0,
        "soc_max_percent": 95.0,
    }
    bess.update(bess_overrides)

    cfg = {
        "site_name": "hard-stop-sim",
        "step": STEP_S,
        "real_time": False,
        "external_models": {"power": {"base_load_w": BASE_LOAD_KW * 1000.0}},
        # No "port" on either asset: device-only, no Modbus server needed.
        "assets": [bess, {"type": "energy_meter", "asset_id": "meter-0"}],
    }

    site = SiteController(cfg)
    site.build()
    return site


def dispatch(site: SiteController, setpoint_kw: float):
    bess = site._devices_by_type["bess"][0]
    bess.apply_commands({"start_stop_standby": 1})
    bess.apply_commands({"active_power_setpoint": setpoint_kw})
    return bess


async def run_ticks(site: SiteController, ticks: int) -> list[dict]:
    """Step the engine and record what each tick reported."""
    bess = site._devices_by_type["bess"][0]
    meter = site._devices_by_type["energy_meter"][0]
    power_model = site.external_models.power_model

    history = []
    for _ in range(ticks):
        await site.engine.step_once()
        history.append({
            "soc": bess.get_telemetry().system_soc,
            "active_power": bess.get_telemetry().active_power,
            "setpoint": bess.inverter.target_power,
            "grid_power": power_model.grid_power_kw,
            "meter_power": meter.get_telemetry().total_active_power,
        })
    return history


# ------------------------------------------------------------
# Charging into the ceiling
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_soc_lands_exactly_on_max_and_power_reads_zero():
    site = build_site()
    bess = dispatch(site, -50.0)

    history = await run_ticks(site, 300)

    # Never crosses the limit on any tick
    assert max(row["soc"] for row in history) <= 95.0 + 1e-9

    # Lands exactly on it
    assert bess.get_telemetry().system_soc == pytest.approx(95.0, abs=1e-9)
    assert bess.battery.energy_kwh == pytest.approx(9.5, abs=1e-9)

    # Once there, reported power is exactly zero and stays there
    limit_tick = next(
        i for i, row in enumerate(history)
        if row["soc"] == pytest.approx(95.0, abs=1e-9)
    )
    assert all(row["active_power"] == 0.0 for row in history[limit_tick + 1:])

    # ...while the setpoint is still what the EMS wrote
    assert all(row["setpoint"] == -50.0 for row in history)


@pytest.mark.asyncio
async def test_site_balance_follows_actual_power_not_setpoint():
    site = build_site()
    dispatch(site, -50.0)

    history = await run_ticks(site, 300)

    # Charging at 50 kW shows up as 60 kW of import early on...
    assert max(row["grid_power"] for row in history) == pytest.approx(
        BASE_LOAD_KW + 50.0, rel=1e-9
    )

    # ...and once the pack is full the site draws base load only, even though
    # the setpoint is still asking for 50 kW of charge.
    assert history[-1]["grid_power"] == pytest.approx(BASE_LOAD_KW, rel=1e-12)
    assert history[-1]["meter_power"] == pytest.approx(BASE_LOAD_KW, rel=1e-12)


@pytest.mark.asyncio
async def test_meter_import_energy_matches_integrated_grid_power():
    site = build_site()
    dispatch(site, -50.0)

    history = await run_ticks(site, 300)
    meter = site._devices_by_type["energy_meter"][0]

    expected_kwh = sum(row["grid_power"] for row in history) * STEP_S / 3600.0

    assert meter.get_telemetry().total_import_energy == pytest.approx(
        expected_kwh, rel=1e-9
    )


# ------------------------------------------------------------
# Discharging into the floor
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_soc_lands_exactly_on_min_and_power_reads_zero():
    site = build_site(initial_soc=11.0)
    bess = dispatch(site, 50.0)

    history = await run_ticks(site, 300)

    assert min(row["soc"] for row in history) >= 10.0 - 1e-9
    assert bess.get_telemetry().system_soc == pytest.approx(10.0, abs=1e-9)
    assert history[-1]["active_power"] == 0.0
    assert history[-1]["setpoint"] == 50.0

    # Discharge gone, so the site is back to importing base load
    assert history[-1]["grid_power"] == pytest.approx(BASE_LOAD_KW, rel=1e-12)


# ------------------------------------------------------------
# Determinism
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_runs_are_identical():
    def dispatched_site() -> SiteController:
        site = build_site()
        dispatch(site, -50.0)
        return site

    assert await run_ticks(dispatched_site(), 120) == await run_ticks(
        dispatched_site(), 120
    )


# ------------------------------------------------------------
# Persistence across a restart
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_soc_survives_a_restart(tmp_path):
    state_file = str(tmp_path / "bess-0.json")

    site = build_site(initial_soc=94.0, state_file=state_file)
    dispatch(site, -50.0)
    await run_ticks(site, 300)

    # stop() flushes device state
    site._running = True
    await site.stop()

    resumed = build_site(initial_soc=20.0, state_file=state_file)

    assert resumed._devices_by_type["bess"][0].soc == pytest.approx(95.0, abs=1e-9)


@pytest.mark.asyncio
async def test_without_state_file_restart_is_a_cold_start(tmp_path):
    site = build_site(initial_soc=94.0)
    dispatch(site, -50.0)
    await run_ticks(site, 300)
    site.save_device_state()

    resumed = build_site(initial_soc=20.0)

    assert resumed._devices_by_type["bess"][0].soc == pytest.approx(20.0)
    assert list(tmp_path.iterdir()) == []
