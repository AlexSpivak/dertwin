"""
Optional SOC persistence across restarts.

Persistence is opt-in. Without a state_file nothing is read or written, and any
unusable state file falls back to the configured initial_soc with a log line
saying so.
"""

import json
import logging

import pytest

from dertwin.devices.bess.simulator import BESSSimulator
from dertwin.devices.bess.state import SCHEMA_VERSION, load_state, save_state


def make_sim(tmp_path, **kwargs) -> BESSSimulator:
    defaults = dict(
        capacity_kwh=100.0,
        initial_soc=50.0,
        state_file=str(tmp_path / "bess.json"),
        asset_id="bess-0",
    )
    defaults.update(kwargs)
    return BESSSimulator(**defaults)


# ------------------------------------------------------------
# Round trip
# ------------------------------------------------------------

def test_state_round_trip_restores_soc(tmp_path):
    first = make_sim(tmp_path)
    first.battery.energy_kwh = first.battery.energy_for_soc(63.25)
    first.battery.charge_energy_total_kwh = 412.8
    first.battery.discharge_energy_total_kwh = 389.1
    first.battery.cycles = 4.01
    first.battery.soh = 99.98
    assert first.save_state() is True

    second = make_sim(tmp_path, initial_soc=50.0)

    assert second.soc == pytest.approx(63.25, rel=1e-9)
    assert second.battery.charge_energy_total_kwh == pytest.approx(412.8)
    assert second.battery.discharge_energy_total_kwh == pytest.approx(389.1)
    assert second.battery.cycles == pytest.approx(4.01)
    assert second.battery.soh == pytest.approx(99.98)


def test_state_file_is_readable_json_with_schema(tmp_path):
    sim = make_sim(tmp_path)
    sim.save_state()

    data = json.loads((tmp_path / "bess.json").read_text())

    assert data["schema"] == SCHEMA_VERSION
    assert data["asset_id"] == "bess-0"
    assert data["soc_percent"] == pytest.approx(50.0)
    assert "saved_utc" in data


def test_atomic_write_leaves_no_temp_file(tmp_path):
    sim = make_sim(tmp_path)
    sim.save_state()

    assert (tmp_path / "bess.json").exists()
    assert not (tmp_path / "bess.json.tmp").exists()


# ------------------------------------------------------------
# Fallbacks
# ------------------------------------------------------------

def test_first_boot_uses_initial_soc_and_logs(tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        sim = make_sim(tmp_path, initial_soc=71.0)

    assert sim.soc == pytest.approx(71.0)
    assert "no state file" in caplog.text


def test_corrupt_state_file_falls_back_and_warns(tmp_path, caplog):
    (tmp_path / "bess.json").write_text("{ this is not json")

    with caplog.at_level(logging.WARNING):
        sim = make_sim(tmp_path, initial_soc=71.0)

    assert sim.soc == pytest.approx(71.0)
    assert "unreadable" in caplog.text


def test_unknown_schema_falls_back_and_warns(tmp_path, caplog):
    (tmp_path / "bess.json").write_text(
        json.dumps({"schema": 999, "energy_kwh": 10.0})
    )

    with caplog.at_level(logging.WARNING):
        sim = make_sim(tmp_path, initial_soc=71.0)

    assert sim.soc == pytest.approx(71.0)
    assert "schema" in caplog.text


def test_missing_energy_field_falls_back_and_warns(tmp_path, caplog):
    (tmp_path / "bess.json").write_text(
        json.dumps({"schema": SCHEMA_VERSION, "soc_percent": 42.0})
    )

    with caplog.at_level(logging.WARNING):
        sim = make_sim(tmp_path, initial_soc=71.0)

    assert sim.soc == pytest.approx(71.0)
    assert "energy_kwh" in caplog.text


def test_capacity_change_preserves_soc_and_warns(tmp_path, caplog):
    first = make_sim(tmp_path, capacity_kwh=100.0)
    first.battery.energy_kwh = first.battery.energy_for_soc(60.0)
    first.save_state()

    with caplog.at_level(logging.WARNING):
        second = make_sim(tmp_path, capacity_kwh=200.0, initial_soc=50.0)

    assert second.soc == pytest.approx(60.0, rel=1e-9)
    assert second.battery.energy_kwh == pytest.approx(120.0, rel=1e-9)
    assert "capacity changed" in caplog.text


# ------------------------------------------------------------
# Disabled by default
# ------------------------------------------------------------

def test_persistence_disabled_by_default(tmp_path):
    sim = BESSSimulator(capacity_kwh=100.0, initial_soc=50.0)

    assert sim.state_path is None
    assert sim.save_state() is False
    assert list(tmp_path.iterdir()) == []


def test_unwritable_path_is_logged_not_raised(tmp_path, caplog):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")

    with caplog.at_level(logging.WARNING):
        ok = save_state(blocker / "bess.json", {"energy_kwh": 1.0}, asset_id="bess-0")

    assert ok is False
    assert "could not save state" in caplog.text


# ------------------------------------------------------------
# Periodic saving
# ------------------------------------------------------------

def test_state_saved_on_interval_during_update(tmp_path):
    sim = make_sim(tmp_path, state_save_interval_s=10.0)
    path = tmp_path / "bess.json"

    for _ in range(9):
        sim.update(1.0)
    assert not path.exists()

    sim.update(1.0)
    assert path.exists()


def test_load_state_returns_none_for_missing_file(tmp_path):
    assert load_state(tmp_path / "nope.json", asset_id="bess-0") is None
