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
        # The setpoint is left exactly as the EMS wrote it. Clamping happens
        # downstream, so dispatch resumes by itself once the pack has headroom.

        # Inverter applies ramp limits toward the setpoint
        ramped_power = self.inverter.step(dt)

        # The battery is the single clamp authority: rating, temperature
        # capability and the hard SOC window all land here.
        actual_power = self.battery.step(ramped_power, dt)

        # Inverter state follows reality, so reactive/apparent power and the
        # next ramp step start from what the pack actually did.
        self.inverter.sync_actual(actual_power)

        # Capability telemetry, evaluated after the step
        min_kw, max_kw = self.battery.get_power_limits()
        available_charge = max(0.0, min(self.inverter.max_charge_kw, -min_kw))
        available_discharge = max(0.0, min(self.inverter.max_discharge_kw, max_kw))

        return BESSTelemetry(
            service_voltage=self.battery.open_circuit_voltage(),
            service_current=self.service_current(actual_power),
            system_soc=self.battery.soc,
            battery_temperature=self.battery.temperature_c,
            hvac_power_kw=self.battery.hvac_power_kw,
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

    def service_current(self, power_kw: float) -> float:
        """DC-side current in amps — the register is amps, not kW."""
        voc = self.battery.open_circuit_voltage()
        if voc == 0:
            return 0.0
        return power_kw * 1000.0 / voc
