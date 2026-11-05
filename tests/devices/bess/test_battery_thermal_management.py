"""
Battery thermal management: the managed container and the capability curve.

Two contracts are checked here.

The feature: a managed container holds the cell inside its setpoint band, so a
site whose ambient walks through the cold derate keeps full capability, and the
container's own HVAC draw reaches the meter as site load.

The compatibility: with no `thermal` block, every number the battery produces
over a simulated day is the number 0.2.1 produced. `_V021Battery` below carries
the 0.2.1 thermal code verbatim, and the golden test steps the two side by side.
"""

import math

import pytest

from dertwin.controllers.site_controller import SiteController
from dertwin.devices.bess.battery import (
    DEFAULT_DERATE_BANDS,
    BatteryModel,
    BatteryThermalConfig,
    TemperatureDerate,
)
from dertwin.devices.bess.simulator import BESSSimulator
from dertwin.devices.external.ambient_temperature import AmbientTemperatureModel


DAY_S = 86400.0
STEP_S = 60.0

# Ambient that spends the whole day inside the cold derate band: 4 °C at the
# night minimum, 14 °C at the afternoon peak, so an unmanaged pack is derated
# around the clock and never once reaches full capability.
COLD_DAY = {"mean_temp_c": 9.0, "amplitude_c": 5.0}


def _managed(**overrides) -> BatteryThermalConfig:
    return BatteryThermalConfig.from_config({"managed": True, **overrides})


# ----------------------------------------------------------------------
# The 0.2.1 model, for the golden comparison
# ----------------------------------------------------------------------

class _V021Battery(BatteryModel):
    """
    BatteryModel with the 0.2.1 thermal code pasted back in.

    Everything else — energy integration, the SOC window, efficiency, the
    counters — is inherited, so stepping this alongside a default 0.2.2
    battery compares the whole pipeline and not just the two methods.
    """

    def _temperature_scale(self) -> float:
        t = self.temperature_c

        if t <= 0:
            return 0.5
        elif t <= 10:
            return 0.8
        elif t <= 40:
            return 1.0
        elif t <= 50:
            return 0.8
        elif t <= 60:
            return 0.5
        else:
            return 0.0

    def update_temperature(self, power_kw: float, dt: float):
        voc = self.open_circuit_voltage()
        current = (power_kw * 1000.0 / voc) if voc else 0.0
        joule = current * current * self.internal_resistance * dt
        Tdiff = max(0.0, self.temperature_c - self.ambient_temp_c)
        cooling = self.thermal_conductance_w_per_k * Tdiff * dt

        delta_T = (joule - cooling) / self.thermal_capacity_j_per_k
        self.temperature_c += delta_T
        self.temperature_c = max(self.ambient_temp_c, min(80.0, self.temperature_c))


# ----------------------------------------------------------------------
# Defaults: absent block == 0.2.1
# ----------------------------------------------------------------------

def test_absent_thermal_block_is_an_unmanaged_pack():
    thermal = BatteryThermalConfig.from_config(None)

    assert thermal.managed is False
    assert thermal.aux_power_kw == 0.0
    assert thermal.derate.bands == DEFAULT_DERATE_BANDS
    assert thermal.derate.above_factor == 0.0


def test_default_derate_bands_are_the_0_2_1_curve():
    derate = TemperatureDerate()

    for temperature_c, expected in (
        (-20.0, 0.5), (0.0, 0.5),
        (0.1, 0.8), (10.0, 0.8),
        (10.1, 1.0), (25.0, 1.0), (40.0, 1.0),
        (40.1, 0.8), (50.0, 0.8),
        (50.1, 0.5), (60.0, 0.5),
        (60.1, 0.0), (90.0, 0.0),
    ):
        assert derate.factor_at(temperature_c) == expected


