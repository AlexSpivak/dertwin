from collections import deque
from typing import Dict

from dertwin.core.device import SimulatedDevice
from dertwin.devices.energy_meter.model import EnergyMeterModel
from dertwin.devices.external.grid_voltage import GridVoltageModel
from dertwin.devices.external.power_flow import SitePowerModel
from dertwin.devices.external.grid_frequency import GridFrequencyModel
from dertwin.telemetry.energy_meter import EnergyMeterTelemetry


class EnergyMeterSimulator(SimulatedDevice):
    """
    Passive PCC energy meter simulator.

    Observes:
        - SitePowerModel (power balance)
        - GridFrequencyModel (frequency)
        - GridVoltageModel (voltage)

    Tracks:
        - Per-phase import/export energy accumulators (kWh)
        - Sliding window demand (kW) with configurable window
    """

    def __init__(
        self,
        power_model: SitePowerModel,
        grid_model: GridFrequencyModel,
        grid_voltage_model: GridVoltageModel,
        seed: int | None = None,
        demand_window_min: float = 15.0,
    ):
        self.power_model = power_model
        self.grid_model = grid_model
        self.grid_voltage_model = grid_voltage_model

        self.model = EnergyMeterModel(seed=seed)

        self._last_telemetry: EnergyMeterTelemetry = EnergyMeterTelemetry.zero()

        # Per-phase energy accumulators (kWh)
        # Positive phase power = import, negative = export
        self._phase_import_energy_kwh = [0.0, 0.0, 0.0]   # A, B, C
        self._phase_export_energy_kwh = [0.0, 0.0, 0.0]   # A, B, C

        # Sliding window demand
        self._demand_window_s = demand_window_min * 60.0
        self._demand_samples: deque = deque()              # (sim_time, power_kw)
        self._sim_time: float = 0.0
        self._current_demand_kw: float = 0.0
        self._max_demand_kw: float = 0.0

    # --------------------------------------------------
    # Private helpers
    # --------------------------------------------------

    def _update_phase_energy(self, phase_powers_kw: list[float], dt: float):
        dt_h = dt / 3600.0
        for i, p in enumerate(phase_powers_kw):
            if p > 0:
                self._phase_import_energy_kwh[i] += p * dt_h
            elif p < 0:
                self._phase_export_energy_kwh[i] += abs(p) * dt_h

    def _update_demand(self, power_kw: float):
        self._demand_samples.append((self._sim_time, abs(power_kw)))

        cutoff = self._sim_time - self._demand_window_s
        while self._demand_samples and self._demand_samples[0][0] < cutoff:
            self._demand_samples.popleft()

        if self._demand_samples:
            self._current_demand_kw = sum(
                p for _, p in self._demand_samples
            ) / len(self._demand_samples)

            if self._current_demand_kw > self._max_demand_kw:
                self._max_demand_kw = self._current_demand_kw

    # --------------------------------------------------
    # Simulation Step
    # --------------------------------------------------

    def update(self, dt: float) -> None:
        self._sim_time += dt

        frequency = self.grid_model.get_frequency()
        voltage_ll = self.grid_voltage_model.get_voltage_ll()

        telemetry = self.model.measure(
            grid_power_kw=self.power_model.grid_power_kw,
            import_energy_kwh=self.power_model.import_energy_kwh,
            export_energy_kwh=self.power_model.export_energy_kwh,
            grid_frequency=frequency,
            voltage_ll=voltage_ll,
        )

        # Update per-phase accumulators from balanced phase powers
        phase_powers = [
            telemetry.phase_active_power_a,
            telemetry.phase_active_power_b,
            telemetry.phase_active_power_c,
        ]
        self._update_phase_energy(phase_powers, dt)
        self._update_demand(telemetry.total_active_power)

        # Attach accumulated values to telemetry
        telemetry.phase_import_energy_a = self._phase_import_energy_kwh[0]
        telemetry.phase_import_energy_b = self._phase_import_energy_kwh[1]
        telemetry.phase_import_energy_c = self._phase_import_energy_kwh[2]
        telemetry.phase_export_energy_a = self._phase_export_energy_kwh[0]
        telemetry.phase_export_energy_b = self._phase_export_energy_kwh[1]
        telemetry.phase_export_energy_c = self._phase_export_energy_kwh[2]
        telemetry.current_demand_kw = self._current_demand_kw
        telemetry.max_demand_kw = self._max_demand_kw

        self._last_telemetry = telemetry

    # --------------------------------------------------
    # Telemetry
    # --------------------------------------------------
    def get_telemetry(self) -> EnergyMeterTelemetry:
        return self._last_telemetry

    # --------------------------------------------------
    # No Control Capability
    # --------------------------------------------------
    def apply_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        return {}

    def init_applied_commands(self, commands: Dict[str, float]) -> Dict[str, float]:
        return {}