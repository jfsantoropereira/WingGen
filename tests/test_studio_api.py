"""Tests for the WingGen Studio FastAPI server (no real optimization runs)."""

from __future__ import annotations

import json
import struct
import sys
import tempfile
import textwrap
import time
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi.testclient import TestClient  # noqa: E402

from wingopt.store import RunStore  # noqa: E402
from wingopt.studio.jobs import Job  # noqa: E402
from wingopt.studio.schema import apply_overrides, dump_toml  # noqa: E402
from wingopt.studio.server import create_app  # noqa: E402

CONFIG_PATH = ROOT / "configs" / "default_wing.toml"

DESIGN_PARAMS = {
    "geometry.wingspan_m": 1.5,
    "geometry.root_chord_m": 0.24,
    "geometry.tip_chord_m": 0.12,
    "geometry.sweep_deg": 26.0,
    "geometry.dihedral_deg": 4.0,
    "geometry.root_incidence_deg": 0.5,
    "geometry.tip_incidence_deg": -2.0,
    "geometry.airfoil": "mh60",
}

STUB_EVENTS_RUNNER = textwrap.dedent(
    """
    import json, sys

    def emit(event, payload):
        sys.stdout.write(json.dumps(
            {"contract_version": "1.1.0", "event": event, "payload": payload}) + "\\n")
        sys.stdout.flush()

    emit("progress", {"stage": "run_optimization", "percent": 50})
    emit("result", {
        "best_design": {
            "wing": {
                "wingspan_m": 1.42, "root_chord_m": 0.22, "tip_chord_m": 0.11,
                "sweep_deg": 25.0, "dihedral_deg": 3.0,
                "root_incidence_deg": 0.4, "tip_incidence_deg": -1.8,
                "airfoil": "mh60", "cg_fraction_mac": 0.22,
                "cruise_ld": 14.2, "cruise_cd": 0.021, "static_margin": 0.09,
                "stall_speed_kmh": 31.0, "total_mass_g": 780.0,
                "feasible": True, "score": 88.0,
            },
            "propulsion": {
                "gross_mass_g": 950.0, "weighted_range_km": 41.0,
                "weighted_endurance_h": 0.9, "feasible": True, "score": 70.0,
            },
            "combined_score": 123.5,
        },
        "top_wing_candidates": [
            {"wingspan_m": 1.3, "airfoil": "mh61", "cruise_ld": 12.0,
             "feasible": False, "score": 60.0},
        ],
        "organic_refinement": None,
        "artifacts": {"stl_file": "outputs/best_wing.stl"},
    })
    emit("progress", {"stage": "complete", "percent": 100})
    """
)

STUB_SLEEP_RUNNER = textwrap.dedent(
    """
    import json, sys, time
    sys.stdout.write(json.dumps(
        {"contract_version": "1.1.0", "event": "progress",
         "payload": {"stage": "run_optimization", "percent": 10}}) + "\\n")
    sys.stdout.flush()
    time.sleep(60)
    """
)


