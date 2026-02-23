from typing import Dict


class PVController:
    """
    Device-level controller for PV.
    Applies commands only on change.
    """

    def __init__(self, model):
        self.model = model
        self._last_applied_commands = {}

    def init_applied_commands(self, commands: Dict):
        self._last_applied_commands = dict(commands or {})
        return commands

    def apply_commands(self, commands: Dict):
        applied = {}

        for name, value in commands.items():

            if name == "active_power_rate":
                self.model.inverter.active_power_rate = float(value)
                applied[name] = value

            elif name == "power_factor_setpoint":
                self.model.inverter.power_factor_setpoint = float(value)
                applied[name] = value

            elif name == "remote_on_off":
                # simple enable/disable logic
                if int(value) == 0:
                    self.model.inverter.active_power_rate = 0.0
                applied[name] = value

        self._last_applied_commands.update(applied)
        return applied

    def step(self, dt: float):
        return self.model.step(dt)

    def get_telemetry(self):
        return self.model.get_telemetry()