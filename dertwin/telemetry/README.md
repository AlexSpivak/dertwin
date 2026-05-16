# Telemetry Classes Documentation

## Overview

The `dertwin.telemetry` module provides structured telemetry snapshots for simulated power devices. These classes represent the internal state of devices such as PV inverters, battery energy storage systems (BESS), energy meters, and combined heat and power (CHP) units at a given simulation step. All telemetry values follow standard units and conventions (IEC / SunSpec) wherever applicable.

Each class includes a `zero()` method to generate a fully-initialized default state, ensuring safe initialization before the first simulation step.

---

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

- `zero()` → `BESSTelemetry`. Returns a fully-initialized default snapshot suitable for `_last_telemetry`.

---

## 2. EnergyMeterTelemetry

Represents a three-phase energy meter snapshot.

### Attributes

| Attribute                | Type  | Units | Description                                            |
| ------------------------ | ----- | ----- | ------------------------------------------------------ |
| `total_active_power`     | float | kW    | Total active power measured.                           |
| `total_reactive_power`   | float | kVAR  | Total reactive power measured.                         |
| `total_power_factor`     | float | –     | Overall power factor (PF).                             |
| `grid_frequency`         | float | Hz    | Grid frequency.                                        |
| `phase_voltage_a`        | float | V     | Voltage at phase A.                                    |
| `phase_voltage_b`        | float | V     | Voltage at phase B.                                    |
| `phase_voltage_c`        | float | V     | Voltage at phase C.                                    |
| `phase_active_power_a`   | float | kW    | Active power on phase A.                               |
| `phase_active_power_b`   | float | kW    | Active power on phase B.                               |
| `phase_active_power_c`   | float | kW    | Active power on phase C.                               |
| `total_import_energy`    | float | kWh   | Cumulative imported energy.                            |
| `total_export_energy`    | float | kWh   | Cumulative exported energy.                            |
| `phase_import_energy_a`  | float | kWh   | Cumulative imported energy on phase A.                 |
| `phase_import_energy_b`  | float | kWh   | Cumulative imported energy on phase B.                 |
| `phase_import_energy_c`  | float | kWh   | Cumulative imported energy on phase C.                 |
| `phase_export_energy_a`  | float | kWh   | Cumulative exported energy on phase A.                 |
| `phase_export_energy_b`  | float | kWh   | Cumulative exported energy on phase B.                 |
| `phase_export_energy_c`  | float | kWh   | Cumulative exported energy on phase C.                 |
| `current_demand_kw`      | float | kW    | Sliding window average demand (default 15-min window). |
| `max_demand_kw`          | float | kW    | Peak demand observed since last reset.                 |

### Methods

- `zero()` → `EnergyMeterTelemetry`. Returns a default snapshot with all powers and energies zeroed and PF set to unity.

---

## 3. PVTelemetry

Represents a photovoltaic (PV) inverter snapshot.

### Attributes

| Attribute                  | Type  | Units | Description                                      |
| -------------------------- | ----- | ----- | ------------------------------------------------ |
| `inverter_status`          | int   | –     | Status code (0 = idle, 1 = producing).           |
| `total_active_power`       | float | kW    | Total AC active power output.                    |
| `total_input_power`        | float | kW    | DC input power from the PV array.                |
| `today_output_energy`      | float | kWh   | Energy produced today.                           |
| `lifetime_output_energy`   | float | kWh   | Cumulative lifetime energy produced.             |
| `grid_frequency`           | float | Hz    | Grid frequency.                                  |
| `phase_neutral_voltage_1`  | float | V     | Phase 1 to neutral voltage.                      |
| `phase_neutral_voltage_2`  | float | V     | Phase 2 to neutral voltage.                      |
| `phase_neutral_voltage_3`  | float | V     | Phase 3 to neutral voltage.                      |
| `temp_inverter`            | float | °C    | Inverter case temperature.                       |
| `power_factor`             | float | –     | Active power factor setpoint.                    |
| `fault_code`               | int   | –     | Active fault code. 0 = no fault.                 |

### Methods

- `zero()` → `PVTelemetry`. Returns a fully-zeroed snapshot suitable for `_last_telemetry`.

---

## 4. CHPTelemetry

Represents a Combined Heat and Power (CHP) unit telemetry snapshot.

### Attributes

#### State

