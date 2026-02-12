"""Stability module tests."""

from pathlib import Path
import unittest

from wingopt.aero import AeroModel
from wingopt.config import load_config
from wingopt.geometry import compute_planform, load_airfoil_library
from wingopt.stability import StabilityAnalyzer
from wingopt.utils.atmosphere import build_atmosphere
from wingopt.utils.units import g_to_kg, kmh_to_ms


class StabilityTests(unittest.TestCase):
    def test_stability_analysis(self) -> None:
        cfg = load_config(Path("configs/default_wing.toml"))
        geometry = compute_planform(cfg.geometry)
        airfoil = load_airfoil_library(Path("data/airfoils"), cfg.geometry.airfoil_candidates)[cfg.geometry.airfoil]
        aero = AeroModel(geometry=geometry, airfoil=airfoil)
        analyzer = StabilityAnalyzer(aero_model=aero, stability=cfg.stability)

        atm = build_atmosphere(temperature_c=20.0, altitude_m=100.0, relative_humidity=0.5)
        result = analyzer.analyze(
            atmosphere=atm,
            weight_n=g_to_kg(1000.0) * 9.80665,
            cruise_speed_ms=kmh_to_ms(60.0),
            min_speed_ms=kmh_to_ms(40.0),
            max_speed_ms=kmh_to_ms(100.0),
            cg_fraction_mac=0.22,
        )

        self.assertGreater(result.neutral_point_fraction_mac, 0.0)
        self.assertLess(result.neutral_point_fraction_mac, 1.0)


if __name__ == "__main__":
    unittest.main()