class StudioApiTestBase(unittest.TestCase):
    """Shared fixture: app bound to a temporary runs_root."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.runs_root = self.tmp_path / "runs"
        self.app = create_app(CONFIG_PATH, runs_root=self.runs_root)
        self.store: RunStore = self.app.state.store

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_stub(self, name: str, source: str) -> Path:
        path = self.tmp_path / name
        path.write_text(source, encoding="utf-8")
        return path

    def use_stub_runner(self, script: Path) -> None:
        def stub_builder(job: Job) -> list[str]:
            return [sys.executable, str(script)]

        self.app.state.jobs.command_builder = stub_builder

    def wait_for_state(self, client: TestClient, job_id: str, target: str, timeout: float = 20.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            body = client.get(f"/api/jobs/{job_id}").json()
            if body["state"] == target:
                return body
            if body["state"] in ("completed", "failed", "cancelled"):
                self.fail(f"Job reached terminal state {body['state']!r}, wanted {target!r}")
            time.sleep(0.05)
        self.fail(f"Timed out waiting for job {job_id} to reach {target!r}")


class HealthAndSchemaTest(StudioApiTestBase):
    def test_health(self) -> None:
        with TestClient(self.app) as client:
            response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("version", body)
        self.assertIsInstance(body["metal_available"], bool)

    def test_schema_params(self) -> None:
        with TestClient(self.app) as client:
            response = client.get("/api/schema/params")
        self.assertEqual(response.status_code, 200)
        entries = {entry["path"]: entry for entry in response.json()}

        wingspan = entries["geometry.wingspan_m"]
        self.assertEqual(wingspan["unit"], "m")
        self.assertEqual(wingspan["kind"], "float")
        self.assertAlmostEqual(wingspan["min"], 1.2)
        self.assertAlmostEqual(wingspan["max"], 1.8)

        airfoil = entries["geometry.airfoil"]
        self.assertEqual(airfoil["kind"], "enum")
        self.assertIn("mh60", airfoil["choices"])
        self.assertGreater(len(airfoil["choices"]), 1)

        self.assertIn("propulsion.battery.parallel", entries)
        self.assertEqual(entries["propulsion.battery.parallel"]["min"], 1)
        self.assertIn("geometry.elevons.span_fraction", entries)
        self.assertEqual(entries["mission.cruise_speed_kmh"]["unit"], "km/h")

    def test_config_default(self) -> None:
        with TestClient(self.app) as client:
            response = client.get("/api/config/default")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["geometry"]["airfoil"], "mh60")

    def test_root_hint_without_dist(self) -> None:
        with TestClient(self.app) as client:
            response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("hint", response.json())


class TomlRoundTripTest(unittest.TestCase):
    def test_default_config_round_trips(self) -> None:
        with CONFIG_PATH.open("rb") as handle:
            original = tomllib.load(handle)
        self.assertEqual(tomllib.loads(dump_toml(original)), original)

    def test_apply_overrides(self) -> None:
        with CONFIG_PATH.open("rb") as handle:
            original = tomllib.load(handle)
        patched = apply_overrides(original, {"geometry.wingspan_m": 1.66})
        self.assertEqual(patched["geometry"]["wingspan_m"], 1.66)
        self.assertEqual(original["geometry"]["wingspan_m"], 1.5)
        reparsed = tomllib.loads(dump_toml(patched))
        self.assertEqual(reparsed["geometry"]["wingspan_m"], 1.66)


class StoreEndpointsTest(StudioApiTestBase):
    def setUp(self) -> None:
        super().setUp()
        run = self.store.create_run(kind="simulate", label="fixture")
        self.run_id = run.run_id
        self.store.append_design(
            run_id=self.run_id,
            source="pass1",
            params=dict(DESIGN_PARAMS),
            metrics={"cruise_ld": 14.0, "range_km": 40.0},
            score=100.0,
            feasible=True,
            label="best",
        )
        self.store.append_design(
            run_id=self.run_id,
            source="pass1",
            params={"geometry.wingspan_m": 1.3},
            metrics={"cruise_ld": 10.0},
            score=50.0,
            feasible=False,
        )
        self.store.update_run(self.run_id, status="completed")

    def test_runs_endpoints(self) -> None:
        with TestClient(self.app) as client:
            listing = client.get("/api/runs")
            single = client.get(f"/api/runs/{self.run_id}")
            missing = client.get("/api/runs/simulate-00000000-000000-dead")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.json()), 1)
        self.assertEqual(single.json()["run_id"], self.run_id)
        self.assertEqual(single.json()["status"], "completed")
        self.assertEqual(missing.status_code, 404)

    def test_designs_ranked_and_filtered(self) -> None:
        with TestClient(self.app) as client:
            ranked = client.get("/api/designs").json()
            feasible = client.get("/api/designs", params={"feasible": 1}).json()
            limited = client.get("/api/designs", params={"limit": 1, "order": "asc"}).json()
            single = client.get(f"/api/designs/{self.run_id}-d0000").json()
            missing = client.get(f"/api/designs/{self.run_id}-d9999")
        self.assertEqual([d["score"] for d in ranked], [100.0, 50.0])
        self.assertEqual(len(feasible), 1)
        self.assertEqual(limited[0]["score"], 50.0)
        self.assertEqual(single["design_id"], f"{self.run_id}-d0000")
        self.assertEqual(missing.status_code, 404)

    def test_export_json_download(self) -> None:
        design_id = f"{self.run_id}-d0000"
        with TestClient(self.app) as client:
            response = client.get(f"/api/designs/{design_id}/export.json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["content-disposition"])
        body = response.json()
        self.assertEqual(body["design_id"], design_id)
        self.assertIn("params", body)

    def test_mesh_stl_binary(self) -> None:
        design_id = f"{self.run_id}-d0000"
        with TestClient(self.app) as client:
            response = client.get(
                f"/api/designs/{design_id}/mesh.stl",
                params={"span_sections": 21, "profile_points": 61},
            )
        self.assertEqual(response.status_code, 200)
        data = response.content
        self.assertGreater(len(data), 84)
        facet_count = struct.unpack("<I", data[80:84])[0]
        self.assertGreater(facet_count, 1000)
        self.assertEqual(len(data), 84 + facet_count * 50)
        # Cached artifact lands in the run's artifacts directory.
        cached = self.runs_root / self.run_id / "artifacts" / f"{design_id}-21x61.stl"
        self.assertTrue(cached.is_file())

    def test_mesh_resolution_bounds(self) -> None:
        design_id = f"{self.run_id}-d0000"
        with TestClient(self.app) as client:
            too_many = client.get(
                f"/api/designs/{design_id}/mesh.stl",
                params={"span_sections": 802, "profile_points": 61},
            )
            too_dense = client.get(
                f"/api/designs/{design_id}/mesh.stl",
                params={"span_sections": 21, "profile_points": 1602},
            )
        self.assertEqual(too_many.status_code, 422)
        self.assertEqual(too_dense.status_code, 422)


class JobLifecycleTest(StudioApiTestBase):
    def test_simulate_job_lifecycle_with_stub_runner(self) -> None:
        script = self.write_stub("stub_events.py", STUB_EVENTS_RUNNER)
        self.use_stub_runner(script)
        with TestClient(self.app) as client:
            created = client.post(
                "/api/jobs",
                json={
                    "kind": "simulate",
                    "label": "stub",
                    "config_overrides": {"geometry.wingspan_m": 1.42},
                    "simulate": {"disable_organic": True},
                },
            )
            self.assertEqual(created.status_code, 202)
            body = created.json()
            job_id, run_id = body["job_id"], body["run_id"]

            final = self.wait_for_state(client, job_id, "completed")
            self.assertEqual(final["exit_code"], 0)

            # events.ndjson written verbatim (plus server run_info/design events).
            events_path = self.runs_root / run_id / "events.ndjson"
            lines = [json.loads(line) for line in events_path.read_text().splitlines()]
            names = [event["event"] for event in lines]
            self.assertEqual(names[0], "run_info")
            self.assertIn("progress", names)
            self.assertIn("result", names)
            self.assertIn("design", names)

            # Materialized config includes the override.
            job_config = tomllib.loads(
                (self.runs_root / run_id / "job_config.toml").read_text()
            )
            self.assertEqual(job_config["geometry"]["wingspan_m"], 1.42)

            # Designs persisted from the result payload.
            designs = client.get("/api/designs", params={"run_id": run_id}).json()
            self.assertEqual(len(designs), 2)
            best = max(designs, key=lambda d: d["score"])
            self.assertEqual(best["source"], "pass1")
            self.assertEqual(best["params"]["geometry.airfoil"], "mh60")
            self.assertAlmostEqual(best["metrics"]["combined_score"], 123.5)
            self.assertAlmostEqual(best["metrics"]["range_km"], 41.0)
            self.assertTrue(best["feasible"])

            # Run marked completed with summary.
            run = client.get(f"/api/runs/{run_id}").json()
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["summary"]["design_count"], 2)

            # SSE replays events then ends.
            seen: list[str] = []
            ended = False
            with client.stream("GET", f"/api/jobs/{job_id}/events") as stream:
                for raw in stream.iter_lines():
                    if raw.startswith("data: "):
                        seen.append(json.loads(raw[len("data: "):])["event"])
                    if raw.startswith("event: end"):
                        ended = True
                        break
            self.assertTrue(ended)
            self.assertIn("result", seen)

    def test_sweep_job_requires_spec_and_uses_spec_file(self) -> None:
        script = self.write_stub("stub_events.py", STUB_EVENTS_RUNNER)
        captured: dict[str, Job] = {}

        def stub_builder(job: Job) -> list[str]:
            captured["job"] = job
            return [sys.executable, str(script)]

        self.app.state.jobs.command_builder = stub_builder
        spec = {
            "kind": "sweep",
            "parameters": [{"path": "geometry.wingspan_m", "min": 1.2, "max": 1.8, "steps": 3}],
            "evaluation": {"mode": "wing_only", "fidelity": "polar_llt"},
            "objective": "combined_score",
        }
        with TestClient(self.app) as client:
            missing = client.post("/api/jobs", json={"kind": "sweep"})
            self.assertEqual(missing.status_code, 422)

            created = client.post("/api/jobs", json={"kind": "sweep", "sweep": spec})
            self.assertEqual(created.status_code, 202)
            body = created.json()
            self.wait_for_state(client, body["job_id"], "completed")

        job = captured["job"]
        self.assertIsNotNone(job.spec_file)
        self.assertEqual(json.loads(job.spec_file.read_text()), spec)
        self.assertTrue(job.run_id.startswith("sweep-"))

    def test_cancel_running_job(self) -> None:
        script = self.write_stub("stub_sleep.py", STUB_SLEEP_RUNNER)
        self.use_stub_runner(script)
        with TestClient(self.app) as client:
            created = client.post("/api/jobs", json={"kind": "simulate"}).json()
            job_id = created["job_id"]
            self.wait_for_state(client, job_id, "running")

            cancelled = client.post(f"/api/jobs/{job_id}/cancel")
            self.assertEqual(cancelled.status_code, 200)

            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                body = client.get(f"/api/jobs/{job_id}").json()
                if body["state"] == "cancelled":
                    break
                time.sleep(0.05)
            self.assertEqual(body["state"], "cancelled")

            run = client.get(f"/api/runs/{created['run_id']}").json()
            self.assertEqual(run["status"], "cancelled")

    def test_spawn_failure_marks_job_failed(self) -> None:
        def broken_builder(job: Job) -> list[str]:
            raise RuntimeError("boom")

        self.app.state.jobs.command_builder = broken_builder
        with TestClient(self.app) as client:
            created = client.post("/api/jobs", json={"kind": "simulate"}).json()
            body = client.get(f"/api/jobs/{created['job_id']}").json()
            self.assertEqual(body["state"], "failed")
            self.assertEqual(body["exit_code"], -1)
            run = client.get(f"/api/runs/{created['run_id']}").json()
            self.assertEqual(run["status"], "failed")
            events_path = self.runs_root / created["run_id"] / "events.ndjson"
            names = [json.loads(line)["event"] for line in events_path.read_text().splitlines()]
            self.assertIn("error", names)

    def test_unknown_job_and_kind(self) -> None:
        with TestClient(self.app) as client:
            self.assertEqual(client.get("/api/jobs/nope").status_code, 404)
            self.assertEqual(
                client.post("/api/jobs", json={"kind": "explode"}).status_code, 422
            )


if __name__ == "__main__":
    unittest.main()
