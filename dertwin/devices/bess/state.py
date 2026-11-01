"""
Optional SOC persistence for BESS assets.

A state file lets a BESS resume where it left off instead of cold-starting at
the configured initial_soc on every process start. Persistence is opt-in: with
no state_file in the config nothing is read or written.

The file is small, human-readable JSON and is written atomically (temp file +
os.replace) so a crash mid-write cannot leave a truncated state behind.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


SCHEMA_VERSION = 1

# Fields a snapshot must carry to be usable at all.
_REQUIRED_FIELDS = ("energy_kwh",)


def load_state(path: Path, asset_id: str = "bess") -> Optional[dict]:
    """
    Read a saved snapshot.

    Returns None — and logs why — whenever the caller should fall back to the
    configured initial_soc: no file yet, unreadable file, bad JSON, unknown
    schema, or missing fields.
    """

    path = Path(path)

    if not path.exists():
        logger.info(
            "BESS %s starting from configured initial_soc (no state file at %s)",
            asset_id, path,
        )
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(
            "BESS %s state file %s is unreadable (%s); "
            "starting from configured initial_soc",
            asset_id, path, e,
        )
        return None

    if not isinstance(data, dict):
        logger.warning(
            "BESS %s state file %s is not a JSON object; "
            "starting from configured initial_soc",
            asset_id, path,
        )
        return None

    schema = data.get("schema")
    if schema != SCHEMA_VERSION:
        logger.warning(
            "BESS %s state file %s has schema %r, expected %d; "
            "starting from configured initial_soc",
            asset_id, path, schema, SCHEMA_VERSION,
        )
        return None

    missing = [key for key in _REQUIRED_FIELDS if key not in data]
    if missing:
        logger.warning(
            "BESS %s state file %s is missing %s; "
            "starting from configured initial_soc",
            asset_id, path, ", ".join(missing),
        )
        return None

    logger.info(
        "BESS %s resuming from %s (SOC %.2f%%, saved %s)",
        asset_id, path, data.get("soc_percent", float("nan")),
        data.get("saved_utc", "unknown"),
    )
    return data


def save_state(path: Path, snapshot: dict, asset_id: str = "bess") -> bool:
    """
    Write a snapshot atomically. Returns True on success.

    A failed save is logged and swallowed — losing persistence must never take
    the simulation down.
    """

    path = Path(path)
    payload = {
        "schema": SCHEMA_VERSION,
        "asset_id": asset_id,
        "saved_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **snapshot,
    }

    tmp_path = path.with_name(path.name + ".tmp")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except OSError as e:
        logger.warning("BESS %s could not save state to %s: %s", asset_id, path, e)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False

    logger.debug("BESS %s state saved to %s", asset_id, path)
    return True
