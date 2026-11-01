"""
The absolute UTC clock.

Simulation time is UTC internally. With no sim_start_utc the clock sits at the
Unix epoch, so start_time_h keeps meaning "hours past midnight" exactly as it
did before absolute time existed.
"""

from datetime import datetime, timezone

import pytest

from dertwin.controllers.site_controller import SiteController
from dertwin.core.clock import DEFAULT_START_UTC, SimulationClock, parse_utc


# ------------------------------------------------------------
# parse_utc
# ------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "2025-10-31T00:00:00Z",
    "2025-10-31T00:00:00z",
    "2025-10-31T00:00:00+00:00",
    "2025-10-31T00:00:00",
    "2025-10-31T01:00:00+01:00",
    "2025-10-30T22:00:00-02:00",
    datetime(2025, 10, 31, tzinfo=timezone.utc),
])
def test_parse_utc_accepts_equivalent_spellings(value):
    assert parse_utc(value) == datetime(2025, 10, 31, tzinfo=timezone.utc)


def test_parse_utc_treats_a_naive_value_as_utc():
    assert parse_utc("2025-10-31T12:00:00").tzinfo == timezone.utc


@pytest.mark.parametrize("value", ["not-a-date", "2025-13-01T00:00:00Z", ""])
def test_parse_utc_rejects_nonsense(value):
    with pytest.raises(ValueError, match="Invalid ISO 8601"):
        parse_utc(value)


# ------------------------------------------------------------
# SimulationClock
# ------------------------------------------------------------

def test_clock_defaults_to_the_unix_epoch():
    clock = SimulationClock(step=1.0, real_time=False)

    assert clock.start_utc == DEFAULT_START_UTC
    assert clock.now_utc() == datetime(1970, 1, 1, tzinfo=timezone.utc)


def test_now_utc_advances_with_simulation_time():
    clock = SimulationClock(step=1.0, real_time=False,
                            start_utc="2025-10-31T00:00:00Z")
    clock.time = 10 * 3600 + 31 * 60

    assert clock.now_utc() == datetime(2025, 10, 31, 10, 31, tzinfo=timezone.utc)


def test_clock_accepts_a_datetime_start():
    start = datetime(2025, 6, 21, tzinfo=timezone.utc)
    clock = SimulationClock(step=1.0, real_time=False, start_utc=start)

    assert clock.now_utc() == start


def test_reset_returns_to_the_start_instant():
    clock = SimulationClock(step=1.0, real_time=False,
                            start_utc="2025-10-31T00:00:00Z")
    clock.time = 12345.0
    clock.reset()

    assert clock.now_utc() == datetime(2025, 10, 31, tzinfo=timezone.utc)


# ------------------------------------------------------------
# Wiring through the site controller
# ------------------------------------------------------------

def build(**config):
    cfg = {"site_name": "clock", "step": 1.0, "real_time": False, "assets": []}
    cfg.update(config)
    site = SiteController(cfg)
    site.build()
    return site


def test_sim_start_utc_anchors_the_clock():
    site = build(sim_start_utc="2025-10-31T06:00:00Z")

    assert site.clock.now_utc() == datetime(2025, 10, 31, 6, tzinfo=timezone.utc)


def test_start_time_h_offsets_from_the_configured_date():
    site = build(sim_start_utc="2025-10-31T00:00:00Z", start_time_h=12.0)

    assert site.clock.time == pytest.approx(12 * 3600.0)
    assert site.clock.now_utc() == datetime(2025, 10, 31, 12, tzinfo=timezone.utc)


def test_start_time_h_alone_keeps_its_original_meaning():
    """No date configured: hours past midnight, as it has always been."""
    site = build(start_time_h=12.0)

    assert site.clock.time == pytest.approx(12 * 3600.0)
    assert site.clock.now_utc() == datetime(1970, 1, 1, 12, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_engine_publishes_the_utc_instant_each_tick():
    site = build(sim_start_utc="2025-10-31T00:00:00Z")

    await site.engine.step_once()
    assert site.engine.utc_time == datetime(2025, 10, 31, tzinfo=timezone.utc)

    await site.engine.step_once()
    assert site.engine.utc_time == datetime(
        2025, 10, 31, 0, 0, 1, tzinfo=timezone.utc
    )
