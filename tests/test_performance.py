"""Integrated performance evaluator tests."""

from pathlib import Path
import unittest

from wingopt.aero import AeroModel
from wingopt.config import load_config
from wingopt.geometry import compute_planform, load_airfoil_library
from wingopt.performance import PerformanceEvaluator
from wingopt.propulsion import PropulsionModel


class PerformanceTests(unittest.TestCase):
    def test_aggregate_performance(self) -> None:
        cfg = load_config(Path("configs/default_wing.toml"))

        geometry = compute_planform(cfg.geometry)
        airfoil = load_airfoil_library(Path("data/airfoils"), cfg.geometry.airfoil_candidates)[cfg.geometry.airfoil]
        aero = AeroModel(geometry=geometry, airfoil=airfoil)

        prop_data = PropulsionModel.load_prop_data(
            Path("data/props") / cfg.propulsion.prop.data_file,
            cfg.propulsion.prop.name,
        )
        propulsion = PropulsionModel(
            motor=cfg.propulsion.motor,
            prop=cfg.propulsion.prop,
            battery=cfg.propulsion.battery,
            prop_data=prop_data,
        )

        evaluator = PerformanceEvaluator(aero=aero, propulsion=propulsion, mission=cfg.mission)
        aggregate = evaluator.aggregate(
            scenarios=cfg.environment.resolved_scenarios(),
            gross_mass_g=1000.0,
            cg_fraction_mac=0.22,
        )

        self.assertGreater(aggregate.weighted_range_km, 0.0)
        self.assertGreater(aggregate.weighted_endurance_h, 0.0)
        self.assertEqual(len(aggregate.scenarios), len(cfg.environment.resolved_scenarios()))


if __name__ == "__main__":
    unittest.main()
