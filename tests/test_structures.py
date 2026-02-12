"""Structures model tests."""

from pathlib import Path
import unittest

from wingopt.config import load_config
from wingopt.geometry import compute_planform
from wingopt.structures import StructuresModel


class StructuresTests(unittest.TestCase):
    def test_structure_estimation(self) -> None:
        cfg = load_config(Path("configs/default_wing.toml"))
        geometry = compute_planform(cfg.geometry)
        model = StructuresModel(geometry=geometry, structure=cfg.structure, components=cfg.components)
        result = model.estimate(gross_weight_n=12.0)

        self.assertGreater(result.structure_mass_g, 0.0)
        self.assertGreater(result.total_empty_mass_g, result.structure_mass_g)
        self.assertTrue(result.checks.deflection_limit_m > 0)


if __name__ == "__main__":
    unittest.main()
