import math
import random
from typing import Dict, Optional
from .device import DeviceSimulator

# -------------------------
# Deterministic / discrete-time BESS Simulator
# -------------------------
class BESSSimulator(DeviceSimulator):
    def __init__(
        self,
        interval: float = 0.1,
        deterministic: bool = True,
        ramp_rate_kw_per_s: float = 100.0,
        ambient_temp_c: float = 20.0,
    ):
        """
        interval: default internal time_step if caller doesn't provide dt (seconds)
        deterministic: when True disables random jitter and fixes initial seeds
        ramp_rate_kw_per_s: maximum ramp rate in kW per second
        ambient_temp_c: environment temperature used by thermal model
        """
        super().__init__()

        # Config
        self.time_step_sec = interval
        self.deterministic = deterministic
        self.ramp_rate_kw_per_s = float(ramp_rate_kw_per_s)
        self.ambient_temp_c = float(ambient_temp_c)

        # Rated values
        self.rated_capacity_kwh = 100.0

        # Core state
        # deterministic initial SOC when requested, else keep randomized for variety
        self.soc = 50.0 if deterministic else random.uniform(40, 60)
        self.temperature_c = 25.0
        self.internal_resistance = 0.05  # ohmic sag (Ω)

        # Energy counters (kWh)
        self.charge_energy_total_kwh = 0.0
        self.discharge_energy_total_kwh = 0.0

        # Power limits (kW)
        self.max_charge_kw = 20.0
        self.max_discharge_kw = 20.0

        # Dynamic state
        # commanded_power_kw: the power device is currently applying (kW)
        # on_grid_power_kw: the most recent setpoint received (kW)
        self.commanded_power_kw = 0.0
        self.on_grid_power_kw = 0.0

        # Efficiency, timing and health
        self.round_trip_eff = 0.92
        self.fault_code = 0
        self.soh = 100.0
        self.cycles = 0.0

        # Modes and protections
        self.mode = "idle"
        self.local_remote_mode = 0
        self.power_control_mode = 0

        # SOC boundaries
        self.soc_upper_limit_1 = 85.0
        self.soc_upper_limit_2 = 90.0
        self.soc_lower_limit_1 = 25.0
        self.soc_lower_limit_2 = 20.0

        # Thermal model parameters
        self.thermal_capacity_j_per_k = 5000.0
        self.thermal_conductance_w_per_k = 0.5

        # internal command memory (to avoid re-applying identical commands)
        self._last_applied_commands = {}

        # grid frequency state
        self.grid_frequency_hz = 50.0

        # store last dt seen (helpful for debugging)
        self._last_dt = float(self.time_step_sec)

    # ------------------------------------------
    # External setter used by write executor
    # ------------------------------------------
    def set_on_grid_power_kw(self, kw: float):
        # sanitize numeric input
        try:
            kw = float(kw)
        except Exception:
            return
        self.on_grid_power_kw = kw

    # ------------------------------------------
    # Ramp + SOC derating + hard-limits enforcement
    # Discrete-time update that uses dt explicitly
    # ------------------------------------------
    def apply_commanded_power(self, dt: Optional[float] = None):
        """
        Updated SOC-limiter implementation (Option B).
        Behavior:
          1) Ramp toward on_grid_power_kw
          2) Clamp to device limits
          3) Apply SOC soft-derating:
                - discharge derates between limit2..limit1
                - charge derates between upper limit1..upper limit2
          4) Hard cutoffs:
                - discharge disabled below limit2
                - charge disabled above upper limit2
        """

        # ------------ 1) RAMP ------------
        if dt is None:
            dt = self.time_step_sec
        self._last_dt = float(dt)

        raw_target = float(self.on_grid_power_kw)

        ramp_step = self.ramp_rate_kw_per_s * dt
        delta = raw_target - self.commanded_power_kw

        if delta > ramp_step:
            delta = ramp_step
        elif delta < -ramp_step:
            delta = -ramp_step

        proposed_kw = self.commanded_power_kw + delta

        # ------------ 2) DEVICE LIMITS ------------
        if proposed_kw >= 0:
            proposed_kw = min(proposed_kw, self.max_discharge_kw)
        else:
            proposed_kw = max(proposed_kw, -self.max_charge_kw)

        soc = self.soc

        # ------------ 3) SOFT DERATING (Option B) ------------

        # ----- Discharging soft-derate -----
        if proposed_kw > 0:
            if self.soc_lower_limit_2 < soc < self.soc_lower_limit_1:
                # scale from 0 → 1 across the band
                factor = (soc - self.soc_lower_limit_2) / (self.soc_lower_limit_1 - self.soc_lower_limit_2)
                proposed_kw *= max(0.0, min(1.0, factor))

        # ----- Charging soft-derate -----
        if proposed_kw < 0:
            if self.soc_upper_limit_1 < soc < self.soc_upper_limit_2:
                # scale from 0 → 1 across the upper band
                factor = (self.soc_upper_limit_2 - soc) / (self.soc_upper_limit_2 - self.soc_upper_limit_1)
                proposed_kw *= max(0.0, min(1.0, factor))

        # ------------ 4) HARD SOC CUTOFFS ------------

        # hard stop discharge
        if proposed_kw > 0 and soc <= self.soc_lower_limit_2:
            proposed_kw = 0.0

        # hard stop charge
        if proposed_kw < 0 and soc >= self.soc_upper_limit_2:
            proposed_kw = 0.0

        # ------------ FINALIZE ------------
        self.commanded_power_kw = proposed_kw

    # ------------------------------------------
    # Electrical helpers
    # ------------------------------------------
    def battery_voltage(self) -> float:
        """Estimate battery DC bus voltage as deterministic function of SOC."""
        base_voltage = 700.0
        soc_factor = 1.0 + 0.1 * math.sin((self.soc / 100.0) * math.pi)
        voc = base_voltage * soc_factor
        # compute current based on commanded power (not target) to avoid recursion
        I = (self.commanded_power_kw * 1000.0 / voc) if voc != 0 else 0.0
        V_final = voc - abs(I) * self.internal_resistance

        return max(500.0, V_final)

    def service_current(self) -> float:
        V = self.battery_voltage()
        return (self.commanded_power_kw * 1000.0 / V) if V != 0 else 0.0

    # ------------------------------------------
    # Thermal model (deterministic)
    # ------------------------------------------
    def update_temperature(self, dt: Optional[float] = None, ambient: Optional[float] = None) -> float:
        if dt is None:
            dt = self._last_dt if hasattr(self, "_last_dt") else self.time_step_sec
        if ambient is None:
            ambient = self.ambient_temp_c

        I = abs(self.service_current())
        joule_energy = (I * I) * self.internal_resistance * dt  # J
        Tdiff = max(0.0, self.temperature_c - ambient)
        cooling_power = self.thermal_conductance_w_per_k * Tdiff
        cooling_energy = cooling_power * dt
        C = self.thermal_capacity_j_per_k
        delta_T = (joule_energy - cooling_energy) / C
        self.temperature_c += delta_T
        # clamp
        self.temperature_c = max(ambient, min(80.0, self.temperature_c))
        return self.temperature_c

    # ------------------------------------------
    # Main discrete-time simulation step
    # ------------------------------------------
    def simulate_values(self, dt: Optional[float] = None) -> Dict[str, float]:
        """
        Compute telemetry / device state for one simulation step.
        dt: seconds since last simulate_values call (if None, uses self.time_step_sec).
        Returns telemetry dict (names -> values) exactly like before.
        """
        if dt is None:
            dt = self.time_step_sec
        # make sure dt is a float
        dt = float(dt)
        self._last_dt = dt

        # Update command application using explicit dt
        self.apply_commanded_power(dt=dt)

        # Energy accounting (kWh)
        dt_h = dt / 3600.0
        eff = math.sqrt(self.round_trip_eff)
        if self.commanded_power_kw > 0:  # discharge (positive = export)
            delta_kwh = -(self.commanded_power_kw * eff * dt_h)
            self.discharge_energy_total_kwh += -delta_kwh
        elif self.commanded_power_kw < 0:  # charge
            delta_kwh = -(self.commanded_power_kw / eff * dt_h)
            self.charge_energy_total_kwh += delta_kwh
        else:
            delta_kwh = 0.0

        # SOC update (deterministic)
        self.soc = max(0.0, min(100.0, self.soc + delta_kwh / self.rated_capacity_kwh * 100.0))

        # Cycle counter
        self.cycles = (self.charge_energy_total_kwh + self.discharge_energy_total_kwh) / self.rated_capacity_kwh

        # Temperature update
        self.temperature_c = self.update_temperature(dt=dt, ambient=self.ambient_temp_c)

        # AC side metrics
        reactive_power = 0.1 * self.commanded_power_kw
        apparent_power = math.hypot(self.commanded_power_kw, reactive_power)

        # Working status
        if self.soc < 10.0:
            working_status = 2
        elif 10.0 <= self.soc <= 95.0:
            working_status = 1
        else:
            working_status = 0

        freq_error = 50.0 - self.grid_frequency_hz
        # relax at 0.01 * freq_error per step scaled by dt / time_step_sec to be more stable
        self.grid_frequency_hz += (freq_error * 0.01) * (dt / self.time_step_sec)
        # if not deterministic, optionally add tiny jitter (kept small)
        if not self.deterministic:
            import random
            self.grid_frequency_hz += random.uniform(-0.01, 0.01)

        # clamp frequency reasonable bounds
        self.grid_frequency_hz = max(45.0, min(55.0, self.grid_frequency_hz))

        telemetry = {
            "service_voltage": self.battery_voltage(),
            "service_current": self.service_current(),
            "system_soc": self.soc,
            "battery_temperature": self.temperature_c,
            "total_charge_energy": self.charge_energy_total_kwh,
            "total_discharge_energy": self.discharge_energy_total_kwh,
            "active_power": self.commanded_power_kw,       # kW (measured)
            "reactive_power": reactive_power,
            "apparent_power": apparent_power,
            "max_charge_power": self.max_charge_kw,
            "max_discharge_power": self.max_discharge_kw,
            "working_status": working_status,
            "fault_code": self.fault_code,
            "charge_and_discharge_cycles": self.cycles,
            "system_soh": self.soh,
            "available_charging_power": self.max_charge_kw,
            "available_discharging_power": self.max_discharge_kw,
            "on_grid_power": self.on_grid_power_kw,
            "grid_frequency": self.grid_frequency_hz,
        }

        return telemetry

    # ------------------------------------------
    # Write instruction handler (unchanged)
    # ------------------------------------------
    def execute_write_instructions(self, instructions: Dict[str, float]) -> Dict[str, float]:
        applied = {}

        for name, val in instructions.items():
            last_val = self._last_applied_commands.get(name)
            if last_val == val:
                applied[name] = val
                continue

            self._last_applied_commands[name] = val

            if name == "start_stop_standby":
                if val == 1:
                    self.mode = "discharge"
                elif val == 2:
                    self.mode = "idle"
                    self.set_on_grid_power_kw(0.0)
                elif val == 3:
                    self.mode = "standby"
                    self.set_on_grid_power_kw(0.0)
                applied[name] = val

            elif name == "local_remote_settings":
                self.local_remote_mode = int(val)
                applied[name] = self.local_remote_mode

            elif name == "power_control_mode":
                self.power_control_mode = int(val)
                applied[name] = self.power_control_mode

            elif name == "on_grid_power":
                # accept float and store
                self.set_on_grid_power_kw(float(val))
                applied[name] = self.on_grid_power_kw

            elif name == "fault_reset":
                if val == 1:
                    self.fault_code = 0
                applied[name] = self.fault_code

            elif name in ["soc_upper_limit_1", "soc_upper_limit_2",
                          "soc_lower_limit_1", "soc_lower_limit_2"]:
                setattr(self, name, float(val))
                applied[name] = float(val)

            else:
                applied[name] = val

        return applied

    def init_applied_commands(self, commands: Dict[str, float]):
        self._last_applied_commands = dict(commands or {})

    def set_grid_frequency(self, hz: float):
        self.grid_frequency_hz = float(hz)
