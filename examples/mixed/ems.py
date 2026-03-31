import asyncio


class BESSUnit:
    """Manages a single BESS unit via Modbus TCP — connection, enable, and power dispatch."""

    SOC_LOW  = 40.0
    SOC_HIGH = 60.0

    def __init__(self, client, name: str, charge_kw: float, discharge_kw: float):
        self.client = client
        self.name = name
        self.charge_kw = charge_kw
        self.discharge_kw = discharge_kw
        self.mode = None
        self._starting = False

    async def connect(self) -> bool:
        print(f"[{self.name}] Connecting via TCP...")
        for attempt in range(10):
            if await self.client.connect():
                print(f"[{self.name}] TCP connected")
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


class RTUDevice:
    """Read-only device connected via Modbus RTU (serial)."""

    def __init__(self, client, name: str, telemetry_fields: list[str]):
        self.client = client
        self.name = name
        self.telemetry_fields = telemetry_fields
        self._connected = False

    async def connect(self) -> bool:
        print(f"[{self.name}] Connecting via RTU...")
        for attempt in range(10):
            try:
                if await self.client.connect():
                    print(f"[{self.name}] RTU connected")
                    self._connected = True
                    return True
            except Exception as e:
                print(f"[{self.name}] RTU attempt {attempt + 1} failed: {e}")
            await asyncio.sleep(2)
        print(f"[{self.name}] Could not connect after 10 attempts")
        return False

    async def read_telemetry(self) -> dict:
        result = {}
        for field in self.telemetry_fields:
            try:
                result[field] = await self.client.read_by_name(field)
            except Exception as e:
                print(f"[{self.name}] Read error ({field}): {e}")
                result[field] = None
        return result


class MixedProtocolEMS:
    """
    Mixed-protocol EMS demo.

    - BESS controlled via Modbus TCP (active charge/discharge cycling)
    - PV inverter monitored via Modbus RTU (read-only telemetry)
    - Energy meter monitored via Modbus RTU (read-only telemetry)

    The BESS cycles between SOC_LOW and SOC_HIGH. PV and meter
    telemetry are displayed for site observability.

    Prerequisites:
      1. Run the simulator:
         dertwin -c configs/mixed_protocol_config.json

      2. Create virtual serial pairs (for RTU):
         socat -d -d pty,raw,echo=0,link=/tmp/dertwin_pv pty,raw,echo=0,link=/tmp/dertwin_pv_client &
         socat -d -d pty,raw,echo=0,link=/tmp/dertwin_meter pty,raw,echo=0,link=/tmp/dertwin_meter_client &

      3. Run this EMS:
         cd examples && python main_mixed.py
    """

    def __init__(
        self,
        bess: BESSUnit,
        pv: RTUDevice,
        meter: RTUDevice,
        poll_interval: float = 2.0,
    ):
        self.bess = bess
        self.pv = pv
        self.meter = meter
        self.poll_interval = poll_interval

    async def run(self):
        # Connect all devices
        results = await asyncio.gather(
            self.bess.connect(),
            self.pv.connect(),
            self.meter.connect(),
        )

        # BESS is mandatory — PV and meter are best-effort
        if not results[0]:
            print("[EMS] BESS connection failed — aborting")
            return

        if not results[1]:
            print("[EMS] PV RTU connection failed — PV telemetry will be unavailable")
        if not results[2]:
            print("[EMS] Meter RTU connection failed — meter telemetry will be unavailable")

        # Initialize BESS mode
        self.bess.mode = await self.bess.read_initial_mode()
        print(f"[{self.bess.name}] Starting in {self.bess.mode.upper()} mode")

        await self.bess.enable()
        await asyncio.sleep(2)

        print("\n[EMS] Mixed-protocol EMS running")
        print(f"[EMS] BESS cycles between {BESSUnit.SOC_LOW}% and {BESSUnit.SOC_HIGH}% SOC")
        print(f"[EMS] PV and meter are read-only via RTU\n")

        while True:
            try:
                # Read BESS telemetry (TCP)
                bess_t = await self.bess.read_telemetry()
                if bess_t:
                    status_str = "RUN " if bess_t["status"] == 1 else "IDLE"
                    print(
                        f"  [{self.bess.name}] {status_str} | "
                        f"SOC={bess_t['soc']:5.1f}% | "
                        f"P={bess_t['power']:+7.2f} kW | "
                        f"MODE={self.bess.mode}"
                    )
                    await self.bess.step(bess_t)

                # Read PV telemetry (RTU)
                if self.pv._connected:
                    pv_t = await self.pv.read_telemetry()
                    pv_kw = pv_t.get("total_active_power", 0) or 0
                    pv_status = "producing" if pv_kw > 0.1 else "idle"
                    print(
                        f"  [PV]    P={pv_kw:6.2f} kW ({pv_status})"
                    )

                # Read meter telemetry (RTU)
                if self.meter._connected:
                    meter_t = await self.meter.read_telemetry()
                    grid_kw = meter_t.get("total_active_power", 0) or 0
                    freq = meter_t.get("grid_frequency", 0) or 0
                    import_kwh = meter_t.get("total_import_energy", 0) or 0
                    export_kwh = meter_t.get("total_export_energy", 0) or 0
                    direction = "importing" if grid_kw > 0 else "exporting"
                    print(
                        f"  [METER] Grid={grid_kw:+7.2f} kW ({direction}) | "
                        f"Freq={freq:.3f} Hz | "
                        f"Import={import_kwh:.1f} kWh | Export={export_kwh:.1f} kWh"
                    )

                print()

            except Exception as e:
                print(f"[EMS] Error: {e}")

            await asyncio.sleep(self.poll_interval)