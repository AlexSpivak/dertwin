from typing import List

from dertwin.devices.bess import BESSSimulator
from dertwin.devices.device import DeviceSimulator
from dertwin.devices.energy_meter import EnergyMeterSimulator
from dertwin.devices.inverter import InverterSimulator


# -------------------------
# Site Controller (PV sells to grid; BESS independent)
# -------------------------
class SiteController:
    def __init__(self):
        self.inverters: List[InverterSimulator] = []
        self.besses: List[BESSSimulator] = []
        self.meters: List[EnergyMeterSimulator] = []

    # --------------------------------
    # Register devices
    # --------------------------------
    def register_device(self, device: DeviceSimulator):
        if isinstance(device, InverterSimulator):
            self.inverters.append(device)
        elif isinstance(device, BESSSimulator):
            self.besses.append(device)
        elif isinstance(device, EnergyMeterSimulator):
            self.meters.append(device)

    # --------------------------------
    # Sample PV output (W)
    # --------------------------------
    def sample_pv_w(self) -> float:
        return sum(inv.get_pv_watts() for inv in self.inverters)

    # --------------------------------
    # Sample BESS power command (kW)
    # --------------------------------
    def sample_bess_kw(self) -> float:
        return sum(b.commanded_power_kw for b in self.besses)
