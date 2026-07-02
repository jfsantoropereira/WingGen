"""Tests for the parameter-sweep and bounded-optimize engine."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wingopt.config import load_config  # noqa: E402
from wingopt.store import RunStore  # noqa: E402
from wingopt.sweeps import (  # noqa: E402
    ConfigPathError,
    OptimizeSpec,
    SpecError,
    SweepRunner,
    SweepSpec,
    parse_spec,
    set_config_value,
    validate_sweep_against_config,
)

CONFIG_PATH = ROOT / "configs" / "default_wing.toml"
DATA_DIR = ROOT / "data"
SWEEP_SCRIPT = ROOT / "scripts" / "sweep.py"

_BASE_CONFIG = None


def base_config():
    """Load the default config once for the whole module."""
    global _BASE_CONFIG
    if _BASE_CONFIG is None:
        _BASE_CONFIG = load_config(CONFIG_PATH)
    return _BASE_CONFIG


class ConfigPathTests(unittest.TestCase):
    def test_set_float_leaf(self) -> None:
        cfg = set_config_value(base_config(), "geometry.wingspan_m", 1.65)
        self.assertAlmostEqual(cfg.geometry.wingspan_m, 1.65)
        # Original untouched (frozen dataclasses rebuilt, not mutated).
        self.assertAlmostEqual(base_config().geometry.wingspan_m, 1.5)

    def test_deep_nested_with_int_coercion(self) -> None:
        cfg = set_config_value(base_config(), "propulsion.battery.parallel", 2.0)
        self.assertEqual(cfg.propulsion.battery.parallel, 2)
        self.assertIsInstance(cfg.propulsion.battery.parallel, int)
        cfg = set_config_value(cfg, "propulsion.battery.series", 6)
        self.assertEqual(cfg.propulsion.battery.series, 6)

    def test_string_leaf(self) -> None:
        cfg = set_config_value(base_config(), "geometry.airfoil", "mh61")
        self.assertEqual(cfg.geometry.airfoil, "mh61")

    def test_unknown_path_raises(self) -> None:
        with self.assertRaises(ConfigPathError):
            set_config_value(base_config(), "geometry.does_not_exist", 1.0)
        with self.assertRaises(ConfigPathError):
            set_config_value(base_config(), "nonexistent.wingspan_m", 1.0)

    def test_non_leaf_path_raises(self) -> None:
        with self.assertRaises(ConfigPathError):
            set_config_value(base_config(), "geometry.elevons", 1.0)

    def test_type_mismatch_raises(self) -> None:
        with self.assertRaises(ConfigPathError):
            set_config_value(base_config(), "geometry.wingspan_m", "wide")
        with self.assertRaises(ConfigPathError):
            set_config_value(base_config(), "geometry.airfoil", 12.0)


class SpecParsingTests(unittest.TestCase):
    def test_parse_sweep_defaults(self) -> None:
        spec = parse_spec(
            {
                "kind": "sweep",
                "parameters": [
                    {"path": "geometry.wingspan_m", "min": 1.2, "max": 1.8, "steps": 3}
                ],
            }
        )
        self.assertIsInstance(spec, SweepSpec)
        self.assertEqual(spec.mode, "wing_only")
        self.assertEqual(spec.fidelity, "polar_llt")
        self.assertEqual(spec.objective, "combined_score")
        self.assertEqual(spec.axes[0].values, (1.2, 1.5, 1.8))

    def test_grid_cap(self) -> None:
        with self.assertRaisesRegex(SpecError, "2000"):
            parse_spec(
                {
                    "kind": "sweep",
                    "parameters": [
                        {"path": "geometry.wingspan_m", "min": 1.0, "max": 2.0, "steps": 50},
                        {"path": "geometry.sweep_deg", "min": 20.0, "max": 30.0, "steps": 50},
                    ],
                }
            )

    def test_min_greater_than_max(self) -> None:
        with self.assertRaisesRegex(SpecError, "min"):
            parse_spec(
                {
                    "kind": "sweep",
                    "parameters": [
                        {"path": "geometry.wingspan_m", "min": 2.0, "max": 1.0, "steps": 3}
                    ],
                }
            )

    def test_steps_below_one(self) -> None:
        with self.assertRaisesRegex(SpecError, "steps"):
            parse_spec(
                {
                    "kind": "sweep",
                    "parameters": [
                        {"path": "geometry.wingspan_m", "min": 1.0, "max": 2.0, "steps": 0}
                    ],
                }
            )

    def test_unknown_root_and_kind(self) -> None:
        with self.assertRaisesRegex(SpecError, "root"):
            parse_spec(
                {
                    "kind": "sweep",
                    "parameters": [{"path": "optimizer.wing.seed", "values": [1]}],
                }
            )
        with self.assertRaisesRegex(SpecError, "kind"):
            parse_spec({"kind": "banana", "parameters": []})

    def test_wing_only_rejects_full_mode_objectives(self) -> None:
        with self.assertRaisesRegex(SpecError, "full"):
            parse_spec(
                {
                    "kind": "sweep",
                    "parameters": [{"path": "geometry.wingspan_m", "values": [1.5]}],
                    "objective": "range_km",
                }
            )

    def test_unknown_path_against_config(self) -> None:
        spec = parse_spec(
            {
                "kind": "sweep",
                "parameters": [{"path": "geometry.no_such_field", "values": [1.0]}],
            }
        )
        with self.assertRaisesRegex(SpecError, "no_such_field"):
            validate_sweep_against_config(spec, base_config())

    def test_airfoil_values_validated_against_candidates(self) -> None:
        spec = parse_spec(
            {
                "kind": "sweep",
                "parameters": [{"path": "geometry.airfoil", "values": ["not_an_airfoil"]}],
            }
        )
        with self.assertRaisesRegex(SpecError, "not_an_airfoil"):
            validate_sweep_against_config(spec, base_config())

    def test_parse_optimize(self) -> None:
        spec = parse_spec(
            {
                "kind": "optimize",
                "variables": {"geometry.wingspan_m": [1.3, 1.7]},
                "budget": {"max_evaluations": 50},
                "seed": 7,
            }
        )
        self.assertIsInstance(spec, OptimizeSpec)
        self.assertEqual(spec.max_evaluations, 50)
        self.assertEqual(spec.variables["geometry.wingspan_m"], (1.3, 1.7))

    def test_optimize_unknown_variable(self) -> None:
        with self.assertRaisesRegex(SpecError, "structure.foam_density_kgm3"):
            parse_spec(
                {
                    "kind": "optimize",
                    "variables": {"structure.foam_density_kgm3": [20, 40]},
                    "budget": {"max_evaluations": 10},
                }
            )

    def test_optimize_bad_bounds_and_budget(self) -> None:
        with self.assertRaisesRegex(SpecError, "min"):
            parse_spec(
                {
                    "kind": "optimize",
                    "variables": {"geometry.wingspan_m": [1.7, 1.3]},
                    "budget": {"max_evaluations": 10},
                }
            )
        with self.assertRaisesRegex(SpecError, "max_evaluations"):
            parse_spec(
                {
                    "kind": "optimize",
                    "variables": {"geometry.wingspan_m": [1.3, 1.7]},
                    "budget": {"max_evaluations": 0},
                }
            )


class _RunnerHarness(unittest.TestCase):
    """Shared helpers running SweepRunner against a temp store."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self._tmp.name) / "runs")
        self.events: list[tuple[str, dict[str, Any]]] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append((event, payload))

    def _run(self, spec_raw: dict[str, Any], config=None):
        config = config or base_config()
        spec = parse_spec(spec_raw)
        if isinstance(spec, SweepSpec):
            validate_sweep_against_config(spec, config)
        run = self.store.create_run(spec.kind, label="test")
        runner = SweepRunner(
            config=config,
            data_dir=DATA_DIR,
            store=self.store,
            run_id=run.run_id,
            emit=self._emit,
        )
        if isinstance(spec, SweepSpec):
            payload, summary = runner.run_sweep(spec)
        else:
            payload, summary = runner.run_optimize(spec)
        self.store.update_run(run.run_id, status="completed", summary=summary)
        return run.run_id, payload, summary