| Attribute    | Type | Units | Description                                                                 |
| ------------ | ---- | ----- | --------------------------------------------------------------------------- |
| `unit_state` | int  | –     | State machine value (0–19). Maps to MWM TEM Evolution register `30279`.    |

#### Power

| Attribute                   | Type  | Units | Description                                            |
| --------------------------- | ----- | ----- | ------------------------------------------------------ |
| `actual_power_percent`      | float | %     | Current electrical output as % of rated.               |
| `actual_power_kw`           | float | kW    | Current electrical output in kW.                       |
| `permitted_power_percent`   | float | %     | Maximum allowed output (after derating).               |
| `heat_power_kw`             | float | kW    | Current thermal output in kW.                          |

#### Engine

| Attribute            | Type  | Units | Description                       |
| -------------------- | ----- | ----- | --------------------------------- |
| `engine_speed_rpm`   | float | rpm   | Engine rotational speed.          |
| `throttle_position`  | float | %     | Throttle valve position (G197).   |

#### Temperatures

| Attribute                     | Type  | Units | Description                                  |
| ----------------------------- | ----- | ----- | -------------------------------------------- |
| `coolant_outlet_temp`         | float | °C    | Coolant water outlet from engine (T206).     |
| `coolant_inlet_temp`          | float | °C    | Coolant water inlet to engine (T207).        |
| `exhaust_temp_after_catalyst` | float | °C    | Exhaust gas temperature after catalyst.      |
| `oil_temperature`             | float | °C    | Engine oil temperature.                      |
| `intake_air_temp`             | float | °C    | Intake air temperature.                      |

#### Pressures

| Attribute          | Type  | Units | Description                       |
| ------------------ | ----- | ----- | --------------------------------- |
| `oil_pressure`     | float | bar   | Oil pressure before filter.       |
| `charge_pressure`  | float | bar   | Turbocharger charge pressure.     |

#### Operating Statistics

CHP operating counters follow the MWM convention of splitting large counters across two registers (`value % 10000` + `value // 10000`). EMSs reconstruct the total as `high * 10000 + low`.

| Attribute               | Type | Units | Description                              |
| ----------------------- | ---- | ----- | ---------------------------------------- |
| `operating_hours`       | int  | h     | Operating hours modulo 10000.            |
| `operating_hours_10000` | int  | h     | Operating hours divided by 10000.        |
| `start_counter`         | int  | –     | Engine starts modulo 10000.              |
| `start_counter_10000`   | int  | –     | Engine starts divided by 10000.          |

#### Discrete Flags

These flags are exposed over Modbus `FC02` (read discrete inputs). They serialize as single bits to addresses in the `1xxxx` range.

| Attribute                | Type | Description                                  |
| ------------------------ | ---- | -------------------------------------------- |
| `engine_running`         | bool | True when engine is in `RUNNING` state.      |
| `circuit_breaker_closed` | bool | True when grid breaker is closed.            |
| `collective_fault`       | bool | True when any fault is active.               |
| `collective_warning`     | bool | True when any warning is active.             |
| `auto_mode`              | bool | True when in auto operating mode.            |
| `e_stop_request`         | bool | True when emergency start request is active. |
| `power_supply_failure`   | bool | True when main power supply is lost.         |
| `ignition_on`            | bool | True when ignition system is energized.      |
| `starter_on`             | bool | True when starter motor is engaged.          |
| `prelube_pump_on`        | bool | True when pre-lubrication pump is running.   |
| `preheat_on`             | bool | True when engine preheat is active.          |

### Methods

- `zero()` → `CHPTelemetry`. Returns a default snapshot in the `READY` state with all powers zeroed and temperatures at ambient (20 °C).

---

## Notes

- All classes use `slots=True` to reduce memory footprint for high-frequency telemetry updates.
- Units and conventions match IEC / SunSpec standards where possible, ensuring compatibility with industry tools and Modbus mappings.
- `CHPTelemetry` follows the MWM TEM Evolution register layout for compatibility with that family of gas-engine controllers, but the field semantics generalise to most CHP units.
- `zero()` should always be used to initialize telemetry before the first simulation step to avoid `AttributeError`.
- Boolean flags in `CHPTelemetry` are serialized to single bits by `write_telemetry_registers` when the register's function code is `FC02`. See `protocol/README.md` for details.