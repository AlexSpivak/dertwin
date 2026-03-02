from dataclasses import dataclass

from dertwin.telemetry.base import TelemetryBase


@dataclass(slots=True)
class PVTelemetry(TelemetryBase):
    inverter_status: int
    total_input_power: float
    today_output_energy: float
    total_active_power: float
    grid_frequency: float
    phase_neutral_voltage_1: float
    phase_neutral_voltage_2: float
    phase_neutral_voltage_3: float
    lifetime_output_energy: float
    temp_inverter: float
    power_factor: float
    fault_code: int

    @classmethod
    def zero(cls) -> "PVTelemetry":
        return cls(
            inverter_status=0,
            total_input_power=0,
            today_output_energy=0,
            total_active_power=0,
            grid_frequency=0,
            phase_neutral_voltage_1=0,
            phase_neutral_voltage_2=0,
            phase_neutral_voltage_3=0,
            lifetime_output_energy=0,
            temp_inverter=0,
            power_factor=0,
            fault_code=0
        )
