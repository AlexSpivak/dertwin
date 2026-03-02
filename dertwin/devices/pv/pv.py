from dertwin.telemetry.pv import PVTelemetry


class PVModel:
    """
    Physical PV plant:
    - Panel DC
    - Inverter AC
    - Energy integration
    """

    def __init__(self, panel, inverter):
        self.panel = panel
        self.inverter = inverter

        self.today_energy_kwh = 0.0
        self.lifetime_energy_kwh = 0.0

    def step(self, dt: float):
        dc_power = self.panel.dc_power_w()
        self.inverter.step(dc_power, dt)

        ac_power = self.inverter.active_power_w

        dt_h = dt / 3600.0
        delta_kwh = (ac_power / 1000.0) * dt_h

        self.today_energy_kwh += delta_kwh
        self.lifetime_energy_kwh += delta_kwh

        return self.get_telemetry()

    def get_telemetry(self):
        return PVTelemetry(
            inverter_status=1 if self.inverter.active_power_w > 10 else 0,
            total_input_power=self.panel.dc_power_w(),
            today_output_energy=self.today_energy_kwh,
            lifetime_output_energy=self.lifetime_energy_kwh,
            total_active_power=self.inverter.active_power_w,
            grid_frequency=self.inverter.grid_frequency,
            phase_neutral_voltage_1=self.inverter.grid_voltage,
            phase_neutral_voltage_2=self.inverter.grid_voltage,
            phase_neutral_voltage_3=self.inverter.grid_voltage,
            temp_inverter=self.inverter.temperature_c,
            power_factor=self.inverter.power_factor_setpoint,
            fault_code=self.inverter.fault_code,
        )