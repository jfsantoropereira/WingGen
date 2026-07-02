"""Tests for the file-backed run/design store."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wingopt.store import RunStore, new_run_id  # noqa: E402


class RunStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self._tmp.name) / "runs")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_run_id_shape(self) -> None:
        rid = new_run_id("sweep")
        self.assertTrue(rid.startswith("sweep-"))
        self.assertNotIn("/", rid)

    def test_create_update_run(self) -> None:
        run = self.store.create_run("simulate", label="baseline", config={"a": 1})
        self.assertEqual(run.status, "running")
        updated = self.store.update_run(run.run_id, status="completed", summary={"best": 2.0})
        self.assertEqual(updated.status, "completed")
        fetched = self.store.get_run(run.run_id)
        self.assertEqual(fetched.summary, {"best": 2.0})
        self.assertEqual(fetched.config, {"a": 1})

    def test_invalid_kind_and_status(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create_run("banana")
        run = self.store.create_run("sweep")
        with self.assertRaises(ValueError):
            self.store.update_run(run.run_id, status="exploded")

    def test_designs_append_rank_and_lookup(self) -> None:
        run = self.store.create_run("sweep")
        for i, (score, feasible) in enumerate([(1.0, True), (5.0, True), (3.0, False)]):
            self.store.append_design(
                run.run_id,
                source="sweep_point",
                params={"geometry.wingspan_m": 1.2 + 0.1 * i},
                metrics={"range_km": 100.0 * (i + 1)},
                score=score,
                feasible=feasible,
            )
        ranked = self.store.list_designs()
        self.assertEqual([d.score for d in ranked], [5.0, 3.0, 1.0])
        feasible = self.store.list_designs(feasible_only=True)
        self.assertEqual([d.score for d in feasible], [5.0, 1.0])
        by_metric = self.store.list_designs(sort_by="range_km", descending=False)
        self.assertEqual(by_metric[0].metrics["range_km"], 100.0)
        top = ranked[0]
        self.assertEqual(self.store.get_design(top.design_id).design_id, top.design_id)
        with self.assertRaises(KeyError):
            self.store.get_design(f"{run.run_id}-d9999")

    def test_ranking_across_runs(self) -> None:
        run_a = self.store.create_run("simulate")
        run_b = self.store.create_run("optimize")
        self.store.append_design(run_a.run_id, "pass1", {}, {}, score=2.0, feasible=True)
        self.store.append_design(run_b.run_id, "optimize", {}, {}, score=9.0, feasible=True)
        ranked = self.store.list_designs(limit=1)
        self.assertEqual(ranked[0].run_id, run_b.run_id)

    def test_artifact_path_safety(self) -> None:
        run = self.store.create_run("simulate")
        path = self.store.artifact_path(run.run_id, "wing.stl")
        self.assertTrue(str(path).endswith("artifacts/wing.stl"))
        with self.assertRaises(ValueError):
            self.store.artifact_path(run.run_id, "../evil.stl")


if __name__ == "__main__":
    unittest.main()
