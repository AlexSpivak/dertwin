"""
Graceful shutdown of the simulator process.

SOC persistence is only as good as the shutdown that flushes it. These tests
drive `run_site` with real signals, because the original bug was invisible to
every test that called `stop()` directly: `start()` absorbed the cancellation,
so `stop()` was simply never reached on Ctrl-C and nothing was ever written.

Every site here sets `state_save_interval_s` to a day, so the state file
cannot appear from the periodic save. If it exists, `stop()` ran.
"""

import asyncio
import json
import os
import signal
from pathlib import Path

import pytest

from dertwin.controllers.site_controller import SiteController
from dertwin.main import run_site


INITIAL_SOC = 43.0


def write_config(tmp_path: Path, **overrides) -> Path:
    asset = {
        "type": "bess",
        "asset_id": "shutdown-bess",
        "capacity_kwh": 100.0,
        "initial_soc": INITIAL_SOC,
        "state_file": "shutdown-bess.json",
        # A day, so only stop() can write the file.
        "state_save_interval_s": 86400.0,
    }
    asset.update(overrides)

    config = {
        "site_name": "shutdown-test",
        "step": 0.01,
        "real_time": True,
        "state_root": "state",
        "external_models": {"power": {"base_load_w": 10000.0}},
        # No "protocols": device-only, so no port is bound.
        "assets": [asset],
    }

    (tmp_path / "state").mkdir(exist_ok=True)
    path = tmp_path / "site.json"
    path.write_text(json.dumps(config))
    return path


def state_file(tmp_path: Path) -> Path:
    return tmp_path / "state" / "shutdown-bess.json"


async def start_site(config_path: Path, timeout: float = 5.0) -> asyncio.Task:
    """
    Start `run_site` and return once it owns the shutdown signals.

    Readiness has to be observed rather than assumed: signalling before the
    handler is installed kills the test runner instead of the site, and a
    fixed sleep would race on a loaded machine. So watch SIGINT's disposition
    change. Note it does not start out as `default_int_handler` — the asyncio
    runner installs one of its own — so the check is against whatever was
    there before, not against the default.
    """

    before = signal.getsignal(signal.SIGINT)
    task = asyncio.create_task(run_site(config_path))

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while loop.time() < deadline:
        if signal.getsignal(signal.SIGINT) is not before:
            return task
        await asyncio.sleep(0.01)

    task.cancel()
    raise AssertionError("run_site never installed its signal handlers")


# ------------------------------------------------------------
# The signals a simulator actually receives
# ------------------------------------------------------------

@pytest.mark.parametrize("signame", ["SIGINT", "SIGTERM"])
@pytest.mark.asyncio
async def test_a_shutdown_signal_flushes_soc(signame, tmp_path):
    """
    Ctrl-C (SIGINT) and `docker stop` (SIGTERM) must both persist SOC.

    SIGTERM is the one that matters in production: the container's entrypoint
    execs the simulator, so it runs as PID 1 and SIGTERM is what it gets.
    """

    task = await start_site(write_config(tmp_path))
    os.kill(os.getpid(), getattr(signal, signame))

    await asyncio.wait_for(task, timeout=10.0)

    saved = state_file(tmp_path)
    assert saved.exists(), f"{signame} did not flush SOC"

    state = json.loads(saved.read_text())
    assert state["schema"] == 1
    assert state["asset_id"] == "shutdown-bess"
    assert state["soc_percent"] == pytest.approx(INITIAL_SOC, abs=1.0)
    assert state["saved_utc"]


@pytest.mark.asyncio
async def test_the_process_exits_rather_than_hanging_on_a_signal(tmp_path):
    """A shutdown signal ends run_site; it does not leave it awaiting."""

    task = await start_site(write_config(tmp_path))
    os.kill(os.getpid(), signal.SIGINT)

    await asyncio.wait_for(task, timeout=10.0)
    assert task.done() and not task.cancelled()
    assert task.exception() is None


@pytest.mark.asyncio
async def test_a_second_signal_is_survivable(tmp_path):
    """
    Two signals in quick succession must not raise out of the handler.

    The second hands the signals back to the interpreter so an impatient user
    can still kill a shutdown that will not finish.
    """

    task = await start_site(write_config(tmp_path))
    os.kill(os.getpid(), signal.SIGINT)
    os.kill(os.getpid(), signal.SIGINT)

    await asyncio.wait_for(task, timeout=10.0)
    assert state_file(tmp_path).exists()


@pytest.mark.asyncio
async def test_cancelling_the_run_task_also_flushes(tmp_path):
    """
    The other way a host stops the simulator: cancelling the task.

    This is the shape the original bug took — the cancellation was absorbed
    and stop() never ran.
    """

    task = await start_site(write_config(tmp_path))
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert state_file(tmp_path).exists(), "cancellation did not flush SOC"


@pytest.mark.asyncio
async def test_soc_survives_a_restart_through_run_site(tmp_path):
    """End to end: stop the site, start it again, and SOC carries over."""

    config_path = write_config(tmp_path, initial_soc=77.0)

    task = await start_site(config_path)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, timeout=10.0)

    first = json.loads(state_file(tmp_path).read_text())["soc_percent"]

    # Second boot: the config still says 77, but the saved value is what the
    # battery must come up with.
    config = json.loads(config_path.read_text())
    config["assets"][0]["initial_soc"] = 5.0
    config_path.write_text(json.dumps(config))

    task = await start_site(config_path)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, timeout=10.0)

    second = json.loads(state_file(tmp_path).read_text())["soc_percent"]
    assert second == pytest.approx(first, abs=1.0)
    assert second > 50.0, "second boot fell back to the config default"


# ------------------------------------------------------------
# Why it was unreachable
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_propagates_cancellation():
    """
    `start()` must not absorb CancelledError.

    Absorbing it made the task look like a clean return, so every caller's
    shutdown path was skipped. This is the unit-level guard for that.
    """

    site = SiteController({
        "site_name": "cancel-propagation",
        "step": 0.01,
        "real_time": True,
        "assets": [{"type": "bess", "asset_id": "b", "capacity_kwh": 10.0}],
    })
    site.build()

    task = asyncio.create_task(site.start())
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    await site.stop()


@pytest.mark.asyncio
async def test_stop_is_idempotent():
    """run_site calls stop() on every path, so a second call must be a no-op."""

    site = SiteController({
        "site_name": "double-stop",
        "step": 0.01,
        "real_time": False,
        "assets": [{"type": "bess", "asset_id": "b", "capacity_kwh": 10.0}],
    })
    site.build()
    await site.start()

    await site.stop()
    await site.stop()
