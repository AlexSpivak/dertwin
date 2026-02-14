import pytest

from dertwin.devices.bess import BESSSimulator
from dertwin.protocol.modbus import (
    ModbusSimulator,
    write_command_registers,
    collect_write_instructions
)

TEST_CONFIG = [
    {"address": 10055, "name": "start_stop_standby", "func": 0x06, "type": "uint16"},
    {"address": 10126, "name": "on_grid_power", "func": 0x10, "type": "int32", "scale": 0.1, "count": 2},
    {"address": 10325, "name": "soc_upper_limit_2", "func": 0x10, "type": "uint16", "scale": 0.1},
    {"address": 10330, "name": "soc_lower_limit_1", "func": 0x10, "type": "uint16", "scale": 0.1},
]


@pytest.mark.asyncio
async def test_bess_modbus_write_flow():
    bess = BESSSimulator(interval=0.1, deterministic=True, ramp_rate_kw_per_s=5.0)
    modbus_sim = ModbusSimulator(
        port=5021,
        unit_id=1,
        configs=TEST_CONFIG,
        device_sim=bess
    )

    # Initial state
    bess.simulate_values()
    assert bess.mode == "idle"
    assert bess.commanded_power_kw == 0

    # Ensure empty instructions read as 0
    instructions = collect_write_instructions(TEST_CONFIG, modbus_sim.context, unit_id=1)
    if instructions:
        applied = bess.execute_write_instructions(instructions)
        assert applied["start_stop_standby"] == 0

    # --- Start ---
    write_command_registers(TEST_CONFIG, modbus_sim.context, 1, {"start_stop_standby": 1})
    instructions = collect_write_instructions(TEST_CONFIG, modbus_sim.context, 1)
    if instructions:
        applied = bess.execute_write_instructions(instructions)
        bess.simulate_values()
        assert applied["start_stop_standby"] == 1
        assert bess.mode == "discharge"
        assert bess.commanded_power_kw == 0

    # --- Power command ---
    write_command_registers(TEST_CONFIG, modbus_sim.context, 1, {"on_grid_power": 50.0})
    instructions = collect_write_instructions(TEST_CONFIG, modbus_sim.context, 1)
    if instructions:
        bess.execute_write_instructions(instructions)

        # setting max discharged kw to the same level as sent on grid power.
        # This way we can avoid being capped by this level
        bess.max_discharge_kw = 50.0
        # simulate 100 iterations just to get ramp up to the on grid power level (50.0 kW)
        for _ in range(100):
            bess.simulate_values()
        assert bess.commanded_power_kw == 50.0

    # --- Stop command ---
    write_command_registers(TEST_CONFIG, modbus_sim.context, 1, {"start_stop_standby": 2})
    instructions = collect_write_instructions(TEST_CONFIG, modbus_sim.context, 1)
    if instructions:
        bess.execute_write_instructions(instructions)
        # simulate 100 iteration just to get ramp down to the 0 from 50.0 commanded power set before
        for _ in range(100):
            bess.simulate_values()

        assert bess.mode == "idle"
        assert bess.commanded_power_kw == 0

def test_initial_state_reasonable():
    bess = BESSSimulator(interval=0.1, deterministic=True, ramp_rate_kw_per_s=5.0)
    assert 40 <= bess.soc <= 60
    assert bess.mode == "idle"
    assert bess.commanded_power_kw == 0
    assert bess.max_charge_kw == 20
    assert bess.max_discharge_kw == 20

def test_apply_commanded_power_respects_limits():
    bess = BESSSimulator(interval=0.1, deterministic=True, ramp_rate_kw_per_s=5.0)

    bess.set_on_grid_power_kw(50)
    bess.apply_commanded_power()  # request huge discharge
    assert bess.commanded_power_kw <= bess.max_discharge_kw

    bess.set_on_grid_power_kw(-50)
    bess.apply_commanded_power()  # request huge charge
    assert bess.commanded_power_kw >= -bess.max_charge_kw

def test_apply_commanded_power_ramp_limit():
    bess = BESSSimulator(interval=0.1, deterministic=True, ramp_rate_kw_per_s=5.0)
    bess.commanded_power_kw = 0

    bess.set_on_grid_power_kw(100)
    bess.apply_commanded_power()
    assert bess.commanded_power_kw == 0.5   # 5 kW/s * 0.1s step = 0.5 kW