def test_unmanaged_day_is_identical_to_0_2_1():
    """
    Golden comparison over a simulated day.

    Both packs see the same ambient curve and the same dispatch schedule —
    idle, discharge, charge, idle — and every observable is compared on every
    one of the 1440 steps: applied power, SOC, cell temperature, the capability
    window and the lifetime counters.
    """

    def build(cls):
        return cls(
            capacity_kwh=100.0,
            initial_soc=60.0,
            max_charge_kw=50.0,
            max_discharge_kw=50.0,
            ambient_temp_c=9.0,
            self_discharge_percent_per_day=0.5,
        )

    current = build(BatteryModel)
    golden = build(_V021Battery)

    ambient_current = AmbientTemperatureModel(**COLD_DAY)
    ambient_golden = AmbientTemperatureModel(**COLD_DAY)

    steps = int(DAY_S / STEP_S)

    for i in range(steps):
        sim_time = i * STEP_S

        # Four six-hour blocks: idle, discharge, charge, idle.
        hour = sim_time / 3600.0
        if hour < 6.0:
            requested_kw = 0.0
        elif hour < 12.0:
            requested_kw = 40.0
        elif hour < 18.0:
            requested_kw = -40.0
        else:
            requested_kw = 0.0

        for battery, ambient in (
            (current, ambient_current),
            (golden, ambient_golden),
        ):
            ambient.update(sim_time, STEP_S)
            battery.set_ambient_temperature(ambient.get_temperature())

        applied_current = current.step(requested_kw, STEP_S)
        applied_golden = golden.step(requested_kw, STEP_S)

        assert applied_current == applied_golden, f"power diverged at step {i}"
        assert current.temperature_c == golden.temperature_c, (
            f"temperature diverged at step {i}"
        )
        assert current.energy_kwh == golden.energy_kwh, f"energy diverged at step {i}"
        assert current.get_power_limits() == golden.get_power_limits(), (
            f"capability diverged at step {i}"
        )

    assert current.soc == golden.soc
    assert current.cycles == golden.cycles
    assert current.soh == golden.soh
    assert current.charge_energy_total_kwh == golden.charge_energy_total_kwh
    assert current.discharge_energy_total_kwh == golden.discharge_energy_total_kwh

    # The day was genuinely spent in the cold derate, so the comparison had
    # something to disagree about.
    assert current.temperature_c < 15.0
    assert current.hvac_power_kw == 0.0


def test_unmanaged_pack_still_derates_in_the_cold():
    battery = BatteryModel(100.0, ambient_temp_c=4.0)

    battery.step(0.0, STEP_S)

    assert battery.temperature_c == pytest.approx(4.0)
    assert battery._temperature_scale() == 0.8


# ----------------------------------------------------------------------
# The managed container
# ----------------------------------------------------------------------

def test_managed_pack_boots_inside_its_band():
    battery = BatteryModel(100.0, ambient_temp_c=4.0, thermal=_managed())

    assert battery.temperature_c == 15.0
    assert battery._temperature_scale() == 1.0


def test_managed_pack_never_derates_across_a_cold_day():
    """The headline contract: 4–14 °C ambient, full nameplate all day."""

    thermal = _managed(aux_power_kw=0.4)
    ambient = AmbientTemperatureModel(**COLD_DAY)

    bess = BESSSimulator(
        capacity_kwh=100.0,
        initial_soc=50.0,
        max_charge_kw=50.0,
        max_discharge_kw=50.0,
        ambient_temp_c=9.0,
        thermal=thermal,
        ambient_temp_model=ambient,
    )

    coldest_ambient = None
    warmest_ambient = None

    for i in range(int(DAY_S / STEP_S)):
        ambient.update(i * STEP_S, STEP_S)
        bess.update(STEP_S)

        ambient_c = ambient.get_temperature()
        coldest_ambient = (
            ambient_c if coldest_ambient is None else min(coldest_ambient, ambient_c)
        )
        warmest_ambient = (
            ambient_c if warmest_ambient is None else max(warmest_ambient, ambient_c)
        )

        telemetry = bess.get_telemetry()

        assert thermal.heat_setpoint_c <= telemetry.battery_temperature
        assert telemetry.battery_temperature <= thermal.cool_setpoint_c
        assert bess.battery._temperature_scale() == 1.0
        assert telemetry.available_charging_power == pytest.approx(50.0)
        assert telemetry.available_discharging_power == pytest.approx(50.0)

    # The ambient really did sweep the cold derate band all day.
    assert 4.0 <= coldest_ambient < 5.0
    assert 13.0 < warmest_ambient <= 14.0

    # Heating was needed throughout, so the container drew its aux power.
    assert bess.get_telemetry().hvac_power_kw == pytest.approx(0.4)


