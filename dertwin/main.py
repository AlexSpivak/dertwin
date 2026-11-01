import argparse
import asyncio
import json
import logging
import signal
from pathlib import Path

from dertwin.controllers.site_controller import SiteController
from dertwin.logging_config import setup_logging

setup_logging("INFO")

logger = logging.getLogger(__name__)

# Ctrl-C and the signal a container runtime sends on `docker stop`.
SHUTDOWN_SIGNALS = ("SIGINT", "SIGTERM")

def load_config(path: Path) -> dict:
    if not path.is_absolute():
        path = Path.cwd() / path

    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # register_map_root resolves relative to cwd, not config file
    if "register_map_root" in config:
        register_map_root = Path(config["register_map_root"])
        if not register_map_root.is_absolute():
            register_map_root = path.parent / register_map_root
        config["register_map_root"] = str(register_map_root.resolve())

    # state_root follows the same rule, so BESS state files live next to the
    # config that declared them rather than wherever dertwin was launched.
    if "state_root" in config:
        state_root = Path(config["state_root"])
        if not state_root.is_absolute():
            state_root = path.parent / state_root
        config["state_root"] = str(state_root.resolve())

    return config

def _install_shutdown_handlers(loop, callback) -> list:
    """
    Route shutdown signals through the event loop.

    Default SIGINT handling raises KeyboardInterrupt wherever the interpreter
    happens to be and tears the loop down, and SIGTERM is not handled at all
    — so neither reaches stop(), and persistent device state is never
    flushed. SIGTERM matters most: it is what `docker stop` sends, and the
    container runs the simulator as PID 1.

    Returns the signals actually installed, which may be none on platforms
    that do not support them.
    """

    installed = []

    for name in SHUTDOWN_SIGNALS:
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, callback, name)
        except (NotImplementedError, RuntimeError):
            # Windows, or not running on the main thread.
            continue
        installed.append(sig)

    return installed


async def run_site(config_path: Path):
    config = load_config(config_path)

    site = SiteController(config)
    site.build()

    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()
    installed = []

    def request_shutdown(signame: str) -> None:
        if shutdown.is_set():
            # Already stopping. Hand the signals back to the interpreter so a
            # further Ctrl-C can still kill a shutdown that will not finish.
            logger.warning("Received %s again, forcing exit on the next one",
                           signame)
            for sig in installed:
                loop.remove_signal_handler(sig)
            installed.clear()
            return

        logger.info("Received %s, shutting down", signame)
        shutdown.set()

    installed = _install_shutdown_handlers(loop, request_shutdown)

    site_task = asyncio.create_task(site.start())
    signalled = asyncio.create_task(shutdown.wait())

    try:
        await asyncio.wait(
            {site_task, signalled}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)
        signalled.cancel()

        # stop() flushes persistent device state and releases the listening
        # sockets, so it runs on every exit path.
        await site.stop()

        site_task.cancel()
        try:
            # Re-raises a genuine engine failure rather than exiting quietly.
            await site_task
        except asyncio.CancelledError:
            pass

def main():
    parser = argparse.ArgumentParser(description="Run DER Twin Site Simulator")

    parser.add_argument(
        "-c",
        "--config",
        type=str,
        required=True,
        help="Path to site configuration JSON",
    )

    args = parser.parse_args()

    asyncio.run(run_site(Path(args.config)))


if __name__ == "__main__":
    main()
