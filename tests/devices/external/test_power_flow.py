import unittest

from dertwin.devices.external.power_flow import SitePowerModel


class TestSitePowerModel(unittest.TestCase):

    # --------------------------------------------------
    # Helpers
    # --------------------------------------------------

    def constant_load(self, value_kw):
        return lambda t: value_kw

    def constant_kw(self, value_kw):
        """All suppliers now return kW — SitePowerModel no longer divides internally."""
        return lambda: value_kw

    # --------------------------------------------------
    # Pure load
    # --------------------------------------------------

    def test_import_only(self):
        model = SitePowerModel(
            base_load_supplier=self.constant_load(10.0),
        )
        model.update(3600.0)
        self.assertAlmostEqual(model.grid_power_kw, 10.0)
        self.assertAlmostEqual(model.import_energy_kwh, 10.0)
        self.assertAlmostEqual(model.export_energy_kwh, 0.0)

    # --------------------------------------------------
    # Pure PV export
    # --------------------------------------------------

    def test_export_only(self):
        model = SitePowerModel(
            base_load_supplier=self.constant_load(0.0),
            pv_supplier=self.constant_kw(5.0),
        )
        model.update(3600.0)
        self.assertAlmostEqual(model.grid_power_kw, -5.0)
        self.assertAlmostEqual(model.import_energy_kwh, 0.0)
        self.assertAlmostEqual(model.export_energy_kwh, 5.0)

    # --------------------------------------------------
    # Partial offset
    # --------------------------------------------------

    def test_partial_offset(self):
        model = SitePowerModel(
            base_load_supplier=self.constant_load(10.0),
            pv_supplier=self.constant_kw(4.0),
        )
        model.update(3600.0)
        self.assertAlmostEqual(model.grid_power_kw, 6.0)
        self.assertAlmostEqual(model.import_energy_kwh, 6.0)

    # --------------------------------------------------
    # BESS discharge reduces import
    # --------------------------------------------------

    def test_bess_discharge(self):
        model = SitePowerModel(
            base_load_supplier=self.constant_load(2.0),
            bess_supplier=self.constant_kw(5.0),  # 5 kW discharge
        )
        model.update(3600.0)
        self.assertAlmostEqual(model.grid_power_kw, -3.0)
        self.assertAlmostEqual(model.export_energy_kwh, 3.0)

    # --------------------------------------------------
    # BESS charge increases import
    # --------------------------------------------------

    def test_bess_charge(self):
        model = SitePowerModel(
            base_load_supplier=self.constant_load(2.0),
            bess_supplier=self.constant_kw(-3.0),  # -3 kW charging
        )
        model.update(3600.0)
        self.assertAlmostEqual(model.grid_power_kw, 5.0)
        self.assertAlmostEqual(model.import_energy_kwh, 5.0)

    # --------------------------------------------------
    # Zero net power
    # --------------------------------------------------

    def test_zero_net(self):
        model = SitePowerModel(
            base_load_supplier=self.constant_load(5.0),
            pv_supplier=self.constant_kw(5.0),
        )
        model.update(3600.0)
        self.assertAlmostEqual(model.grid_power_kw, 0.0)
        self.assertAlmostEqual(model.import_energy_kwh, 0.0)
        self.assertAlmostEqual(model.export_energy_kwh, 0.0)

    # --------------------------------------------------
    # Multi-step integration
    # --------------------------------------------------

    def test_multiple_steps_energy_accumulation(self):
        model = SitePowerModel(
            base_load_supplier=self.constant_load(10.0),
        )
        model.update(1800.0)
        model.update(1800.0)
        self.assertAlmostEqual(model.import_energy_kwh, 10.0)

    # --------------------------------------------------
    # No suppliers edge case
    # --------------------------------------------------

    def test_no_pv_no_bess(self):
        model = SitePowerModel(
            base_load_supplier=self.constant_load(3.0),
        )
        model.update(3600.0)
        self.assertAlmostEqual(model.grid_power_kw, 3.0)
        self.assertAlmostEqual(model.import_energy_kwh, 3.0)

    # --------------------------------------------------
    # PV and BESS combined
    # --------------------------------------------------

    def test_pv_and_bess_combined_export(self):
        """PV + BESS discharge both offsetting load → net export."""
        model = SitePowerModel(
            base_load_supplier=self.constant_load(5.0),
            pv_supplier=self.constant_kw(8.0),
            bess_supplier=self.constant_kw(4.0),
        )
        model.update(3600.0)
        self.assertAlmostEqual(model.grid_power_kw, -7.0)
        self.assertAlmostEqual(model.export_energy_kwh, 7.0)
        self.assertAlmostEqual(model.import_energy_kwh, 0.0)

    def test_sim_time_advances(self):
        model = SitePowerModel(base_load_supplier=self.constant_load(1.0))
        model.update(60.0)
        model.update(60.0)
        self.assertAlmostEqual(model.get_sim_time(), 120.0)

    def test_export_energy_not_negative(self):
        """Export energy counter must never go negative."""
        model = SitePowerModel(base_load_supplier=self.constant_load(10.0))
        for _ in range(10):
            model.update(360.0)
        self.assertGreaterEqual(model.export_energy_kwh, 0.0)

    def test_import_energy_not_negative(self):
        """Import energy counter must never go negative."""
        model = SitePowerModel(
            base_load_supplier=self.constant_load(0.0),
            pv_supplier=self.constant_kw(10.0),
        )
        for _ in range(10):
            model.update(360.0)
        self.assertGreaterEqual(model.import_energy_kwh, 0.0)


if __name__ == "__main__":
    unittest.main()