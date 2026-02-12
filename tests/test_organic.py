"""Organic refinement tests."""

from dataclasses import replace
from pathlib import Path
import unittest

from wingopt.config import load_config
from wingopt.optimizer import WingOptimizer
from wingopt.organic import OrganicRefiner


class OrganicRefinementTests(unittest.TestCase):
    def test_refiner_runs_and_outputs_profile(self) -> None:
        cfg = load_config(Path("configs/default_wing.toml"))
        fast_wing = replace(cfg.optimizer.wing, max_evaluations=20)
        fast_optimizer = replace(cfg.optimizer, wing=fast_wing)
        fast_organic = replace(
            cfg.organic_refinement,
            generations=4,
            population_size=10,
            elite_count=2,
            seed=99,
        )
        cfg = replace(cfg, optimizer=fast_optimizer, organic_refinement=fast_organic)

        wing_candidates = WingOptimizer(config=cfg, data_dir="data").optimize(top_k=1)
        self.assertGreater(len(wing_candidates), 0)

        result = OrganicRefiner(config=cfg, data_dir="data").refine(wing_candidates[0])
        self.assertEqual(len(result.generations), 4)
        self.assertEqual(
            len(result.best_candidate.dihedral_profile),
            len(cfg.organic_refinement.dihedral_profile.eta_control_points),
        )
        self.assertGreater(
            result.best_candidate.effective_dihedral_deg,
            cfg.organic_refinement.dihedral_profile.angle_bounds_deg.minimum - 0.5,
        )
        self.assertLess(
            result.best_candidate.effective_dihedral_deg,
            cfg.organic_refinement.dihedral_profile.angle_bounds_deg.maximum + 0.5,
        )

    def test_external_engine_contract_runner(self) -> None:
        cfg = load_config(Path("configs/default_wing.toml"))
        fast_wing = replace(cfg.optimizer.wing, max_evaluations=12)
        fast_optimizer = replace(cfg.optimizer, wing=fast_wing)
        cfd_cfg = replace(
            cfg.organic_refinement.cfd,
            case_root="/tmp/winggen_cfd_cases",
            external_runner=(
                "python3 scripts/cfd/mock_external_cfd.py "
                "--engine {engine} --input-json {input_json} --output-json {output_json}"
            ),
        )
        external_organic = replace(
            cfg.organic_refinement,
            engine="su2",
            generations=2,
            population_size=6,
            elite_count=1,
            seed=11,
            cfd=cfd_cfg,
        )
        cfg = replace(cfg, optimizer=fast_optimizer, organic_refinement=external_organic)

        wing_candidates = WingOptimizer(config=cfg, data_dir="data").optimize(top_k=1)
        self.assertGreater(len(wing_candidates), 0)

        result = OrganicRefiner(config=cfg, data_dir="data").refine(wing_candidates[0])
        self.assertEqual(result.best_candidate.source, "su2")
        self.assertGreater(result.best_candidate.drag_coefficient, 0.0)


if __name__ == "__main__":
    unittest.main()
