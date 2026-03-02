from dataclasses import dataclass

from dertwin.telemetry.base import TelemetryBase


@dataclass(slots=True)
class EnergyMeterTelemetry(TelemetryBase):
    total_active_power: float
    total_reactive_power: float
    total_power_factor: float
    grid_frequency: float

    phase_voltage_a: float
    phase_voltage_b: float
    phase_voltage_c: float

    phase_active_power_a: float
    phase_active_power_b: float
    phase_active_power_c: float

    total_import_energy: float
    total_export_energy: float

    @classmethod
    def zero(cls) -> "EnergyMeterTelemetry":
        """
        Returns a safe, fully-initialized zero state.
        Used to initialize _last_telemetry before first simulation step.
        """
        return cls(
            total_active_power=0.0,
            total_reactive_power=0.0,
            total_power_factor=1.0,  # unity PF default
            grid_frequency=50.0,  # nominal IEC default

            phase_voltage_a=0.0,
            phase_voltage_b=0.0,
            phase_voltage_c=0.0,

            phase_active_power_a=0.0,
            phase_active_power_b=0.0,
            phase_active_power_c=0.0,

            total_import_energy=0.0,
            total_export_energy=0.0,
        )