class SweepRunnerTests(_RunnerHarness):
    def test_2x2_wing_only_sweep(self) -> None:
        run_id, payload, summary = self._run(
            {
                "kind": "sweep",
                "parameters": [
                    {"path": "geometry.wingspan_m", "min": 1.4, "max": 1.6, "steps": 2},
                    {"path": "geometry.sweep_deg", "values": [24.0, 28.0]},
                ],
                "evaluation": {"mode": "wing_only", "fidelity": "polar_llt"},
                "objective": "combined_score",
            }
        )

        designs = self.store.list_designs(run_id=run_id, descending=False)
        self.assertEqual(len(designs), 4)
        self.assertTrue(all(d.source == "sweep_point" for d in designs))

        points = [payload for event, payload in self.events if event == "sweep_point"]
        self.assertEqual([p["index"] for p in points], [0, 1, 2, 3])
        self.assertTrue(all(p["total"] == 4 for p in points))
        for point in points:
            self.assertIn("cruise_ld", point["metrics"])
            self.assertIn("static_margin", point["metrics"])
            self.assertIn("geometry.wingspan_m", point["params"])
            self.assertIn("geometry.sweep_deg", point["params"])

        # Ranking respects the objective: best score is the max combined_score.
        best_score = max(p["metrics"]["combined_score"] for p in points)
        self.assertAlmostEqual(payload["best"]["score"], best_score)
        self.assertEqual(payload["total_points"], 4)
        self.assertEqual(summary["total_points"], 4)
        self.assertEqual(summary["objective"], "combined_score")
        top_scores = [d["score"] for d in payload["top"]]
        self.assertEqual(top_scores, sorted(top_scores, reverse=True))

        run = self.store.get_run(run_id)
        self.assertEqual(run.status, "completed")

    def test_airfoil_values_axis(self) -> None:
        run_id, _payload, _ = self._run(
            {
                "kind": "sweep",
                "parameters": [{"path": "geometry.airfoil", "values": ["mh60", "mh61"]}],
            }
        )
        designs = self.store.list_designs(run_id=run_id, descending=False)
        self.assertEqual(len(designs), 2)
        swept = {d.params["geometry.airfoil"] for d in designs}
        self.assertEqual(swept, {"mh60", "mh61"})
        self.assertTrue(all(d.metrics["cruise_ld"] > 0 for d in designs))

    def test_full_mode_single_point(self) -> None:
        run_id, _payload, _ = self._run(
            {
                "kind": "sweep",
                "parameters": [{"path": "geometry.wingspan_m", "values": [1.5]}],
                "evaluation": {"mode": "full", "fidelity": "polar_llt"},
                "objective": "range_km",
            }
        )
        designs = self.store.list_designs(run_id=run_id)
        self.assertEqual(len(designs), 1)
        metrics = designs[0].metrics
        self.assertGreater(metrics["range_km"], 0.0)
        self.assertGreater(metrics["endurance_h"], 0.0)
        self.assertGreater(metrics["gross_mass_g"], 0.0)
        self.assertAlmostEqual(designs[0].score, metrics["range_km"])

    def test_vlm_fidelity_enriches_metrics(self) -> None:
        run_id, _, _ = self._run(
            {
                "kind": "sweep",
                "parameters": [{"path": "geometry.wingspan_m", "values": [1.5]}],
                "evaluation": {"mode": "wing_only", "fidelity": "vlm"},
            }
        )
        notes = [
            payload.get("note", "")
            for event, payload in self.events
            if event == "progress"
        ]
        self.assertTrue(any("vortex-lattice" in note for note in notes))
        designs = self.store.list_designs(run_id=run_id)
        self.assertEqual(len(designs), 1)
        metrics = designs[0].metrics
        self.assertIn("vlm_cdi_cruise", metrics)
        self.assertIn("vlm_static_margin", metrics)
        self.assertGreater(metrics["vlm_cdi_cruise"], 0.0)
        self.assertGreater(metrics["vlm_cl_cruise"], 0.0)
        self.assertGreater(metrics["vlm_cl_alpha_per_deg"], 0.0)
        self.assertTrue(-10.0 < metrics["vlm_alpha_trim_deg"] < 15.0)
        self.assertTrue(-0.5 < metrics["vlm_static_margin"] < 0.5)


