"""Optimizer stack tests (wing + propulsion + coordinator)."""

from dataclasses import replace
from pathlib import Path
import unittest

from wingopt.config import load_config
from wingopt.optimizer import OptimizationCoordinator, PropulsionOptimizer, WingOptimizer


class OptimizerTests(unittest.TestCase):
    def setUp(self) -> None:
        cfg = load_config(Path("configs/default_wing.toml"))
        fast_wing = replace(cfg.optimizer.wing, max_evaluations=24)
        fast_prop = replace(cfg.optimizer.propulsion, max_evaluations=24)
        fast_coord = replace(cfg.optimizer.coordinator, max_coupling_iterations=2)
        self.cfg = replace(cfg, optimizer=replace(cfg.optimizer, wing=fast_wing, propulsion=fast_prop, coordinator=fast_coord))

    def test_wing_optimizer(self) -> None:
        optimizer = WingOptimizer(config=self.cfg, data_dir="data")
        results = optimizer.optimize(top_k=3)
        self.assertGreater(len(results), 0)
        self.assertTrue(all(r.airfoil in self.cfg.geometry.airfoil_candidates for r in results))

    def test_propulsion_optimizer(self) -> None:
        wing_results = WingOptimizer(config=self.cfg, data_dir="data").optimize(top_k=2)
        prop_results = PropulsionOptimizer(config=self.cfg, data_dir="data").optimize_for_wings(wing_results, top_k=3)
        self.assertGreater(len(prop_results), 0)

    def test_coordinator(self) -> None:
        result = OptimizationCoordinator(config=self.cfg, data_dir="data").run()
        self.assertGreater(len(result.iterations), 0)
        self.assertTrue(result.best_design.propulsion.weighted_range_km > 0)


if __name__ == "__main__":
    unittest.main()
