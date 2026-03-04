import asyncio


class SimpleEMS:
    def __init__(self, client, poll_interval=2):
        self.client = client
        self.poll_interval = poll_interval
        self.mode = "charge"  # start direction

    async def enable_bess(self):
        print("[EMS] Enabling BESS")
        await self.client.write_by_name("start_stop_standby", 1)
        await asyncio.sleep(1)

    async def run(self):
        await self.client.connect()
        print("[EMS] Connected to BESS")

        while True:
            try:
                soc = await self.client.read_by_name("system_soc")
                power = await self.client.read_by_name("active_power")
                status = await self.client.read_by_name("working_status")

                print(
                    f"[EMS] STATUS={status} | "
                    f"SOC={soc:6.2f}% | "
                    f"P={power:7.2f} kW | "
                    f"MODE={self.mode}"
                )

                if status != 1:
                    await self.enable_bess()
                    await asyncio.sleep(self.poll_interval)
                    continue

                # ---- Oscillation logic ----
                if self.mode == "charge":
                    await self.client.write_by_name("on_grid_power_setpoint", -20.0)

                    if soc >= 60:
                        print("[EMS] Reached 60% → switching to DISCHARGE")
                        self.mode = "discharge"

                elif self.mode == "discharge":
                    await self.client.write_by_name("on_grid_power_setpoint", 20.0)

                    if soc <= 40:
                        print("[EMS] Reached 40% → switching to CHARGE")
                        self.mode = "charge"

            except Exception as e:
                print(f"[EMS] Error: {e}")

            await asyncio.sleep(self.poll_interval)