from dataclasses import dataclass

from dertwin.telemetry.base import TelemetryBase


@dataclass(slots=True)
class CHPTelemetry(TelemetryBase):
    """
    Telemetry snapshot for a CHP unit.

    Field names match register `name` (not `internal_name`) in chp_modbus.yaml
    so that DeviceController.apply_telemetry can map them directly.
    """

    # State machine
    unit_state: int

    # Power
    actual_power_percent: float
    actual_power_kw: float
    permitted_power_percent: float
    heat_power_kw: float

    # Engine
    engine_speed_rpm: float
    throttle_position: float

    # Temperatures
    coolant_outlet_temp: float
    coolant_inlet_temp: float
    exhaust_temp_after_catalyst: float
    oil_temperature: float
    intake_air_temp: float

    # Pressures
    oil_pressure: float
    charge_pressure: float

    # Operating statistics (split MWM convention)
    operating_hours: int
    operating_hours_10000: int
    start_counter: int
    start_counter_10000: int

    # Discrete flags (booleans serialized as 0/1 by FC02 writer)
    engine_running: bool
    circuit_breaker_closed: bool
    collective_fault: bool
    collective_warning: bool
    auto_mode: bool
    e_stop_request: bool
    power_supply_failure: bool
    ignition_on: bool
    starter_on: bool
    prelube_pump_on: bool
    preheat_on: bool

    @classmethod
    def zero(cls) -> "CHPTelemetry":
        return cls(
            unit_state=1,  # READY
            actual_power_percent=0.0,
            actual_power_kw=0.0,
            permitted_power_percent=100.0,
            heat_power_kw=0.0,
            engine_speed_rpm=0.0,
            throttle_position=0.0,
            coolant_outlet_temp=20.0,
            coolant_inlet_temp=20.0,
            exhaust_temp_after_catalyst=20.0,
            oil_temperature=20.0,
            intake_air_temp=20.0,
            oil_pressure=0.0,
            charge_pressure=0.0,
            operating_hours=0,
            operating_hours_10000=0,
            start_counter=0,
            start_counter_10000=0,
            engine_running=False,
            circuit_breaker_closed=False,
            collective_fault=False,
            collective_warning=False,
            auto_mode=True,
            e_stop_request=False,
            power_supply_failure=False,
            ignition_on=False,
            starter_on=False,
            prelube_pump_on=False,
            preheat_on=False,
        )