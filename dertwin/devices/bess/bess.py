from dertwin.devices.bess.battery import BatteryModel
from dertwin.devices.bess.inverter import InverterModel


class BESSModel:

    def __init__(self, battery: BatteryModel, inverter: InverterModel):
        self.battery = battery
        self.inverter = inverter

    def set_power_command(self, kw: float):
        self.inverter.set_target_power(kw)

    def step(self, dt: float):

        raw_target = self.inverter.target_power
        allowed_target = self.battery.limit_power(raw_target)
        self.inverter._target_power = allowed_target
        actual_power = self.inverter.step(dt)
        self.battery.step(actual_power, dt)

        telemetry = {
            "service_voltage": self.battery.open_circuit_voltage(),
            "service_current": actual_power,
            "system_soc": self.battery.soc,
            "battery_temperature": self.battery.temperature_c,
            "total_charge_energy": self.battery.charge_energy_total_kwh,
            "total_discharge_energy": self.battery.discharge_energy_total_kwh,
            "active_power": actual_power,
            "reactive_power": self.inverter.reactive_power(),
            "apparent_power": self.inverter.apparent_power(),
            "max_charge_power": self.inverter.max_charge_kw,
            "max_discharge_power": self.inverter.max_discharge_kw,
            "charge_and_discharge_cycles": self.battery.cycles,
            "system_soh": self.battery.soh,
            "available_charging_power": self.inverter.max_charge_kw,
            "available_discharging_power": self.inverter.max_discharge_kw,
            "on_grid_power": actual_power,
            "grid_frequency": self.inverter.grid_frequency_hz,
        }

        return telemetry