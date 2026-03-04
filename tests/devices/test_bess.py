from dertwin.devices.bess.simulator import BESSSimulator

def test_initial_state_reasonable():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    assert 40 <= bess.soc <= 60
    assert bess.mode == "idle"
    assert bess.commanded_power_kw == 0
    assert bess.max_charge_kw == 20
    assert bess.max_discharge_kw == 20


def test_initial_fault_code_zero():
    bess = BESSSimulator()
    assert bess.fault_code == 0


def test_initial_soh_100():
    bess = BESSSimulator()
    assert bess.battery.soh == 100.0


# ============================================================
# POWER LIMITS
# ============================================================

def test_apply_commanded_power_respects_limits():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)

    bess.set_on_grid_power_kw(50)
    bess.apply_commanded_power(dt=0.1)
    assert bess.commanded_power_kw <= bess.max_discharge_kw

    bess.set_on_grid_power_kw(-50)
    bess.apply_commanded_power(dt=0.1)
    assert bess.commanded_power_kw >= -bess.max_charge_kw


def test_apply_commanded_power_ramp_limit():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    bess.commanded_power_kw = 0
    bess.controller.apply_command("start_stop_standby", 1)

    bess.set_on_grid_power_kw(100)
    bess.apply_commanded_power(dt=0.1)
    assert bess.commanded_power_kw == 0.5  # 5 kW/s * 0.1s


def test_command_on_grid_power_respects_limits():
    bess = BESSSimulator(ramp_rate_kw_per_s=100.0)
    bess.apply_commands({"start_stop_standby": 1})

    bess.apply_commands({"active_power_setpoint": 100})
    bess.update(dt=0.1)
    assert bess.commanded_power_kw == 10  # ramp: 100 * 0.1 = 10

    bess.update(dt=0.1)
    assert bess.commanded_power_kw == 20  # hits max_discharge_kw=20

    bess.apply_commands({"active_power_setpoint": -100})
    bess.update(dt=0.1)
    assert bess.commanded_power_kw == 10  # ramp down: 20 → 10

    bess.update(dt=0.1)
    assert bess.commanded_power_kw == 0   # 10 → 0

    for _ in range(2):
        bess.update(dt=0.1)
    assert bess.commanded_power_kw == -20  # 0 → -10 → -20


# ============================================================
# SOC DYNAMICS
# ============================================================

def test_soc_updates_correctly():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    bess.soc = 50
    bess.controller.apply_command("start_stop_standby", 1)
    bess.set_on_grid_power_kw(10)
    prev_soc = bess.soc
    bess.update(dt=0.1)
    assert bess.get_telemetry().system_soc < prev_soc


def test_realistic_charge_discharge_cycle():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    bess.soc = 50
    bess.controller.apply_command("start_stop_standby", 1)

    dt = 0.1

    bess.apply_commands({"active_power_setpoint": 20})
    for _ in range(int((30 * 60) / dt)):
        bess.update(dt=dt)
    mid_soc = bess.soc

    assert 39 < mid_soc < 41

    bess.apply_commands({"active_power_setpoint": -20})
    for _ in range(int((30 * 60) / dt)):
        bess.update(dt)
    final_soc = bess.soc

    assert final_soc > mid_soc
    assert 49 < final_soc < 51


# ============================================================
# START / STOP / STANDBY
# ============================================================

def test_command_start_stop():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)

    bess.apply_commands({"start_stop_standby": 1})
    assert bess.mode == "run"

    bess.apply_commands({"start_stop_standby": 2})
    bess.update(0.1)
    assert bess.mode == "idle"
    assert bess.commanded_power_kw == 0

    bess.apply_commands({"start_stop_standby": 3})
    bess.update(0.1)
    assert bess.mode == "standby"
    assert bess.commanded_power_kw == 0


def test_power_forced_zero_when_stopped():
    bess = BESSSimulator()
    bess.apply_commands({"start_stop_standby": 1, "active_power_setpoint": 10})
    bess.update(1.0)
    assert bess.commanded_power_kw > 0

    bess.apply_commands({"start_stop_standby": 2})
    bess.update(0.1)
    assert bess.commanded_power_kw == 0


def test_stop_prevents_on_grid_power_override():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    bess.apply_commands({"start_stop_standby": 1})
    bess.apply_commands({"active_power_setpoint": 50})
    bess.update(dt=0.1)
    assert bess.commanded_power_kw != 0

    bess.apply_commands({"start_stop_standby": 2})
    bess.update(dt=0.1)
    assert bess.mode == "idle"
    assert bess.commanded_power_kw == 0

    bess.apply_commands({"on_grid_power_setpoint": 50, "start_stop_standby": 2})
    assert bess.commanded_power_kw == 0


# ============================================================
# FAULT HANDLING
# ============================================================

def test_command_fault_reset():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    bess.fault_code = 7
    bess.apply_commands({"fault_reset": 1})
    assert bess.fault_code == 0


def test_overtemperature_triggers_fault():
    """Battery temperature above 75°C must auto-trigger a fault code."""
    bess = BESSSimulator()
    bess.apply_commands({"start_stop_standby": 1})
    bess.battery.temperature_c = 76.0
    bess.update(dt=0.1)
    assert bess.fault_code != 0


def test_fault_blocks_power_dispatch():
    """Active fault must prevent power delivery even in run mode."""
    bess = BESSSimulator(ramp_rate_kw_per_s=100.0)
    bess.apply_commands({"start_stop_standby": 1})
    bess.apply_commands({"active_power_setpoint": 20})

    bess.battery.temperature_c = 76.0  # trigger fault on next step
    bess.update(dt=1.0)

    assert bess.fault_code != 0
    assert bess.commanded_power_kw == 0