def test_soc_updates_correctly():
    bess = BESSSimulator(interval=0.1, deterministic=True, ramp_rate_kw_per_s=5.0)
    bess.soc = 50
    bess.set_on_grid_power_kw(10)
    prev_soc = bess.soc
    result = bess.simulate_values()
    assert result["system_soc"] < prev_soc # discharged SoC a little on simulation

def test_realistic_charge_discharge_cycle():
    bess = BESSSimulator(interval=0.1, deterministic=True, ramp_rate_kw_per_s=5.0)
    bess.soc = 50

    # Phase 1: discharge 20 kW for 30 simulated minutes → SOC must drop
    bess.execute_write_instructions({"on_grid_power": 20})
    for _ in range(int(30 * 60 // bess.time_step_sec)):
        bess.simulate_values()
    mid_soc = bess.soc

    # since we were discharging 100kW (default capacity) BESS to 20 kWh for 30 minutes
    # we should loose approx. 10% of SoC. With some loose on efficiency the SoC must be
    # in range between 39% and 41%
    assert 39 < mid_soc < 41

    # Phase 2: charge 20 kW for 30 minutes → SOC must recover
    bess.execute_write_instructions({"on_grid_power": -20})
    for _ in range(int(30 * 60 // bess.time_step_sec)):
        bess.simulate_values()
    final_soc = bess.soc

    # we charged here so final SoC is higher then mid SoC (after discharging)
    assert final_soc > mid_soc

    # since we were charging last 30 minutes for 20 kW and the initial default capacity 100 kW
    # the battery must gain approx 10% of SoC. So the SoC range should be between 49% and 51%
    assert 49 < final_soc < 51

def test_command_start_stop():
    bess = BESSSimulator(interval=0.1, deterministic=True, ramp_rate_kw_per_s=5.0)

    bess.execute_write_instructions({"start_stop_standby": 1})
    assert bess.mode == "discharge"

    bess.execute_write_instructions({"start_stop_standby": 2})
    assert bess.mode == "idle"
    assert bess.commanded_power_kw == 0

    bess.execute_write_instructions({"start_stop_standby": 3})
    assert bess.mode == "standby"
    assert bess.commanded_power_kw == 0

def test_command_local_remote():
    bess = BESSSimulator(interval=0.1, deterministic=True, ramp_rate_kw_per_s=5.0)
    bess.execute_write_instructions({"local_remote_settings": 2})
    assert bess.local_remote_mode == 2


def test_command_power_control_mode():
    bess = BESSSimulator(interval=0.1, deterministic=True, ramp_rate_kw_per_s=5.0)
    bess.execute_write_instructions({"power_control_mode": 1})
    assert bess.power_control_mode == 1

def test_command_on_grid_power_respects_limits():
    bess = BESSSimulator(interval=0.1, deterministic=True, ramp_rate_kw_per_s=100.0)

    # send +100 kW command
    bess.execute_write_instructions({"on_grid_power": 100})
    bess.simulate_values()
    assert bess.commanded_power_kw == 10  # with ramp 100 from 0 to +10 in 0.1 sec

    bess.simulate_values()
    assert bess.commanded_power_kw == 20  # another 0.1 sec and we hit to +20 (max)

    # -----------------------
    # Now test charging direction
    # -----------------------

    # command -100 kW
    bess.execute_write_instructions({"on_grid_power": -100})
    bess.simulate_values()
    assert bess.commanded_power_kw == 10  # ramp 10kW back toward 0 (20 → 10)

    # command execute another iteration and commanded power goes to 0
    bess.simulate_values()
    assert bess.commanded_power_kw == 0   # ramp 10 → 0

    # continue ramping until we reach -20 (which is limit we should reach in 2 iterations)
    for _ in range(2):
        bess.simulate_values()
    assert bess.commanded_power_kw == -20  # ramp 0 → -10



def test_command_fault_reset():
    bess = BESSSimulator(interval=0.1, deterministic=True, ramp_rate_kw_per_s=5.0)
    bess.fault_code = 7
    bess.execute_write_instructions({"fault_reset": 1})
    assert bess.fault_code == 0


def test_soc_limit_writes():
    bess = BESSSimulator(interval=0.1, deterministic=True, ramp_rate_kw_per_s=5.0)

    bess.execute_write_instructions({"soc_upper_limit_1": 90})
    assert bess.soc_upper_limit_1 == 90

    bess.execute_write_instructions({"soc_upper_limit_2": 95})
    assert bess.soc_upper_limit_2 == 95

    bess.execute_write_instructions({"soc_lower_limit_1": 30})
    assert bess.soc_lower_limit_1 == 30

    bess.execute_write_instructions({"soc_lower_limit_2": 15})
    assert bess.soc_lower_limit_2 == 15

def test_start_stop_behavior():
    bess = BESSSimulator(interval=0.1, deterministic=True, ramp_rate_kw_per_s=5.0)

    # Ensure clean state
    assert bess.mode == "idle"
    assert bess.commanded_power_kw == 0

    # START command
    bess.execute_write_instructions({"start_stop_standby": 1})
    assert bess.mode == "discharge"

    # STOP command
    bess.execute_write_instructions({"start_stop_standby": 2})
    assert bess.mode == "idle"
    assert bess.commanded_power_kw == 0

def test_stop_prevents_on_grid_power_override():
    bess = BESSSimulator(interval=0.1, deterministic=True, ramp_rate_kw_per_s=5.0)

    # First write initial power
    bess.execute_write_instructions({"on_grid_power": 50})
    bess.simulate_values()
    first_power = bess.commanded_power_kw
    assert first_power != 0

    # STOP the BESS
    bess.execute_write_instructions({"start_stop_standby": 2})
    bess.simulate_values()
    assert bess.mode == "idle"
    assert bess.commanded_power_kw == 0

    # Write instructions dict again, but without changing on_grid_power.
    # Should NOT reapply the 50 kW command.
    bess.execute_write_instructions({
        "on_grid_power": 50,  # unchanged!
        "start_stop_standby": 2
    })

    # Still zero — STOP takes priority
    assert bess.commanded_power_kw == 0

def test_service_current_uses_commanded_power():
    bess = BESSSimulator(interval=0.1, deterministic=True)
    bess.commanded_power_kw = 10     # discharge 10 kW
    bess.soc = 50                    # deterministic SOC so voltage predictable

    V = bess.battery_voltage()
    expected_I = 10000 / V

    current = bess.service_current()
    assert abs(current - expected_I) < 1e-6


def test_apply_commanded_power_ordering_and_limits():
    bess = BESSSimulator(interval=0.1, deterministic=True, ramp_rate_kw_per_s=5.0)
    bess.max_discharge_kw = 20
    bess.max_charge_kw = 20

    # Ramp test
    bess.on_grid_power_kw = 200
    bess.apply_commanded_power()
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
    bess.apply_commanded_power()

    # ramp produced 0.5 kW (ramp_rate 5 kW/s * dt 0.1)
    ramp_step = bess.ramp_rate_kw_per_s * bess.time_step_sec
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
    bess.apply_commanded_power()
    assert bess.commanded_power_kw == 0


@pytest.mark.asyncio
async def test_modbus_write_only_on_change():
    """
    Ensures the ModbusSimulator update loop only re-applies writes when
    the write-instruction snapshot *changes*, preventing HR zeros from
    overwriting commands.
    """
    bess = BESSSimulator(interval=0.1, deterministic=True)
    modbus = ModbusSimulator(port=5021, unit_id=1, configs=TEST_CONFIG, device_sim=bess)

    # write a value to Modbus and apply it to the BESS
    write_command_registers(TEST_CONFIG, modbus.context, 1, {"on_grid_power": 10})
    instr1 = collect_write_instructions(TEST_CONFIG, modbus.context, 1)
    bess.execute_write_instructions(instr1)

    assert bess.on_grid_power_kw == 10

    # snapshot
    prev = dict(instr1)

    # collect again WITHOUT changes
    instr2 = collect_write_instructions(TEST_CONFIG, modbus.context, 1)
    assert instr2 == prev

    # apply again — this should be idempotent
    bess.execute_write_instructions(instr2)

    # Should NOT overwrite the value
    assert bess.on_grid_power_kw == 10

    # now change a register
    write_command_registers(TEST_CONFIG, modbus.context, 1, {"on_grid_power": 20})
    instr3 = collect_write_instructions(TEST_CONFIG, modbus.context, 1)
    assert instr3 != prev

    bess.execute_write_instructions(instr3)
    assert bess.on_grid_power_kw == 20
