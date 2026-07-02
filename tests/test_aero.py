"""Aerodynamic model tests."""

import unittest
from pathlib import Path

from wingopt.aero import AeroCondition, AeroModel
from wingopt.config import load_config
from wingopt.geometry import compute_planform, load_airfoil_library
from wingopt.utils.atmosphere import build_atmosphere
from wingopt.utils.units import g_to_kg, kmh_to_ms


class AeroTests(unittest.TestCase):
    def setUp(self) -> None:
        cfg = load_config(Path("configs/default_wing.toml"))
        geometry = compute_planform(cfg.geometry)
        airfoils = load_airfoil_library(Path("data/airfoils"), cfg.geometry.airfoil_candidates)
        self.model = AeroModel(geometry=geometry, airfoil=airfoils[cfg.geometry.airfoil])
        self.atm = build_atmosphere(temperature_c=20.0, altitude_m=0.0, relative_humidity=0.5)

    def test_evaluate_condition(self) -> None:
        result = self.model.evaluate(
            condition=AeroCondition(
                speed_ms=kmh_to_ms(60.0),
                alpha_deg=4.0,
                elevon_deflection_deg=0.0,
                cg_x_fraction_mac=0.22,
            ),
            atmosphere=self.atm,
        )
        self.assertGreater(result.cl, 0.0)
        self.assertGreater(result.cd, 0.0)
        self.assertGreater(result.ld, 1.0)

    def test_trim_for_level_flight(self) -> None:
        weight_n = g_to_kg(1000.0) * 9.80665
        trim = self.model.trim_for_level_flight(
            weight_n=weight_n,
            speed_ms=kmh_to_ms(60.0),
            atmosphere=self.atm,
            cg_x_fraction_mac=0.22,
        )
        self.assertGreater(trim.cl, 0.1)
        self.assertAlmostEqual(trim.cm, 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
