from .battery import BatteryModel
from .inverter import InverterModel


class BESSModel:
    """
    Composition of inverter + battery.
    """

    def __init__(self, battery: BatteryModel, inverter: InverterModel):
        self.battery = battery
        self.inverter = inverter

    # ---------------------------------------------------------

    def set_power_command(self, power_kw: float):
        self.inverter.set_target_power(power_kw)

    # ---------------------------------------------------------

    def step(self, dt_seconds: float):

        inverter_power = self.inverter.step(dt_seconds)

        actual_power = self.battery.step(
            inverter_power,
            dt_seconds,
        )

        return {
            "soc": self.battery.soc,
            "battery_power_kw": actual_power,
            "inverter_power_kw": inverter_power,
        }