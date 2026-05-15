import asyncio


# Magic value for MWM TEM Evolution remote acknowledgment
CHP_ACK_MAGIC = 0x10E1


class BESSUnit:
    """Manages a single BESS unit — connection, enable, and power dispatch."""

    SOC_LOW  = 40.0
    SOC_HIGH = 60.0

    def __init__(self, client, name: str, charge_kw: float, discharge_kw: float):
        self.client = client
        self.name = name
        self.charge_kw = charge_kw
        self.discharge_kw = discharge_kw
        self.mode = None          # set after reading initial SOC
        self._starting = False

    async def connect(self) -> bool:
        print(f"[{self.name}] Connecting...")
        for attempt in range(10):
            if await self.client.connect():
                print(f"[{self.name}] Connected")
                return True
            print(f"[{self.name}] Attempt {attempt + 1} failed — retrying in 2s")
            await asyncio.sleep(2)
        print(f"[{self.name}] Could not connect after 10 attempts")
        return False

    async def enable(self):
        if self._starting:
            return
        self._starting = True
        print(f"[{self.name}] Enabling")
        await self.client.write_by_name("start_stop_standby", 1)
        await asyncio.sleep(1)

    async def read_initial_mode(self) -> str:
        soc = await self.client.read_by_name("system_soc")
        if soc is None or soc <= self.SOC_LOW:
            return "charge"
        if soc >= self.SOC_HIGH:
            return "discharge"
        return "charge"

    async def read_telemetry(self) -> dict | None:
        try:
            return {
                "soc":    await self.client.read_by_name("system_soc"),
                "power":  await self.client.read_by_name("active_power"),
                "status": await self.client.read_by_name("working_status"),
            }
        except Exception as e:
            print(f"[{self.name}] Telemetry error: {e}")
            return None

    async def step(self, t: dict):
        """Run one control cycle given telemetry dict."""
        soc    = t["soc"]
        status = t["status"]

        if status != 1:
            await self.enable()
            return
        self._starting = False

        if self.mode == "charge":
            await self.client.write_by_name("on_grid_power_setpoint", -self.charge_kw)
            if soc >= self.SOC_HIGH:
                print(f"[{self.name}] SOC {soc:.1f}% >= {self.SOC_HIGH}% -> DISCHARGE")
                self.mode = "discharge"

        elif self.mode == "discharge":
            await self.client.write_by_name("on_grid_power_setpoint", self.discharge_kw)
            if soc <= self.SOC_LOW:
                print(f"[{self.name}] SOC {soc:.1f}% <= {self.SOC_LOW}% -> CHARGE")
                self.mode = "charge"


class CHPUnit:
    """
    Manages a single CHP unit — connection, startup sequence, power dispatch,
    and fault recovery.

    State machine (matches MWM TEM Evolution register 30279):
        0 = Fault, 1 = Ready, 2 = Starting, 3 = Idle,
        4 = Synchronizing, 5 = Running, 6 = Stopping
    """

    STATE_FAULT   = 0
    STATE_READY   = 1
    STATE_RUNNING = 5

    def __init__(
        self,
        client,
        name: str,
        rated_kw: float = 4000.0,
        dispatch_setpoint_percent: float = 60.0,
    ):
        self.client = client
        self.name = name
        self.rated_kw = rated_kw
        self.dispatch_setpoint_percent = dispatch_setpoint_percent
        self._setpoint_applied = False
        self._start_requested = False

    async def connect(self) -> bool:
        print(f"[{self.name}] Connecting...")
        for attempt in range(10):
            if await self.client.connect():
                print(f"[{self.name}] Connected (rated {self.rated_kw:.0f} kW)")
                return True
            print(f"[{self.name}] Attempt {attempt + 1} failed — retrying in 2s")
            await asyncio.sleep(2)
        print(f"[{self.name}] Could not connect after 10 attempts")
        return False

    async def enable(self):
        """Send start_stop=1 to begin the startup sequence."""
        if self._start_requested:
            return
        self._start_requested = True
        print(f"[{self.name}] Requesting start")
        await self.client.write_by_name("start_stop", 1)
        await asyncio.sleep(1)

    async def acknowledge_fault(self):
        """Write the magic acknowledgment value to clear faults."""
        print(f"[{self.name}] Acknowledging fault")
        await self.client.write_by_name("remote_acknowledgment", CHP_ACK_MAGIC)
        # Reset start request so we can re-enable after ack
        self._start_requested = False
        await asyncio.sleep(1)

    async def read_telemetry(self) -> dict | None:
        try:
            return {
                "state":       await self.client.read_by_name("unit_state"),
                "power_kw":    await self.client.read_by_name("actual_power_kw"),
                "power_pct":   await self.client.read_by_name("actual_power_percent"),
                "heat_kw":     await self.client.read_by_name("heat_power_kw"),
                "rpm":         await self.client.read_by_name("engine_speed_rpm"),
                "coolant_c":   await self.client.read_by_name("coolant_outlet_temp"),
            }
        except Exception as e:
            print(f"[{self.name}] Telemetry error: {e}")
            return None

    async def step(self, t: dict):
        """Run one control cycle given telemetry dict."""
        state = t["state"]

        if state == self.STATE_FAULT:
            await self.acknowledge_fault()
            return

        if state != self.STATE_RUNNING:
            # Still starting up — request start if not already
            await self.enable()
            return

        # Running — apply dispatch setpoint once
        if not self._setpoint_applied:
            kw = self.dispatch_setpoint_percent / 100 * self.rated_kw
            print(
                f"[{self.name}] Running -- dispatching at "
                f"{self.dispatch_setpoint_percent:.0f}% ({kw:.0f} kW)"
            )
            await self.client.write_by_name(
                "power_setpoint",
                self.dispatch_setpoint_percent,
            )
            self._setpoint_applied = True


