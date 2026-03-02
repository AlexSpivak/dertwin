import unittest

from dertwin.devices.external.power_flow import SitePowerModel


class TestSitePowerModel(unittest.TestCase):

    # --------------------------------------------------
    # Helpers
    # --------------------------------------------------

    def constant_load(self, value_kw):
        return lambda t: value_kw

    def constant_power_w(self, value_w):
        return lambda: value_w

    # --------------------------------------------------
    # Pure load
    # --------------------------------------------------

    def test_import_only(self):
        model = SitePowerModel(
            base_load_supplier=self.constant_load(10.0),  # 10 kW
        )

        model.update(3600.0)  # 1 hour

        self.assertAlmostEqual(model.grid_power_kw, 10.0)
        self.assertAlmostEqual(model.import_energy_kwh, 10.0)
        self.assertAlmostEqual(model.export_energy_kwh, 0.0)

    # --------------------------------------------------
    # Pure PV export
    # --------------------------------------------------

    def test_export_only(self):
        model = SitePowerModel(
            base_load_supplier=self.constant_load(0.0),
            pv_supplier=self.constant_power_w(5000.0),  # 5 kW
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
            pv_supplier=self.constant_power_w(4000.0),  # 4 kW
        )

        model.update(3600.0)

        self.assertAlmostEqual(model.grid_power_kw, 6.0)
        self.assertAlmostEqual(model.import_energy_kwh, 6.0)

    # --------------------------------------------------
    # BESS discharge (export)
    # --------------------------------------------------

    def test_bess_discharge(self):
        model = SitePowerModel(
            base_load_supplier=self.constant_load(2.0),
            bess_supplier=self.constant_power_w(5000.0),  # 5 kW discharge
        )

        model.update(3600.0)

        self.assertAlmostEqual(model.grid_power_kw, -3.0)
        self.assertAlmostEqual(model.export_energy_kwh, 3.0)

    # --------------------------------------------------
    # BESS charge (import)
    # --------------------------------------------------

    def test_bess_charge(self):
        model = SitePowerModel(
            base_load_supplier=self.constant_load(2.0),
            bess_supplier=self.constant_power_w(-3000.0),  # -3 kW (charging)
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
            pv_supplier=self.constant_power_w(5000.0),
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

        # 30 min
        model.update(1800.0)
        # another 30 min
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


if __name__ == "__main__":
    unittest.main()