class OptimizeRunnerTests(_RunnerHarness):
    def test_optimize_two_variables_tiny_budget(self) -> None:
        cfg = base_config()
        fast_coordinator = replace(cfg.optimizer.coordinator, max_coupling_iterations=1)
        cfg = replace(cfg, optimizer=replace(cfg.optimizer, coordinator=fast_coordinator))

        bounds = {"geometry.wingspan_m": [1.3, 1.7], "geometry.sweep_deg": [20.0, 32.0]}
        run_id, payload, summary = self._run(
            {
                "kind": "optimize",
                "variables": bounds,
                "budget": {"max_evaluations": 30},
                "objective": "combined_score",
                "seed": 42,
            },
            config=cfg,
        )

        designs = self.store.list_designs(run_id=run_id)
        self.assertGreaterEqual(len(designs), 1)
        self.assertTrue(all(d.source == "optimize" for d in designs))
        best = payload["best"]
        self.assertIsNotNone(best)
        self.assertIn("combined_score", best["metrics"])
        self.assertEqual(best["label"], "best")

        # Bound overrides must be respected in every persisted design.
        for design in designs:
            for path, (low, high) in bounds.items():
                value = design.params[path]
                self.assertGreaterEqual(value, low, f"{path} below bound in {design.design_id}")
                self.assertLessEqual(value, high, f"{path} above bound in {design.design_id}")

        design_events = [payload for event, payload in self.events if event == "design"]
        self.assertEqual(len(design_events), len(designs))
        self.assertEqual(self.store.get_run(run_id).status, "completed")
        self.assertEqual(summary["objective"], "combined_score")


