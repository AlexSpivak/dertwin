from typing import Optional, Dict

from dertwin.devices.external.ambient_temperature import AmbientTemperatureModel
from dertwin.devices.external.irradiance import IrradianceModel
from dertwin.devices.external.power_flow import SitePowerModel
from dertwin.devices.external.grid_frequency import GridFrequencyModel, ConstantGridFrequencyModel
from dertwin.devices.external.grid_voltage import GridVoltageModel, ConstantGridVoltageModel


class ExternalModels:
    """
    Aggregates and advances all external world models.

    This provides a single deterministic update point for:

        - Power flow (site import/export)
        - Grid frequency
        - Grid voltage
        - Irradiance
        - Ambient temperature

    SimulationEngine calls update() exactly once per tick BEFORE devices step.

    This guarantees deterministic causality:
        world → devices → telemetry
    """

    def __init__(
        self,
        power_model: Optional[SitePowerModel] = None,
        grid_frequency_model: Optional[GridFrequencyModel] = None,
        grid_voltage_model: Optional[GridVoltageModel] = None,
        ambient_temperature_model: Optional[AmbientTemperatureModel]=None,
        irradiance_model: Optional[IrradianceModel]=None,
    ):
        self.power_model = power_model
        self.grid_frequency_model = grid_frequency_model
        self.grid_voltage_model = grid_voltage_model
        self.ambient_temperature_model = ambient_temperature_model
        self.irradiance_model = irradiance_model

    # ---------------------------------------------------------
    # STEP ALL EXTERNAL MODELS
    # ---------------------------------------------------------

    def update(self, sim_time: float, dt: float) -> None:
        """
        Advance all external world models.

        Called once per simulation tick BEFORE devices update.
        """

        # Power balance must run first so meter sees correct values
        if self.power_model:
            self.power_model.update(dt)

        # Grid electrical state
        if self.grid_frequency_model:
            self.grid_frequency_model.update(sim_time, dt)

        if self.grid_voltage_model:
            self.grid_voltage_model.update(sim_time, dt)

        # Future extensions
        if self.ambient_temperature_model:
            self.ambient_temperature_model.update(sim_time, dt)

        if self.irradiance_model:
            self.irradiance_model.update(sim_time, dt)

    @staticmethod
    def build_power_model(devices_by_type, config = None):
        base_load = 5.0 # default base load
        if config:
            base_load = config.get("power", {}).get("base_load_w")
            if base_load:
                base_load = float(base_load) / 1000  # kW

        bess_devices = devices_by_type.get("bess", [])
        pv_devices = devices_by_type.get("inverter", [])

        return SitePowerModel(
            base_load_supplier=lambda t: base_load,
            pv_supplier=lambda: sum(
                p.get_telemetry().total_active_power
                for p in pv_devices
            ),
            bess_supplier=lambda: sum(
                b.get_telemetry().active_power * 1000.0
                for b in bess_devices
            ),
        )

    @classmethod
    def build_default(cls):
        return cls(
            grid_frequency_model=ConstantGridFrequencyModel(),
            grid_voltage_model=ConstantGridVoltageModel(),
            ambient_temperature_model=None,
            irradiance_model=None,
        )

    @classmethod
    def from_config(cls, config: Dict):
        freq_cfg = config.get("grid_frequency", {})
        volt_cfg = config.get("grid_voltage", {})
        irr_cfg = config.get("irradiance", {})
        temp_cfg = config.get("ambient_temperature", {})

        return cls(
            grid_frequency_model=GridFrequencyModel(
                nominal_hz=freq_cfg.get("nominal_hz", 50.0),
                noise_std=freq_cfg.get("noise_std", 0.0),
                drift_std=freq_cfg.get("drift_std", 0.0),
                seed=freq_cfg.get("seed", None),
            ),

            grid_voltage_model=GridVoltageModel(
                nominal_v_ll=volt_cfg.get("nominal_voltage_ll", 400.0),
                noise_std=volt_cfg.get("noise_std", 0.0),
                drift_std=volt_cfg.get("drift_std", 0.0),
                seed=volt_cfg.get("seed", None),
            ),

            irradiance_model=IrradianceModel(
                peak_irradiance_w_m2=irr_cfg.get("peak", 1000.0),
                sunrise_hour=irr_cfg.get("sunrise", 6.0),
                sunset_hour=irr_cfg.get("sunset", 18.0),
            ),

            ambient_temperature_model=AmbientTemperatureModel(
                mean_temp_c=temp_cfg.get("mean", 20.0),
                amplitude_c=temp_cfg.get("amplitude", 5.0),
                peak_hour=temp_cfg.get("peak_hour", 15.0),
            ),
        )