def test_managed_pack_relaxes_toward_ambient_with_the_configured_time_constant():
    """Inside the band nothing is held: the cell decays toward ambient at tau."""

    tau_s = 2500.0
    battery = BatteryModel(
        100.0,
        ambient_temp_c=30.0,
        thermal=_managed(aux_power_kw=1.0, time_constant_s=tau_s),
    )

    assert battery.hvac_time_constant_s == tau_s
    assert battery.temperature_c == 30.0  # boots at the cool setpoint

    battery.set_ambient_temperature(20.0)

    elapsed = 0.0
    while elapsed < tau_s:
        battery.step(0.0, STEP_S)
        elapsed += STEP_S

    # One time constant of first-order decay from 30 toward 20.
    assert battery.temperature_c == pytest.approx(
        20.0 + 10.0 * math.exp(-1.0), rel=2e-2
    )

    # Still inside the band, so the HVAC never had to do anything.
    assert battery.hvac_power_kw == 0.0


def test_managed_pack_is_held_at_the_cool_setpoint_when_ambient_climbs_past_it():
    """The exchange with ambient is signed, so a hot day pushes the cell up."""

    battery = BatteryModel(
        100.0, ambient_temp_c=25.0, thermal=_managed(aux_power_kw=0.75)
    )

    assert battery.temperature_c == 25.0

    battery.set_ambient_temperature(45.0)

    for _ in range(600):  # 10 h
        battery.step(0.0, STEP_S)

    assert battery.temperature_c == pytest.approx(30.0)
    assert battery._temperature_scale() == 1.0
    assert battery.hvac_power_kw == pytest.approx(0.75)


def test_managed_pack_draws_nothing_while_ambient_sits_inside_the_band():
    battery = BatteryModel(
        100.0, ambient_temp_c=22.0, thermal=_managed(aux_power_kw=2.0)
    )

    for _ in range(600):
        battery.step(0.0, STEP_S)

    assert battery.temperature_c == pytest.approx(22.0)
    assert battery.hvac_power_kw == 0.0


def test_managed_pack_time_constant_defaults_to_the_pack_thermal_constant():
    battery = BatteryModel(
        100.0,
        thermal_capacity_j_per_k=400_000.0,
        thermal_conductance_w_per_k=200.0,
        thermal=_managed(),
    )

    assert battery.hvac_time_constant_s == pytest.approx(2000.0)


def test_managed_pack_restored_from_a_cold_snapshot_lands_inside_the_band():
    battery = BatteryModel(100.0, ambient_temp_c=4.0, thermal=_managed())

    battery.restore_state({
        "capacity_kwh": 100.0, "energy_kwh": 50.0, "temperature_c": -5.0,
    })

    assert battery.temperature_c == 15.0
    assert battery._temperature_scale() == 1.0


def test_unmanaged_pack_restored_from_a_cold_snapshot_keeps_it():
    battery = BatteryModel(100.0, ambient_temp_c=4.0)

    battery.restore_state({
        "capacity_kwh": 100.0, "energy_kwh": 50.0, "temperature_c": -5.0,
    })

    assert battery.temperature_c == -5.0


# ----------------------------------------------------------------------
# Configurable derate
# ----------------------------------------------------------------------

def test_configured_derate_bands_replace_the_defaults():
    thermal = BatteryThermalConfig.from_config({
        "derate": {
            "bands": [[-10.0, 0.0], [5.0, 0.9], [45.0, 1.0]],
            "above_factor": 0.25,
        },
    })

    battery = BatteryModel(100.0, ambient_temp_c=4.0, thermal=thermal)

    # 4 °C used to be a 0.8 derate; this pack calls it 0.9.
    assert battery._temperature_scale() == 0.9

    battery.temperature_c = 50.0
    assert battery._temperature_scale() == 0.25

    battery.temperature_c = -20.0
    assert battery._temperature_scale() == 0.0


def test_configured_derate_reaches_the_capability_registers():
    thermal = BatteryThermalConfig.from_config({
        "derate": {"bands": [[10.0, 0.25], [40.0, 1.0]]},
    })

    bess = BESSSimulator(
        capacity_kwh=100.0,
        max_charge_kw=40.0,
        max_discharge_kw=40.0,
        ambient_temp_c=5.0,
        thermal=thermal,
    )

    bess.update(STEP_S)
    telemetry = bess.get_telemetry()

    assert telemetry.available_charging_power == pytest.approx(10.0)
    assert telemetry.available_discharging_power == pytest.approx(10.0)