class FullSiteEMS:
    """
    Full site EMS demo -- dual BESS, PV inverter, CHP, energy meter.

    Each BESS unit cycles independently between SOC_LOW and SOC_HIGH.
    CHP starts up and dispatches at a fixed setpoint.
    PV and meter telemetry are read and displayed for observability
    but do not drive control decisions in this demo.
    """

    def __init__(
        self,
        bess_units: list[BESSUnit],
        chp_units: list[CHPUnit],
        meter_client,
        pv_client,
        poll_interval: float = 2.0,
    ):
        self.bess_units = bess_units
        self.chp_units = chp_units
        self.meter_client = meter_client
        self.pv_client = pv_client
        self.poll_interval = poll_interval

    async def _connect_aux(self) -> bool:
        for attempt in range(10):
            m = await self.meter_client.connect()
            p = await self.pv_client.connect()
            if m and p:
                print("[EMS] Meter and PV connected")
                return True
            print(f"[EMS] Meter/PV attempt {attempt + 1} failed -- retrying in 2s")
            await asyncio.sleep(2)
        print("[EMS] Could not connect to meter or PV")
        return False

    async def _read_site(self) -> dict:
        try:
            return {
                "grid_kw": await self.meter_client.read_by_name("total_active_power"),
                "pv_kw":   await self.pv_client.read_by_name("total_active_power"),
                "freq":    await self.meter_client.read_by_name("grid_frequency"),
            }
        except Exception as e:
            print(f"[EMS] Site read error: {e}")
            return {}

    async def run(self):
        # Connect everything concurrently
        results = await asyncio.gather(
            *[unit.connect() for unit in self.bess_units],
            *[unit.connect() for unit in self.chp_units],
            self._connect_aux(),
        )
        if not all(results):
            print("[EMS] Not all devices connected -- aborting")
            return

        # Read initial SOC and set starting mode for each BESS unit
        for unit in self.bess_units:
            unit.mode = await unit.read_initial_mode()
            print(f"[{unit.name}] Starting in {unit.mode.upper()} mode")

        # Enable all BESS units and request CHP startup
        await asyncio.gather(
            *[unit.enable() for unit in self.bess_units],
            *[unit.enable() for unit in self.chp_units],
        )
        await asyncio.sleep(2)

        print("\n[EMS] Full site EMS running")
        print(f"[EMS] Each BESS cycles between {BESSUnit.SOC_LOW}% and {BESSUnit.SOC_HIGH}% SOC")
        if self.chp_units:
            print(f"[EMS] {len(self.chp_units)} CHP unit(s) starting up (state machine takes ~2 min)")
        print()

        while True:
            try:
                # Read all telemetry concurrently
                bess_telemetries = await asyncio.gather(
                    *[unit.read_telemetry() for unit in self.bess_units]
                )
                chp_telemetries = await asyncio.gather(
                    *[unit.read_telemetry() for unit in self.chp_units]
                )
                site = await self._read_site()

                # Print site summary
                if site:
                    pv_status = "producing" if site["pv_kw"] > 0.1 else "idle"
                    print(
                        f"[SITE] Grid={site['grid_kw']:+8.2f} kW | "
                        f"PV={site['pv_kw']:6.2f} kW ({pv_status}) | "
                        f"Freq={site['freq']:.3f} Hz"
                    )

                for unit, t in zip(self.bess_units, bess_telemetries):
                    if t:
                        status_str = "RUN " if t["status"] == 1 else "IDLE"
                        print(
                            f"  [{unit.name}] {status_str} | "
                            f"SOC={t['soc']:5.1f}% | "
                            f"P={t['power']:+8.2f} kW | "
                            f"MODE={unit.mode}"
                        )
                        await unit.step(t)

                for unit, t in zip(self.chp_units, chp_telemetries):
                    if t:
                        state_names = {
                            0: "FAULT", 1: "READY", 2: "STARTING", 3: "IDLE",
                            4: "SYNC", 5: "RUNNING", 6: "STOPPING",
                        }
                        state_str = state_names.get(int(t["state"]), f"S{int(t['state'])}")
                        print(
                            f"  [{unit.name}] {state_str:8s} | "
                            f"P={t['power_kw']:8.1f} kW ({t['power_pct']:5.1f}%) | "
                            f"Heat={t['heat_kw']:8.1f} kW | "
                            f"RPM={t['rpm']:6.0f} | "
                            f"Coolant={t['coolant_c']:5.1f}C"
                        )
                        await unit.step(t)

            except Exception as e:
                print(f"[EMS] Error: {e}")

            await asyncio.sleep(self.poll_interval)