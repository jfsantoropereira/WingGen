"""Tests for TOML configuration loading and validation."""

from pathlib import Path
import unittest

from wingopt.config import ConfigError, load_config


class ConfigLoaderTests(unittest.TestCase):
    def test_load_default_config(self) -> None:
        cfg = load_config(Path("configs/default_wing.toml"))
        self.assertAlmostEqual(cfg.geometry.wingspan_m, 1.5)
        self.assertEqual(cfg.geometry.airfoil, "mh60")
        self.assertAlmostEqual(cfg.geometry.dihedral_deg, 4.0)
        self.assertGreater(len(cfg.environment.resolved_scenarios()), 0)

    def test_scenarios_are_normalized(self) -> None:
        cfg = load_config(Path("configs/default_wing.toml"))
        scenarios = cfg.environment.resolved_scenarios()
        self.assertAlmostEqual(sum(s.weight for s in scenarios), 1.0)

    def test_invalid_missing_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_config(Path("configs/missing.toml"))

    def test_invalid_schema(self) -> None:
        path = Path("/tmp/winggen_invalid.toml")
        path.write_text("[mission]\ncruise_speed_kmh=70\n", encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(path)


if __name__ == "__main__":
    unittest.main()