# ----------------------------------------------------------------------
# Config validation
# ----------------------------------------------------------------------

def test_heat_setpoint_above_cool_setpoint_is_rejected():
    with pytest.raises(ValueError, match="heat_setpoint_c"):
        BatteryThermalConfig.from_config({
            "managed": True, "heat_setpoint_c": 30.0, "cool_setpoint_c": 20.0,
        })


def test_equal_setpoints_are_allowed():
    thermal = BatteryThermalConfig.from_config({
        "managed": True, "heat_setpoint_c": 20.0, "cool_setpoint_c": 20.0,
    })

    battery = BatteryModel(100.0, ambient_temp_c=-10.0, thermal=thermal)
    battery.step(0.0, STEP_S)

    assert battery.temperature_c == pytest.approx(20.0)


def test_site_build_rejects_an_inverted_setpoint_band():
    site = SiteController({
        "site_name": "inverted-band",
        "step": 1.0,
        "real_time": False,
        "assets": [{
            "type": "bess",
            "capacity_kwh": 10.0,
            "thermal": {
                "managed": True,
                "heat_setpoint_c": 25.0,
                "cool_setpoint_c": 15.0,
            },
        }],
    })

    with pytest.raises(ValueError, match="cool_setpoint_c"):
        site.build()


def test_cool_setpoint_above_the_cell_limit_is_rejected():
    with pytest.raises(ValueError, match="cool_setpoint_c"):
        BatteryThermalConfig.from_config({
            "managed": True, "cool_setpoint_c": 95.0,
        })


def test_negative_aux_power_is_rejected():
    with pytest.raises(ValueError, match="aux_power_kw"):
        BatteryThermalConfig.from_config({"managed": True, "aux_power_kw": -1.0})


def test_non_positive_time_constant_is_rejected():
    with pytest.raises(ValueError, match="time_constant_s"):
        BatteryThermalConfig.from_config({"managed": True, "time_constant_s": 0.0})


@pytest.mark.parametrize("derate_cfg", [
    {"bands": []},
    {"bands": [[10.0, 0.5], [5.0, 1.0]]},
    {"bands": [[10.0, 0.5], [10.0, 1.0]]},
    {"bands": [[10.0, 1.5]]},
    {"bands": [[10.0, -0.1]]},
    {"bands": [[10.0, 1.0]], "above_factor": -0.1},
    {"bands": [[10.0, 1.0]], "above_factor": 2.0},
])
def test_unusable_derate_configs_are_rejected(derate_cfg):
    with pytest.raises(ValueError, match="derate"):
        BatteryThermalConfig.from_config({"derate": derate_cfg})


@pytest.mark.parametrize("config, message", [
    (
        {"managed": True, "heat_setpoint_c": 30.0, "cool_setpoint_c": 20.0},
        "thermal.heat_setpoint_c (30.0) must not exceed "
        "thermal.cool_setpoint_c (20.0)",
    ),
    (
        {"derate": {"bands": [[10.0, 0.5], [5.0, 1.0]]}},
        "thermal.derate.bands bounds must increase strictly, got 5.0 after 10.0",
    ),
    (
        {"derate": {"bands": [[10.0, 1.5]]}},
        "thermal.derate band factor must be in [0, 1], got 1.5",
    ),
    (
        {"derate": {"bands": [[10.0, -0.1]]}},
        "thermal.derate band factor must be in [0, 1], got -0.1",
    ),
    (
        {"derate": {"bands": []}},
        "thermal.derate.bands needs at least one band",
    ),
    (
        {"managed": True, "cool_setpoint_c": 95.0},
        "thermal.cool_setpoint_c (95.0) must not exceed the 80.0 °C cell limit",
    ),
    (
        {"managed": True, "aux_power_kw": -1.0},
        "thermal.aux_power_kw must be non-negative, got -1.0",
    ),
    (
        {"managed": True, "time_constant_s": 0.0},
        "thermal.time_constant_s must be positive, got 0.0",
    ),
])
def test_validation_messages_name_the_offending_key(config, message):
    """
    The exact text, not just the exception type: a config error has to say
    which key to go and look at.
    """

    with pytest.raises(ValueError) as excinfo:
        BatteryThermalConfig.from_config(config)

    assert str(excinfo.value) == message


