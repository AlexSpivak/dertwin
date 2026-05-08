from typing import Dict, Any, Callable


class PVController:
    """
    Device-level controller for PV.
    Applies commands with per-command change detection to avoid
    one command inadvertently resetting another.
    """

    def __init__(self, model):
        self.model = model
        self._last_applied_commands: Dict[str, Any] = {}

    def init_applied_commands(self, commands: Dict):
        self._last_applied_commands = dict(commands or {})
        return commands

    def apply_command(
        self,
        key: str,
        value: Any,
        apply_fn: Callable[[Any], None],
        applied: dict,
    ):
        """Apply value only if it differs from the last applied value."""
        if self._last_applied_commands.get(key) != value:
            apply_fn(value)
            self._last_applied_commands[key] = value
            applied[key] = value

    def apply_commands(self, commands: dict) -> dict:
        applied = {}

        if "active_power_rate" in commands:
            raw = int(commands["active_power_rate"])
            rate = 100.0 if raw == 255 else float(raw)
            rate = max(0.0, min(100.0, rate))
            self.apply_command(
                "active_power_rate", rate,
                lambda v: setattr(self.model.inverter, "active_power_rate", v),
                applied,
            )

        if "power_factor_setpoint" in commands:
            pf = max(-1.0, min(1.0, float(commands["power_factor_setpoint"])))
            self.apply_command(
                "power_factor_setpoint", pf,
                lambda v: setattr(self.model.inverter, "power_factor_setpoint", v),
                applied,
            )

        if "remote_on_off" in commands:
            self.apply_command(
                "remote_on_off", commands["remote_on_off"],
                lambda v: setattr(self.model.inverter, "enabled", int(v) == 1),
                applied,
            )

        return applied

    def step(self, dt: float):
        return self.model.step(dt)

    def get_telemetry(self):
        return self.model.get_telemetry()