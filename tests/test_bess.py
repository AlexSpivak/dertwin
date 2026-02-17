from dertwin.devices.bess import BESSSimulator

def test_initial_state_reasonable():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    assert 40 <= bess.soc <= 60
    assert bess.mode == "idle"
    assert bess.commanded_power_kw == 0
    assert bess.max_charge_kw == 20
    assert bess.max_discharge_kw == 20

def test_apply_commanded_power_respects_limits():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)

    bess.set_on_grid_power_kw(50)
    bess.apply_commanded_power(dt=0.1)  # request huge discharge
    assert bess.commanded_power_kw <= bess.max_discharge_kw

    bess.set_on_grid_power_kw(-50)
    bess.apply_commanded_power(dt=0.1)  # request huge charge
    assert bess.commanded_power_kw >= -bess.max_charge_kw

def test_apply_commanded_power_ramp_limit():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    bess.commanded_power_kw = 0

    bess.set_on_grid_power_kw(100)
    bess.apply_commanded_power(dt=0.1)
    assert bess.commanded_power_kw == 0.5   # 5 kW/s * 0.1s step = 0.5 kW

def test_soc_updates_correctly():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    bess.soc = 50
    bess.set_on_grid_power_kw(10)
    prev_soc = bess.soc
    bess.update(dt=0.1)
    result = bess.get_telemetry()
    assert result["system_soc"] < prev_soc # discharged SoC a little on simulation

def test_realistic_charge_discharge_cycle():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    bess.soc = 50
    dt = 0.1
    # Phase 1: discharge 20 kW for 30 simulated minutes → SOC must drop
    bess.apply_commands({"on_grid_power_setpoint": 20})
    for _ in range(int((30 * 60) / dt)):
        bess.update(dt=dt)
    mid_soc = bess.soc

    # since we were discharging 100kW (default capacity) BESS to 20 kWh for 30 minutes
    # we should loose approx. 10% of SoC. With some loose on efficiency the SoC must be
    # in range between 39% and 41%
    assert 39 < mid_soc < 41

    # Phase 2: charge 20 kW for 30 minutes → SOC must recover
    bess.apply_commands({"on_grid_power_setpoint": -20})
    for _ in range(int((30 * 60) / dt)):
        bess.update(dt)
    final_soc = bess.soc

    # we charged here so final SoC is higher then mid SoC (after discharging)
    assert final_soc > mid_soc

    # since we were charging last 30 minutes for 20 kW and the initial default capacity 100 kW
    # the battery must gain approx 10% of SoC. So the SoC range should be between 49% and 51%
    assert 49 < final_soc < 51

def test_command_start_stop():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)

    bess.apply_commands({"start_stop_standby": 1})
    assert bess.mode == "discharge"

    bess.apply_commands({"start_stop_standby": 2})
    assert bess.mode == "idle"
    assert bess.commanded_power_kw == 0

    bess.apply_commands({"start_stop_standby": 3})
    assert bess.mode == "standby"
    assert bess.commanded_power_kw == 0

def test_command_local_remote():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    bess.apply_commands({"local_remote_settings": 2})
    assert bess.local_remote_mode == 2


def test_command_power_control_mode():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    bess.apply_commands({"power_control_mode": 1})
    assert bess.power_control_mode == 1

def test_command_on_grid_power_respects_limits():
    bess = BESSSimulator(ramp_rate_kw_per_s=100.0)

    # send +100 kW command
    bess.apply_commands({"on_grid_power_setpoint": 100})
    bess.update(dt=0.1)
    assert bess.commanded_power_kw == 10  # with ramp 100 from 0 to +10 in 0.1 sec

    bess.update(dt=0.1)
    assert bess.commanded_power_kw == 20  # another 0.1 sec and we hit to +20 (max)

    # -----------------------
    # Now test charging direction
    # -----------------------

    # command -100 kW
    bess.apply_commands({"on_grid_power_setpoint": -100})
    bess.update(dt=0.1)
    assert bess.commanded_power_kw == 10  # ramp 10kW back toward 0 (20 → 10)

    # command execute another iteration and commanded power goes to 0
    bess.update(dt=0.1)
    assert bess.commanded_power_kw == 0   # ramp 10 → 0

    # continue ramping until we reach -20 (which is limit we should reach in 2 iterations)
    for _ in range(2):
        bess.update(dt=0.1)
    assert bess.commanded_power_kw == -20  # ramp 0 → -10



