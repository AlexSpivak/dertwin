from typing import Dict, Any, Callable

from dertwin.devices.chp.chp import CHPModel
from dertwin.telemetry.chp import CHPTelemetry


# Magic value from MWM TEM Evolution spec for remote acknowledgment
ACKNOWLEDGMENT_MAGIC = 0x10E1  # 4321 in decimal


class CHPController:
    """
    Vendor-independent CHP control layer.

    Responsibilities:
    - Translate Modbus command values into engine actions
    - Per-command change detection (don't re-trigger unchanged commands)
    - Re-dispatch power setpoint when conditions change

    Command map:
      start_stop:                   0 → request_stop, 1 → request_start
      power_setpoint_percent:       0–1100 raw (0–110.0%)
      remote_acknowledgment:        write 4321 to acknowledge faults
    """

    # Commands that always execute regardless of memory (momentary triggers)
    _STATELESS_COMMANDS = {"remote_acknowledgment"}

    def __init__(self, chp: CHPModel):
        self.chp = chp
        self._last_applied_commands: Dict[str, Any] = {}

    def init_applied_commands(self, commands: Dict):
        self._last_applied_commands = dict(commands or {})
        return commands

    def _apply_if_changed(
        self,
        key: str,
        value: Any,
        apply_fn: Callable[[Any], None],
        applied: dict,
    ):
        if self._last_applied_commands.get(key) != value:
            apply_fn(value)
            self._last_applied_commands[key] = value
            applied[key] = value

    def apply_commands(self, commands: dict) -> dict:
        applied = {}

        # ----------------------------------------
        # Start / Stop
        # ----------------------------------------
        if "start_stop" in commands:
            cmd = int(commands["start_stop"])
            self._apply_if_changed(
                "start_stop", cmd,
                lambda v: self._handle_start_stop(int(v)),
                applied,
            )

        # ----------------------------------------
        # Power Setpoint (percent)
        # ----------------------------------------
        if "power_setpoint_percent" in commands:
            pct = float(commands["power_setpoint_percent"])
            pct = max(0.0, min(110.0, pct))
            self._apply_if_changed(
                "power_setpoint_percent", pct,
                lambda v: self.chp.set_power_setpoint_percent(v),
                applied,
            )

        # ----------------------------------------
        # Remote Acknowledgment (stateless)
        # ----------------------------------------
        if "remote_acknowledgment" in commands:
            value = int(commands["remote_acknowledgment"])
            if value == ACKNOWLEDGMENT_MAGIC:
                self.chp.engine.acknowledge_fault()
                applied["remote_acknowledgment"] = value

        return applied

    def _handle_start_stop(self, cmd: int):
        if cmd == 1:
            self.chp.engine.request_start()
        elif cmd == 0:
            self.chp.engine.request_stop()

    # =========================================================
    # Step (called by simulator wrapper)
    # =========================================================

    def step(self, dt: float) -> CHPTelemetry:
        return self.chp.step(dt)