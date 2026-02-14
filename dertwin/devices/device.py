from datetime import datetime
from typing import Dict, Optional


class DeviceSimulator:
    def __init__(self):
        self.cumulative_energy = 0.0
        self.today_energy = 0.0
        self.last_day = datetime.now().day

    def reset_daily_counters(self):
        current_day = datetime.now().day
        if current_day != self.last_day:
            self.today_energy = 0.0
            self.last_day = current_day

    def execute_write_instructions(self, write_instructions: Dict[str, float]) -> Dict[str, float]:
        """
        Execute a batch of write instructions.
        Input: { register_name: value }
        Output: { register_name: applied_value }
        """
        raise NotImplementedError

    def simulate_values(self, dt: Optional[float]) -> Dict[str, float]:
        raise NotImplementedError

    def init_applied_commands(self, commands: Dict[str, float]):
        raise NotImplementedError