def test_thermal_is_an_accepted_asset_key():
    site = SiteController({"site_name": "keys", "assets": []})

    site._validate_asset_spec({
        "type": "bess",
        "thermal": {"managed": True, "aux_power_kw": 0.5},
    })


# ----------------------------------------------------------------------
# The aux draw in the site balance
# ----------------------------------------------------------------------

BASE_LOAD_KW = 7.0


def _site(thermal_cfg=None):
    """A cold site with one idle BESS and a meter at the PCC."""

    bess = {
        "type": "bess",
        "capacity_kwh": 100.0,
        "initial_soc": 50.0,
        "max_charge_kw": 50.0,
        "max_discharge_kw": 50.0,
    }
    if thermal_cfg is not None:
        bess["thermal"] = thermal_cfg

    site = SiteController({
        "site_name": "thermal-balance",
        "step": 60.0,
        "real_time": False,
        "external_models": {
            "power": {"base_load_w": BASE_LOAD_KW * 1000.0},
            "ambient_temperature": {"mean": 4.0, "amplitude": 0.0},
        },
        "assets": [bess, {"type": "energy_meter"}],
    })
    site.build()

    return site


# Long enough for the cell to settle against a constant ambient: the pack's
# thermal time constant is 2500 s, so ten hours is several of them.
SETTLE_STEPS = 600


async def _run(site, steps: int = SETTLE_STEPS):
    for _ in range(steps):
        await site.engine.step_once()

    return (
        site._devices_by_type["bess"][0].get_telemetry(),
        site._devices_by_type["energy_meter"][0].get_telemetry(),
    )


@pytest.mark.asyncio
async def test_hvac_aux_draw_appears_in_the_meter():
    site = _site({"managed": True, "aux_power_kw": 1.5})

    bess, meter = await _run(site)

    assert bess.hvac_power_kw == pytest.approx(1.5)
    assert bess.active_power == 0.0

    # Site load plus the container's draw, on the load side of the balance.
    assert meter.total_active_power == pytest.approx(BASE_LOAD_KW + 1.5)
    assert site.external_models.power_model.grid_power_kw == pytest.approx(
        BASE_LOAD_KW + 1.5
    )
    assert site.external_models.power_model.import_energy_kwh > 0.0

    # Accounted at the PCC only: the site load model is what it always was, so
    # the draw is not double-counted as load on the way there.
    assert site.external_models.load_model.get_power_kw() == pytest.approx(
        BASE_LOAD_KW
    )


@pytest.mark.asyncio
async def test_managed_container_without_aux_power_is_free():
    site = _site({"managed": True})

    bess, meter = await _run(site)

    assert bess.battery_temperature == pytest.approx(15.0)
    assert bess.hvac_power_kw == 0.0
    assert meter.total_active_power == pytest.approx(BASE_LOAD_KW)


@pytest.mark.asyncio
async def test_unmanaged_site_meter_is_untouched():
    site = _site()

    bess, meter = await _run(site)

    assert bess.battery_temperature == pytest.approx(4.0, abs=1e-3)
    assert bess.hvac_power_kw == 0.0
    assert meter.total_active_power == pytest.approx(BASE_LOAD_KW)

    # And the cold pack is derated, which is the thing a managed one avoids.
    assert bess.available_charging_power == pytest.approx(40.0)
    assert bess.available_discharging_power == pytest.approx(40.0)


@pytest.mark.asyncio
async def test_aux_draw_is_not_netted_off_bess_power():
    """
    The container's draw is site load, not inverter power: a discharging pack
    still reports what the inverter delivered, and the balance carries the aux
    separately.
    """

    site = _site({"managed": True, "aux_power_kw": 2.0})
    bess_device = site._devices_by_type["bess"][0]

    # Settle the cell against the cold ambient first, then dispatch: a ten-hour
    # discharge would walk the pack into its SOC floor and report 0 kW.
    await _run(site)

    bess_device.apply_commands({"start_stop_standby": 1})
    bess_device.apply_commands({"active_power_setpoint": 10.0})

    bess, meter = await _run(site, steps=5)

    assert bess.active_power == pytest.approx(10.0)
    assert bess.hvac_power_kw == pytest.approx(2.0)
    assert meter.total_active_power == pytest.approx(BASE_LOAD_KW + 2.0 - 10.0)
