# Telemetry Classes Documentation
## Overview

The dertwin.telemetry module provides structured telemetry snapshots for simulated power devices. These classes represent the internal state of devices such as PV inverters, battery energy storage systems (BESS), and energy meters at a given simulation step. All telemetry values follow standard units and conventions (IEC / SunSpec) wherever applicable.

Each class includes a zero() method to generate a fully-initialized default state, ensuring safe initialization before the first simulation step.

## 1. BESSTelemetry

Represents a Battery Energy Storage System (BESS) telemetry snapshot.

### Attributes
| Attribute                     | Type  | Units  | Description                                       |
| ----------------------------- | ----- | ------ | ------------------------------------------------- |
| `service_voltage`             | float | V      | Voltage at the BESS output terminals.             |
| `service_current`             | float | A      | Current at the BESS output terminals.             |
| `active_power`                | float | kW     | Active power delivered or absorbed.               |
| `reactive_power`              | float | kVAR   | Reactive power delivered or absorbed.             |
| `apparent_power`              | float | kVA    | Apparent power.                                   |
| `system_soc`                  | float | %      | State of Charge of the battery.                   |
| `system_soh`                  | float | %      | State of Health of the battery.                   |
| `battery_temperature`         | float | °C     | Battery temperature.                              |
| `available_charging_power`    | float | kW     | Maximum available charging power at this step.    |
| `available_discharging_power` | float | kW     | Maximum available discharging power at this step. |
| `max_charge_power`            | float | kW     | Maximum allowed charging power.                   |
| `max_discharge_power`         | float | kW     | Maximum allowed discharging power.                |
| `total_charge_energy`         | float | kWh    | Cumulative energy charged.                        |
| `total_discharge_energy`      | float | kWh    | Cumulative energy discharged.                     |
| `charge_and_discharge_cycles` | float | cycles | Total number of charge/discharge cycles.          |
| `grid_frequency`              | float | Hz     | Frequency of the connected grid.                  |
| `grid_voltage_ab`             | float | V      | Line-to-line voltage AB.                          |
| `grid_voltage_bc`             | float | V      | Line-to-line voltage BC.                          |
| `grid_voltage_ca`             | float | V      | Line-to-line voltage CA.                          |
| `working_status`              | int   | –      | Device working status code. Default: 0.           |
| `fault_code`                  | int   | –      | Active fault code. Default: 0.                    |
| `local_remote_mode`           | int   | –      | Control mode: local or remote. Default: 0.        |
| `power_control_mode`          | int   | –      | Power control mode. Default: 0.                   |

### Methods

- zero() → BESSTelemetry. Returns a fully-initialized default snapshot suitable for _last_telemetry.

## 2. EnergyMeterTelemetry

Represents a three-phase energy meter snapshot.

### Attributes
| Attribute              | Type  | Units | Description                    |
| ---------------------- | ----- | ----- | ------------------------------ |
| `total_active_power`   | float | kW    | Total active power measured.   |
| `total_reactive_power` | float | kVAR  | Total reactive power measured. |
| `total_power_factor`   | float | –     | Overall power factor (PF).     |
| `grid_frequency`       | float | Hz    | Grid frequency.                |
| `phase_voltage_a`      | float | V     | Voltage at phase A.            |
| `phase_voltage_b`      | float | V     | Voltage at phase B.            |
| `phase_voltage_c`      | float | V     | Voltage at phase C.            |
| `phase_active_power_a` | float | kW    | Active power on phase A.       |
| `phase_active_power_b` | float | kW    | Active power on phase B.       |
| `phase_active_power_c` | float | kW    | Active power on phase C.       |
| `total_import_energy`  | float | kWh   | Cumulative imported energy.    |
| `total_export_energy`  | float | kWh   | Cumulative exported energy.    |

### Methods

- zero() → EnergyMeterTelemetry. Returns a default snapshot with all powers and energies zeroed and PF set to unity.

## 3. PVTelemetry

Represents a photovoltaic (PV) inverter snapshot.

### Attributes
| Attribute              | Type  | Units | Description                    |
| ---------------------- | ----- | ----- | ------------------------------ |
| `total_active_power`   | float | kW    | Total active power measured.   |
| `total_reactive_power` | float | kVAR  | Total reactive power measured. |
| `total_power_factor`   | float | –     | Overall power factor (PF).     |
| `grid_frequency`       | float | Hz    | Grid frequency.                |
| `phase_voltage_a`      | float | V     | Voltage at phase A.            |
| `phase_voltage_b`      | float | V     | Voltage at phase B.            |
| `phase_voltage_c`      | float | V     | Voltage at phase C.            |
| `phase_active_power_a` | float | kW    | Active power on phase A.       |
| `phase_active_power_b` | float | kW    | Active power on phase B.       |
| `phase_active_power_c` | float | kW    | Active power on phase C.       |
| `total_import_energy`  | float | kWh   | Cumulative imported energy.    |
| `total_export_energy`  | float | kWh   | Cumulative exported energy.    |

### Methods

- zero() → PVTelemetry. Returns a fully-zeroed snapshot suitable for _last_telemetry.

## Notes

All classes use slots to reduce memory footprint for high-frequency telemetry updates.

Units and conventions are chosen to match IEC / SunSpec standards, ensuring compatibility with industry tools and Modbus mappings.

zero() should always be used to initialize telemetry before the first simulation step to avoid AttributeErrors.