class CliTests(unittest.TestCase):
    def _invoke(self, spec: dict[str, Any], runs_root: Path, extra: list[str] | None = None):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, dir=str(runs_root.parent)
        ) as handle:
            json.dump(spec, handle)
            spec_path = handle.name
        proc = subprocess.run(
            [
                sys.executable,
                str(SWEEP_SCRIPT),
                "--config",
                str(CONFIG_PATH),
                "--spec",
                spec_path,
                "--runs-root",
                str(runs_root),
                "--data-dir",
                str(DATA_DIR),
                *(extra or []),
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=110,
        )
        events = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        return proc, events

    def test_cli_end_to_end_ndjson_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            proc, events = self._invoke(
                {
                    "kind": "sweep",
                    "parameters": [
                        {"path": "geometry.wingspan_m", "min": 1.4, "max": 1.6, "steps": 2}
                    ],
                    "evaluation": {"mode": "wing_only", "fidelity": "polar_llt"},
                },
                runs_root,
                extra=["--label", "cli-e2e"],
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

            for event in events:
                self.assertEqual(event["contract_version"], "1.1.0")
                self.assertIn("event", event)
                self.assertIsInstance(event["payload"], dict)

            names = [event["event"] for event in events]
            self.assertIn("run_info", names)
            self.assertIn("sweep_point", names)
            self.assertIn("result", names)
            # run_info precedes all store-backed events.
            self.assertLess(names.index("run_info"), names.index("sweep_point"))

            run_info = next(e for e in events if e["event"] == "run_info")["payload"]
            self.assertEqual(run_info["kind"], "sweep")
            self.assertEqual(run_info["label"], "cli-e2e")

            result = next(e for e in events if e["event"] == "result")["payload"]
            self.assertEqual(result["total_points"], 2)
            self.assertIn("feasible_points", result)
            self.assertIsNotNone(result["best"])
            self.assertLessEqual(len(result["top"]), 10)
            self.assertEqual(result["run_id"], run_info["run_id"])

            store = RunStore(runs_root)
            run = store.get_run(run_info["run_id"])
            self.assertEqual(run.status, "completed")
            self.assertEqual(run.summary["total_points"], 2)
            self.assertEqual(len(store.list_designs(run_id=run_info["run_id"])), 2)

    def test_cli_spec_errors_exit_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            proc, events = self._invoke(
                {
                    "kind": "sweep",
                    "parameters": [{"path": "geometry.bogus_field", "values": [1.0]}],
                },
                runs_root,
            )
            self.assertEqual(proc.returncode, 2)
            errors = [e for e in events if e["event"] == "error"]
            self.assertEqual(len(errors), 1)
            self.assertIn("bogus_field", errors[0]["payload"]["message"])

            proc, events = self._invoke(
                {
                    "kind": "sweep",
                    "parameters": [
                        {"path": "geometry.wingspan_m", "min": 1.0, "max": 2.0, "steps": 50},
                        {"path": "geometry.sweep_deg", "min": 20.0, "max": 30.0, "steps": 50},
                    ],
                },
                runs_root,
            )
            self.assertEqual(proc.returncode, 2)
            errors = [e for e in events if e["event"] == "error"]
            self.assertIn("2000", errors[0]["payload"]["message"])


if __name__ == "__main__":
    unittest.main()
