"""Geometry and airfoil data tests."""

from pathlib import Path
import unittest

from wingopt.config import load_config
from wingopt.geometry import compute_planform, load_airfoil_library


class GeometryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_config(Path("configs/default_wing.toml"))

    def test_planform_metrics(self) -> None:
        wing = compute_planform(self.cfg.geometry)
        self.assertGreater(wing.area_m2, 0.0)
        self.assertGreater(wing.aspect_ratio, 0.0)
        self.assertEqual(len(wing.elevons), 4)

    def test_airfoil_library_loading(self) -> None:
        library = load_airfoil_library(Path("data/airfoils"), self.cfg.geometry.airfoil_candidates)
        self.assertIn("mh60", library)
        self.assertGreater(len(library["mh60"].coordinates), 5)
        self.assertGreater(len(library["mh60"].polars), 5)


if __name__ == "__main__":
    unittest.main()
