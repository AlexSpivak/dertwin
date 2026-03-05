import asyncio


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
                print(f"[{self.name}] SOC {soc:.1f}% ≥ {self.SOC_HIGH}% → DISCHARGE")
                self.mode = "discharge"

        elif self.mode == "discharge":
            await self.client.write_by_name("on_grid_power_setpoint", self.discharge_kw)
            if soc <= self.SOC_LOW:
                print(f"[{self.name}] SOC {soc:.1f}% ≤ {self.SOC_LOW}% → CHARGE")
                self.mode = "charge"


class FullSiteEMS:
    """
    Full site EMS demo — dual BESS, PV inverter, energy meter.

    Each BESS unit cycles independently between SOC_LOW and SOC_HIGH.
    PV and meter telemetry are read and displayed for observability
    but do not drive control decisions in this demo.
    """

    def __init__(
        self,
        bess_units: list[BESSUnit],
        meter_client,
        pv_client,
        poll_interval: float = 2.0,
    ):
        self.units = bess_units
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
            print(f"[EMS] Meter/PV attempt {attempt + 1} failed — retrying in 2s")
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
            *[unit.connect() for unit in self.units],
            self._connect_aux(),
        )
        if not all(results):
            print("[EMS] Not all devices connected — aborting")
            return

        # Read initial SOC and set starting mode for each unit
        for unit in self.units:
            unit.mode = await unit.read_initial_mode()
            print(f"[{unit.name}] Starting in {unit.mode.upper()} mode")

        # Enable all BESS units
        await asyncio.gather(*[unit.enable() for unit in self.units])
        await asyncio.sleep(2)

        print("\n[EMS] Full site EMS running")
        print(f"[EMS] Each BESS cycles between {BESSUnit.SOC_LOW}% and {BESSUnit.SOC_HIGH}% SOC\n")

        while True:
            try:
                # Read all telemetry concurrently
                telemetries = await asyncio.gather(
                    *[unit.read_telemetry() for unit in self.units]
                )
                site = await self._read_site()

                # Print site summary
                if site:
                    pv_status = "producing" if site["pv_kw"] > 0.1 else "idle"
                    print(
                        f"[SITE] Grid={site['grid_kw']:+7.2f} kW | "
                        f"PV={site['pv_kw']:6.2f} kW ({pv_status}) | "
                        f"Freq={site['freq']:.3f} Hz"
                    )

                for unit, t in zip(self.units, telemetries):
                    if t:
                        status_str = "RUN " if t["status"] == 1 else "IDLE"
                        print(
                            f"  [{unit.name}] {status_str} | "
                            f"SOC={t['soc']:5.1f}% | "
                            f"P={t['power']:+7.2f} kW | "
                            f"MODE={unit.mode}"
                        )
                        await unit.step(t)

            except Exception as e:
                print(f"[EMS] Error: {e}")

            await asyncio.sleep(self.poll_interval)