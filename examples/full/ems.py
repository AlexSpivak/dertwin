import asyncio


class BESSUnit:
    """Manages a single BESS unit — connection, enable, and power dispatch."""

    def __init__(self, client, name: str, max_charge_kw: float, max_discharge_kw: float):
        self.client = client
        self.name = name
        self.max_charge_kw = max_charge_kw
        self.max_discharge_kw = max_discharge_kw
        self._starting = False

    async def connect(self) -> bool:
        print(f"[{self.name}] Connecting...")
        for attempt in range(10):
            connected = await self.client.connect()
            if connected:
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

    async def set_power(self, kw: float):
        """Positive = discharge, negative = charge. Clamped to unit limits."""
        kw = max(-self.max_charge_kw, min(self.max_discharge_kw, kw))
        await self.client.write_by_name("on_grid_power_setpoint", kw)

    async def read_telemetry(self) -> dict | None:
        try:
            soc    = await self.client.read_by_name("system_soc")
            power  = await self.client.read_by_name("active_power")
            status = await self.client.read_by_name("working_status")
            return {"soc": soc, "power": power, "status": status}
        except Exception as e:
            print(f"[{self.name}] Telemetry error: {e}")
            return None


class FullSiteEMS:
    """
    Demo EMS for a site with dual BESS, PV inverter, and energy meter.

    Strategy:
      - Read net grid power from the energy meter
      - Charge BESS when PV produces excess (grid export)
      - Discharge BESS to cover load when grid import is high
      - Respect per-unit SOC limits (20-80%)
      - Split power proportionally to each unit's available headroom
    """

    SOC_MIN = 20.0
    SOC_MAX = 80.0
    EXPORT_THRESHOLD_KW = -2.0   # grid power below this → charge BESS
    IMPORT_THRESHOLD_KW =  5.0   # grid power above this → discharge BESS

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
            grid_power = await self.meter_client.read_by_name("total_active_power")
            pv_power   = await self.pv_client.read_by_name("total_active_power")
            freq       = await self.meter_client.read_by_name("grid_frequency")
            return {"grid_power_kw": grid_power, "pv_power_kw": pv_power, "freq_hz": freq}
        except Exception as e:
            print(f"[EMS] Site read error: {e}")
            return {}

    def _split_power(self, total_kw: float, telemetries: list[dict]) -> list[float]:
        """Split total_kw across units proportional to available SOC headroom."""
        if total_kw == 0:
            return [0.0] * len(self.units)

        headrooms = []
        for t in telemetries:
            soc = t["soc"] if t else 50.0
            h = max(0.0, self.SOC_MAX - soc) if total_kw < 0 else max(0.0, soc - self.SOC_MIN)
            headrooms.append(h)

        total_headroom = sum(headrooms)
        if total_headroom == 0:
            return [0.0] * len(self.units)

        return [total_kw * (h / total_headroom) for h in headrooms]

    def _decide_power(self, grid_power_kw: float) -> float:
        """Return total kW to dispatch (positive = discharge, negative = charge)."""
        if grid_power_kw < self.EXPORT_THRESHOLD_KW:
            total_charge = sum(u.max_charge_kw for u in self.units)
            return -min(total_charge, abs(grid_power_kw))
        if grid_power_kw > self.IMPORT_THRESHOLD_KW:
            total_discharge = sum(u.max_discharge_kw for u in self.units)
            return min(total_discharge, grid_power_kw)
        return 0.0

    async def run(self):
        # Connect all devices concurrently
        results = await asyncio.gather(
            *[unit.connect() for unit in self.units],
            self._connect_aux(),
        )
        if not all(results):
            print("[EMS] Not all devices connected — aborting")
            return

        # Enable all BESS units
        await asyncio.gather(*[unit.enable() for unit in self.units])
        await asyncio.sleep(2)

        print("\n[EMS] Full site EMS running")
        print(f"[EMS] SOC window: {self.SOC_MIN}% – {self.SOC_MAX}%")
        print(f"[EMS] Charge when grid < {self.EXPORT_THRESHOLD_KW} kW | "
              f"Discharge when grid > {self.IMPORT_THRESHOLD_KW} kW\n")

        while True:
            try:
                telemetries = await asyncio.gather(
                    *[unit.read_telemetry() for unit in self.units]
                )
                site = await self._read_site()

                if not site:
                    await asyncio.sleep(self.poll_interval)
                    continue

                grid_kw = site["grid_power_kw"]
                pv_kw   = site["pv_power_kw"]
                freq    = site["freq_hz"]

                print(
                    f"[SITE] Grid={grid_kw:+7.2f} kW | "
                    f"PV={pv_kw:6.2f} kW | "
                    f"Freq={freq:.3f} Hz"
                )
                for unit, t in zip(self.units, telemetries):
                    if t:
                        status_str = "RUN " if t["status"] == 1 else "IDLE"
                        print(
                            f"  [{unit.name}] {status_str} | "
                            f"SOC={t['soc']:5.1f}% | "
                            f"P={t['power']:+7.2f} kW"
                        )

                # Re-enable any unit that dropped out of run mode
                for unit, t in zip(self.units, telemetries):
                    if t and t["status"] != 1:
                        await unit.enable()
                    elif t:
                        unit._starting = False

                # Dispatch
                total_kw = self._decide_power(grid_kw)
                splits   = self._split_power(total_kw, telemetries)

                if abs(total_kw) > 0.01:
                    action = "CHARGING" if total_kw < 0 else "DISCHARGING"
                    split_str = " | ".join(
                        f"{u.name}={kw:+.1f} kW"
                        for u, kw in zip(self.units, splits)
                    )
                    print(f"  [EMS] {action} {total_kw:+.1f} kW total → {split_str}")
                else:
                    print(f"  [EMS] HOLD — grid within comfortable band")

                await asyncio.gather(*[
                    unit.set_power(kw)
                    for unit, kw in zip(self.units, splits)
                ])

            except Exception as e:
                print(f"[EMS] Error: {e}")

            await asyncio.sleep(self.poll_interval)