from dertwin.devices.bess.battery import BatteryModel
from dertwin.devices.bess.inverter import InverterModel
from dertwin.telemetry.bess import BESSTelemetry


class BESSModel:

    def __init__(self, battery: BatteryModel, inverter: InverterModel):
        self.battery = battery
        self.inverter = inverter

    def set_power_command(self, kw: float):
        self.inverter.set_target_power(kw)

    def step(self, dt: float):
        # Requested power from controller
        requested = self.inverter.target_power

        # Battery capability limits
        min_kw, max_kw = self.battery.get_power_limits()

        # Clamp requested to allowed range
        allowed = max(min_kw, min(max_kw, requested))

        # Send allowed request to inverter
        self.inverter.set_target_power(allowed)

        # Inverter applies ramp limits
        actual_power = self.inverter.step(dt)

        # Battery integrates actual power
        actual_power = self.battery.step(actual_power, dt)

        # Capability telemetry
        available_charge = max(0.0, min(self.inverter.max_charge_kw, -min_kw))
        available_discharge = max(0.0, min(self.inverter.max_discharge_kw, max_kw))

        return BESSTelemetry(
            service_voltage=self.battery.open_circuit_voltage(),
            service_current=actual_power,
            system_soc=self.battery.soc,
            battery_temperature=self.battery.temperature_c,
            active_power=actual_power,
            reactive_power=self.inverter.reactive_power(),
            apparent_power=self.inverter.apparent_power(),
            available_charging_power=available_charge,
            available_discharging_power=available_discharge,
            max_charge_power=self.inverter.max_charge_kw,
            max_discharge_power=self.inverter.max_discharge_kw,
            total_charge_energy=self.battery.charge_energy_total_kwh,
            total_discharge_energy=self.battery.discharge_energy_total_kwh,
            charge_and_discharge_cycles=self.battery.cycles,
            system_soh=self.battery.soh,
            grid_frequency=self.inverter.grid_frequency,
            grid_voltage_ab=self.inverter.grid_voltage_ll,
            grid_voltage_bc=self.inverter.grid_voltage_ll,
            grid_voltage_ca=self.inverter.grid_voltage_ll,
        )