def test_fault_reset_allows_power_again():
    """After fault is cleared, power dispatch should resume."""
    bess = BESSSimulator(ramp_rate_kw_per_s=100.0)
    bess.apply_commands({"start_stop_standby": 1})
    bess.apply_commands({"active_power_setpoint": 20})

    bess.battery.temperature_c = 76.0
    bess.update(dt=1.0)
    assert bess.fault_code != 0

    # Fix temperature and reset fault
    bess.battery.temperature_c = 25.0
    bess.apply_commands({"fault_reset": 1})
    # fault_reset is a stateless command — no deduplication, always fires
    bess.update(dt=1.0)

    assert bess.fault_code == 0
    assert bess.commanded_power_kw > 0


def test_soc_zero_triggers_fault():
    """
    SOC reaching 0 must auto-trigger a fault.

    SOC derating hard cutoff (soc_lower_limit_2) normally prevents reaching 0.
    Set limits to 0 so discharge can drain the battery fully and trigger the fault.
    """
    from dertwin.devices.bess.battery import BatteryLimits

    bess = BESSSimulator(
        capacity_kwh=1.0,
        initial_soc=1.0,
        max_discharge_kw=100.0,
        ramp_rate_kw_per_s=1000.0,
    )
    # Remove SOC protection so battery can reach 0
    bess.battery.limits = BatteryLimits(
        soc_lower_limit_1=0.0,
        soc_lower_limit_2=0.0,
        soc_upper_limit_1=100.0,
        soc_upper_limit_2=100.0,
    )
    bess.apply_commands({"start_stop_standby": 1})
    bess.apply_commands({"active_power_setpoint": 100})

    for _ in range(1000):
        bess.update(dt=0.1)

    assert bess.fault_code != 0


# ============================================================
# COMMAND CHANNELS
# ============================================================

def test_command_local_remote():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    bess.apply_commands({"local_remote_settings": 2})
    assert bess.local_remote_settings == 2


def test_command_power_control_mode():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    bess.apply_commands({"power_control_mode": 1})
    assert bess.power_control_mode == 1


def test_soc_limit_writes():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)

    bess.apply_commands({"soc_upper_limit_1": 90})
    assert bess.soc_upper_limit_1 == 90

    bess.apply_commands({"soc_upper_limit_2": 95})
    assert bess.soc_upper_limit_2 == 95

    bess.apply_commands({"soc_lower_limit_1": 30})
    assert bess.soc_lower_limit_1 == 30

    bess.apply_commands({"soc_lower_limit_2": 15})
    assert bess.soc_lower_limit_2 == 15


def test_soc_limits_actually_affect_derating():
    """Writing new SOC limits must change physical behavior, not just store values."""
    bess = BESSSimulator(
        capacity_kwh=100.0,
        initial_soc=35.0,
        max_discharge_kw=20.0,
        ramp_rate_kw_per_s=1000.0,
    )
    bess.apply_commands({"start_stop_standby": 1})

    # At SOC=35 with default lower_limit_1=25, full power should be available
    bess.apply_commands({"active_power_setpoint": 20})
    bess.update(dt=1.0)
    power_before = bess.commanded_power_kw

    # Now raise lower_limit_1 to 40 — SOC=35 is now inside derating zone
    bess.apply_commands({"soc_lower_limit_1": 40, "soc_lower_limit_2": 30})
    bess.commanded_power_kw = 0  # reset ramp state
    bess.apply_commands({"active_power_setpoint": 20})
    bess.update(dt=1.0)
    power_after = bess.commanded_power_kw

    assert power_after < power_before


# ============================================================
# SERVICE CURRENT
# ============================================================

def test_service_current_uses_commanded_power():
    bess = BESSSimulator()
    bess.commanded_power_kw = 10
    bess.soc = 50

    V = bess.battery_voltage()
    expected_I = 10000 / V

    assert abs(bess.service_current() - expected_I) < 1e-6


def test_service_current_zero_at_zero_power():
    bess = BESSSimulator()
    bess.commanded_power_kw = 0
    assert bess.service_current() == 0.0


# ============================================================
# DERATING END-TO-END
# ============================================================

def test_apply_commanded_power_ordering_and_limits():
    dt = 0.1
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    bess.apply_commands({"start_stop_standby": 1})

    bess.apply_commands({"active_power_setpoint": 200})
    bess.update(dt)
    assert abs(bess.commanded_power_kw - 0.5) < 1e-6

    low = min(bess.soc_lower_limit_1, bess.soc_lower_limit_2)
    high = max(bess.soc_lower_limit_1, bess.soc_lower_limit_2)
    mid_soc = (low + high) / 2.0

    bess.soc = mid_soc
    bess.commanded_power_kw = 0

    bess.apply_commands({"active_power_setpoint": 20})
    bess.update(dt)

    ramp_step = bess.ramp_rate_kw_per_s * dt
    factor = (bess.soc - low) / (high - low)
    factor = max(0.0, min(1.0, factor))
    derated_target = 20 * factor
    expected = min(ramp_step, derated_target)

    assert abs(bess.commanded_power_kw - expected) < 1e-6

    bess.soc = low - 1.0
    bess.commanded_power_kw = 0
    bess.apply_commands({"active_power_setpoint": 20})
    bess.update(dt)
    assert bess.commanded_power_kw == 0