from dataclasses import dataclass
from typing import Dict, Any
from dertwin.devices.bess.bess import BESSModel


@dataclass
class ControllerState:
    run_mode: int = 0  # 0=idle, 1=run, 2=standby
    local_remote_settings: int = 0
    power_control_mode: int = 0
    fault_code: int = 0


class BESSController:
    """
    Vendor-independent device control layer.

    Responsibilities:
    - Command handling
    - Command memory
    - Fault detection
    - Working status evaluation
    - Power dispatch logic
    """

    def __init__(self, bess: BESSModel):
        self.bess = bess
        self.state = ControllerState()

        # Prevent re-applying identical commands
        self._last_applied_commands: Dict[str, Any] = {}

    # -------------------------------------------------
    # Initialize Command Memory
    # -------------------------------------------------

    def init_applied_commands(self, commands: Dict[str, Any]) -> Dict[str, Any]:
        """
        Initialize internal command memory at startup.

        This prevents immediate re-triggering of identical
        write operations when Modbus connects.
        """

        if not commands:
            return {}

        # Store memory
        self._last_applied_commands = dict(commands)

        return commands

    # -------------------------------------------------
    # Apply Command (External Entry)
    # -------------------------------------------------

    def apply_command(self, name: str, value: Any):
        """
        Apply command with memory protection.
        """

        # Skip if identical to last applied
        if self._last_applied_commands.get(name) == value:
            return

        self._last_applied_commands[name] = value

        self._apply_without_memory_update(name, value)

    # -------------------------------------------------
    # Internal Application (No Memory Check)
    # -------------------------------------------------

    def _apply_without_memory_update(self, name: str, value: Any):

        if name == "start_stop_standby":
            # 1 = run
            # 2 = idle
            # 3 = standby
            self.state.run_mode = int(value)

        elif name == "local_remote_settings":
            self.state.local_remote_settings = int(value)

        elif name == "power_control_mode":
            self.state.power_control_mode = int(value)

        elif name == "active_power_setpoint":
            if self.state.run_mode == 1 and self.state.fault_code == 0:
                self.bess.set_power_command(float(value))

        elif name == "soc_lower_limit_1":
            self.bess.battery.limits.soc_lower_limit_1 = float(value)

        elif name == "soc_lower_limit_2":
            self.bess.battery.limits.soc_lower_limit_2 = float(value)

        elif name == "soc_upper_limit_1":
            self.bess.battery.limits.soc_upper_limit_1 = float(value)

        elif name == "soc_upper_limit_2":
            self.bess.battery.limits.soc_upper_limit_2 = float(value)

        elif name == "fault_reset":
            if int(value) == 1:
                self.state.fault_code = 0

    # -------------------------------------------------
    # Fault Logic
    # -------------------------------------------------

    def evaluate_faults(self):
        if self.bess.battery.temperature_c > 75.0:
            self.state.fault_code = 1001

        if self.bess.battery.soc <= 0.0:
            self.state.fault_code = 2001

    # -------------------------------------------------
    # Working Status
    # -------------------------------------------------

    def working_status(self) -> int:
        if self.state.run_mode == 2:
            return 0  # standby treated as not running

        if self.state.run_mode == 0:
            return 0  # idle

        if self.state.fault_code != 0:
            return 2  # fault

        return 1  # running

    # -------------------------------------------------
    # Simulation Step
    # -------------------------------------------------

    def step(self, dt: float) -> Dict[str, float]:

        # Enforce stop or fault
        if self.state.run_mode != 1 or self.state.fault_code != 0:
            self.bess.set_power_command(0.0)

        telemetry = self.bess.step(dt)

        self.evaluate_faults()

        telemetry.update({
            "working_status": self.working_status(),
            "fault_code": self.state.fault_code,
            "local_remote_mode": self.state.local_remote_settings,
            "power_control_mode": self.state.power_control_mode,
        })

        return telemetry