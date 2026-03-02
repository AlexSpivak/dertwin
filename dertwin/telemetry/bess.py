from dataclasses import dataclass

from dertwin.telemetry.base import TelemetryBase


@dataclass(slots=True)
class BESSTelemetry(TelemetryBase):
    """
    Battery Energy Storage System telemetry snapshot.

    Units follow IEC / SunSpec conventions.
    """

    # Electrical
    service_voltage: float          # V
    service_current: float          # A
    active_power: float             # kW
    reactive_power: float           # kVAR
    apparent_power: float           # kVA

    # Battery state
    system_soc: float               # %
    system_soh: float               # %
    battery_temperature: float     # °C

    # Capability
    available_charging_power: float     # kW
    available_discharging_power: float  # kW
    max_charge_power: float            # kW
    max_discharge_power: float         # kW

    # Energy counters
    total_charge_energy: float         # kWh
    total_discharge_energy: float      # kWh
    charge_and_discharge_cycles: float

    # Grid
    grid_frequency: float              # Hz
    grid_voltage_ab: float             # V
    grid_voltage_bc: float             # V
    grid_voltage_ca: float             # V

    # Controller
    working_status: int = 0
    fault_code: int = 0
    local_remote_mode: int = 0
    power_control_mode: int = 0

    @classmethod
    def zero(cls) -> "BESSTelemetry":
        """
        Returns a safe, fully-initialized zero state.
        Used to initialize _last_telemetry before first simulation step.
        """
        return cls(
            # Electrical
            service_voltage=0.0,
            service_current=0.0,
            active_power=0.0,
            reactive_power=0.0,
            apparent_power=0.0,

            # Battery state
            system_soc=0.0,
            system_soh=100.0,  # healthy by default
            battery_temperature=0.0,

            # Capability
            available_charging_power=0.0,
            available_discharging_power=0.0,
            max_charge_power=0.0,
            max_discharge_power=0.0,

            # Energy counters
            total_charge_energy=0.0,
            total_discharge_energy=0.0,
            charge_and_discharge_cycles=0.0,

            # Grid
            grid_frequency=50.0,  # nominal default
            grid_voltage_ab=0.0,
            grid_voltage_bc=0.0,
            grid_voltage_ca=0.0,

            # Controller
            working_status=0,
            fault_code=0,
            local_remote_mode=0,
            power_control_mode=0,
        )