def test_command_fault_reset():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    bess.fault_code = 7
    bess.apply_commands({"fault_reset": 1})
    assert bess.fault_code == 0


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

def test_start_stop_behavior():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)

    # Ensure clean state
    assert bess.mode == "idle"
    assert bess.commanded_power_kw == 0

    # START command
    bess.apply_commands({"start_stop_standby": 1})
    assert bess.mode == "discharge"

    # STOP command
    bess.apply_commands({"start_stop_standby": 2})
    assert bess.mode == "idle"
    assert bess.commanded_power_kw == 0

def test_stop_prevents_on_grid_power_override():
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)

    # First write initial power
    bess.apply_commands({"on_grid_power_setpoint": 50})
    bess.update(dt=0.1)
    first_power = bess.commanded_power_kw
    assert first_power != 0

    # STOP the BESS
    bess.apply_commands({"start_stop_standby": 2})
    bess.update(dt=0.1)
    assert bess.mode == "idle"
    assert bess.commanded_power_kw == 0

    # Write instructions dict again, but without changing on_grid_power_setpoint.
    # Should NOT reapply the 50 kW command.
    bess.apply_commands({
        "on_grid_power_setpoint": 50,  # unchanged!
        "start_stop_standby": 2
    })

    # Still zero — STOP takes priority
    assert bess.commanded_power_kw == 0

def test_service_current_uses_commanded_power():
    bess = BESSSimulator()
    bess.commanded_power_kw = 10     # discharge 10 kW
    bess.soc = 50                    # deterministic SOC so voltage predictable

    V = bess.battery_voltage()
    expected_I = 10000 / V

    current = bess.service_current()
    assert abs(current - expected_I) < 1e-6


def test_apply_commanded_power_ordering_and_limits():
    dt = 0.1
    bess = BESSSimulator(ramp_rate_kw_per_s=5.0)
    bess.max_discharge_kw = 20
    bess.max_charge_kw = 20

    # Ramp test
    bess.on_grid_power_kw = 200
    bess.apply_commanded_power(dt=0.1)
    assert abs(bess.commanded_power_kw - 0.5) < 1e-6

    # --- DERATING TEST (robust to configured limits) ---
    # pick SOC inside derating band (strictly between limit2 and limit1)
    low = min(bess.soc_lower_limit_1, bess.soc_lower_limit_2)
    high = max(bess.soc_lower_limit_1, bess.soc_lower_limit_2)
    mid_soc = (low + high) / 2.0

    # ensure we picked the correct mid inside the soft band
    assert low < mid_soc < high

    bess.soc = mid_soc
    bess.on_grid_power_kw = 20
    bess.commanded_power_kw = 0
    bess.apply_commanded_power(dt=0.1)

    # ramp produced 0.5 kW (ramp_rate 5 kW/s * dt 0.1)
    ramp_step = bess.ramp_rate_kw_per_s * dt
    initial_ramp = ramp_step if ramp_step < bess.max_discharge_kw else bess.max_discharge_kw

    # expected derate factor across the band: (soc - limit2) / (limit1 - limit2)
    limit2 = min(bess.soc_lower_limit_1, bess.soc_lower_limit_2)
    limit1 = max(bess.soc_lower_limit_1, bess.soc_lower_limit_2)
    factor = (bess.soc - limit2) / (limit1 - limit2)
    factor = max(0.0, min(1.0, factor))

    expected = initial_ramp * factor
    assert abs(bess.commanded_power_kw - expected) < 1e-6

    # --- HARD STOP TEST ---
    # put SOC below the lower hard limit and ensure discharge is 0
    bess.soc = limit2 - 1.0
    bess.commanded_power_kw = 0
    bess.on_grid_power_kw = 20
    bess.apply_commanded_power(dt=0.1)
    assert bess.commanded_power_kw == 0
