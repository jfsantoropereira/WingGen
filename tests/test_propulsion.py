"""Propulsion module tests."""

from pathlib import Path
import unittest

from wingopt.config import load_config
from wingopt.propulsion import PropulsionModel


class PropulsionTests(unittest.TestCase):
    def setUp(self) -> None:
        cfg = load_config(Path("configs/default_wing.toml"))
        prop_data = PropulsionModel.load_prop_data(
            Path("data/props") / cfg.propulsion.prop.data_file,
            cfg.propulsion.prop.name,
        )
        self.model = PropulsionModel(
            motor=cfg.propulsion.motor,
            prop=cfg.propulsion.prop,
            battery=cfg.propulsion.battery,
            prop_data=prop_data,
        )

    def test_operating_point(self) -> None:
        op = self.model.solve_operating_point(airspeed_ms=16.0, density_kgm3=1.2)
        self.assertGreater(op.thrust_n, 0.0)
        self.assertGreater(op.current_a, 0.0)
        self.assertGreater(op.total_efficiency, 0.0)

    def test_endurance_estimate(self) -> None:
        estimate = self.model.estimate_endurance(cruise_current_a=8.0, cruise_speed_ms=16.0)
        self.assertGreater(estimate.endurance_h, 0.0)
        self.assertGreater(estimate.range_km, 0.0)


if __name__ == "__main__":
    unittest.main()
