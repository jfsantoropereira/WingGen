"""Job manager: spawns runner subprocesses, streams NDJSON events, owns queueing.

Responsibilities:
    * Create the store run first (so ``run_id`` is known), materialize the job
      config/spec files inside the run directory, then spawn the runner.
    * Enforce a concurrency limit (2); extra jobs queue as ``pending``.
    * Pump runner stdout lines verbatim into ``<run_dir>/events.ndjson`` and
      fan them out to SSE subscribers via the server's asyncio loop.
    * On exit, update run status and (for ``simulate`` jobs) persist the
      designs found in the final ``result`` event into the run store.

The runner command is built by ``self.command_builder`` which tests may
replace with a stub (signature: ``callable(job: Job) -> list[str]``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import tomllib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wingopt.store import RunStore
from wingopt.studio.schema import apply_overrides, dump_toml

LOGGER = logging.getLogger(__name__)

MAX_CONCURRENT_JOBS = 2
CANCEL_KILL_GRACE_S = 10.0
STREAM_END = object()

_WING_PARAM_KEYS = (
    "wingspan_m",
    "root_chord_m",
    "tip_chord_m",
    "sweep_deg",
    "dihedral_deg",
    "root_incidence_deg",
    "tip_incidence_deg",
    "airfoil",
)
_WING_METRIC_KEYS = ("cruise_ld", "cruise_cd", "static_margin", "stall_speed_kmh", "total_mass_g")


@dataclass
class Job:
    """In-memory state for one runner job."""

    job_id: str
    run_id: str
    kind: str
    label: str
    config_file: Path
    run_dir: Path
    spec_file: Path | None = None
    options: dict[str, Any] = field(default_factory=dict)
    state: str = "pending"
    exit_code: int | None = None
    cancel_requested: bool = False
    process: subprocess.Popen[str] | None = None
    events: list[str] = field(default_factory=list)
    result_payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the public REST representation of this job."""
        return {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "label": self.label,
            "state": self.state,
            "exit_code": self.exit_code,
        }


def default_command_builder(job: Job) -> list[str]:
    """Build the runner argv for a job per the studio contract (section 7).

    ``simulate`` maps to ``scripts/simulate.py``; ``sweep``/``optimize`` map to
    ``scripts/sweep.py --spec ... --runs-root ... --run-id ...``.
    """
    python = sys.executable
    if job.kind == "simulate":
        cmd = [
            python,
            "scripts/simulate.py",
            "--config",
            str(job.config_file),
            "--stl-out",
            str(job.run_dir / "artifacts" / "baseline.stl"),
        ]
        if job.options.get("disable_organic"):
            cmd.append("--disable-organic")
        engine = job.options.get("organic_engine")
        if engine:
            cmd.extend(["--organic-engine", str(engine)])
        return cmd
    if job.kind in ("sweep", "optimize"):
        if job.spec_file is None:
            raise ValueError(f"Job {job.job_id} of kind {job.kind!r} has no spec file")
        return [
            python,
            "scripts/sweep.py",
            "--config",
            str(job.config_file),
            "--spec",
            str(job.spec_file),
            "--runs-root",
            str(job.run_dir.parent),
            "--run-id",
            job.run_id,
        ]
    raise ValueError(f"Unknown job kind: {job.kind!r}")


class JobManager:
    """Queue, spawn, and monitor runner subprocesses against the run store."""

    def __init__(
        self,
        store: RunStore,
        repo_root: Path,
        default_config_path: Path,
        command_builder: Callable[[Job], list[str]] | None = None,
        max_concurrent: int = MAX_CONCURRENT_JOBS,
    ) -> None:
        self.store = store
        self.repo_root = repo_root
        self.default_config_path = default_config_path
        self.command_builder = command_builder or default_command_builder
        self.max_concurrent = max_concurrent
        self.loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._pending: list[str] = []
        self._running: set[str] = set()
        self._subscribers: dict[str, list[asyncio.Queue[Any]]] = {}

    # --------------------------------------------------------------- public

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Register the server's asyncio loop for SSE fan-out."""
        self.loop = loop

    def list_jobs(self) -> list[dict[str, Any]]:
        """Return all jobs, newest first."""
        with self._lock:
            return [self._jobs[jid].to_dict() for jid in reversed(self._order)]

    def get_job(self, job_id: str) -> Job:
        """Return a job by ID.

        Raises:
            KeyError: If the job is unknown.
        """
        with self._lock:
            return self._jobs[job_id]

    def submit(
        self,
        kind: str,
        label: str = "",
        config_overrides: dict[str, Any] | None = None,
        simulate_options: dict[str, Any] | None = None,
        spec: dict[str, Any] | None = None,
    ) -> Job:
        """Create the store run, materialize inputs, and enqueue the job.

        Args:
            kind: One of ``simulate``, ``sweep``, ``optimize``.
            label: Optional human label recorded on the run.
            config_overrides: Dotted-path config overrides.
            simulate_options: ``{disable_organic?, organic_engine?}``.
            spec: Sweep/optimize spec JSON (required for those kinds).

        Returns:
            The queued job (state ``pending``).
        """
        overrides = dict(config_overrides or {})
        with self.default_config_path.open("rb") as handle:
            raw_config = tomllib.load(handle)
        raw_config = apply_overrides(raw_config, overrides)

        run = self.store.create_run(
            kind=kind,
            label=label,
            config={"toml": raw_config, "overrides": overrides},
        )
        run_dir = self.store.root / run.run_id

        if kind == "simulate":
            # Keep organic export artifacts inside the run directory.
            raw_config = apply_overrides(
                raw_config,
                {
                    "organic_refinement.export.output_stl": str(
                        run_dir / "artifacts" / "organic.stl"
                    )
                },
            )
        config_file = run_dir / "job_config.toml"
        config_file.write_text(dump_toml(raw_config), encoding="utf-8")

        spec_file: Path | None = None
        if kind in ("sweep", "optimize"):
            if spec is None:
                raise ValueError(f"Job kind {kind!r} requires a spec")
            spec_file = run_dir / "job_spec.json"
            spec_file.write_text(json.dumps(spec, indent=2), encoding="utf-8")

        job = Job(
            job_id=uuid.uuid4().hex[:12],
            run_id=run.run_id,
            kind=kind,
            label=label,
            config_file=config_file,
            run_dir=run_dir,
            spec_file=spec_file,
            options=dict(simulate_options or {}),
        )
        with self._lock:
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
            self._pending.append(job.job_id)
        self._emit_server_event(
            job, "run_info", {"run_id": job.run_id, "kind": job.kind, "label": job.label}
        )
        self._pump_queue()
        return job

    def cancel(self, job_id: str) -> Job:
        """Cancel a job: dequeue if pending, else SIGTERM its process group.

        SIGKILL is escalated after ``CANCEL_KILL_GRACE_S`` seconds if the
        process has not exited.
        """
        with self._lock:
            job = self._jobs[job_id]
            job.cancel_requested = True
            if job.state == "pending":
                self._pending.remove(job_id)
                job.state = "cancelled"
                process = None
            else:
                process = job.process if job.state == "running" else None
        if process is None:
            if job.state == "cancelled":
                self._safe_update_run(job.run_id, "cancelled")
                self._finish_stream(job)
            return job

        self._signal_group(process, signal.SIGTERM)

        def escalate() -> None:
            if process.poll() is None:
                self._signal_group(process, signal.SIGKILL)

        timer = threading.Timer(CANCEL_KILL_GRACE_S, escalate)
        timer.daemon = True
        timer.start()
        return job

    def subscribe(self, job_id: str) -> tuple[list[str], asyncio.Queue[Any] | None]:
        """Atomically snapshot past events and register a live queue.

        Returns:
            ``(snapshot_lines, queue)``; ``queue`` is ``None`` when the job is
            already terminal (nothing further will arrive).
        """
        with self._lock:
            job = self._jobs[job_id]
            snapshot = list(job.events)
            if job.state in ("completed", "failed", "cancelled"):
                return snapshot, None
            queue: asyncio.Queue[Any] = asyncio.Queue()
            self._subscribers.setdefault(job_id, []).append(queue)
            return snapshot, queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue[Any]) -> None:
        """Drop a live SSE subscriber queue."""
        with self._lock:
            subscribers = self._subscribers.get(job_id, [])
            if queue in subscribers:
                subscribers.remove(queue)

    def shutdown(self) -> None:
        """Terminate all running jobs (used on server shutdown)."""
        with self._lock:
            running = [self._jobs[jid] for jid in self._running]
        for job in running:
            if job.process is not None and job.process.poll() is None:
                job.cancel_requested = True
                self._signal_group(job.process, signal.SIGTERM)

    # -------------------------------------------------------------- internal

    def _pump_queue(self) -> None:
        while True:
            failure: tuple[Job, Exception] | None = None
            with self._lock:
                if not self._pending or len(self._running) >= self.max_concurrent:
                    return
                job = self._jobs[self._pending.pop(0)]
                try:
                    self._start_locked(job)
                except Exception as exc:  # job must fail, not the server
                    LOGGER.exception("Failed to start job %s", job.job_id)
                    job.state = "failed"
                    job.exit_code = -1
                    self._running.discard(job.job_id)
                    failure = (job, exc)
            if failure is not None:
                # Outside the lock: these helpers re-acquire it.
                failed_job, exc = failure
                self._safe_update_run(failed_job.run_id, "failed")
                self._emit_server_event(
                    failed_job, "error", {"message": str(exc), "stage": "spawn"}
                )
                self._finish_stream(failed_job)

    def _start_locked(self, job: Job) -> None:
        command = self.command_builder(job)
        stderr_path = job.run_dir / "runner.stderr.log"
        with stderr_path.open("ab") as stderr_handle:
            process = subprocess.Popen(  # fixed argv list, no shell
                command,
                cwd=str(self.repo_root),
                stdout=subprocess.PIPE,
                stderr=stderr_handle,
                text=True,
                start_new_session=True,
            )
        job.process = process
        job.state = "running"
        self._running.add(job.job_id)
        thread = threading.Thread(target=self._pump_job, args=(job,), daemon=True)
        thread.start()

    def _pump_job(self, job: Job) -> None:
        process = job.process
        assert process is not None and process.stdout is not None
        try:
            for line in process.stdout:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                self._append_event_line(job, line)
                self._track_result(job, line)
        finally:
            process.stdout.close()
            exit_code = process.wait()
            self._on_job_exit(job, exit_code)

    def _on_job_exit(self, job: Job, exit_code: int) -> None:
        if job.cancel_requested:
            state = "cancelled"
        elif exit_code == 0:
            state = "completed"
        else:
            state = "failed"
        if state == "completed" and job.kind == "simulate":
            try:
                self._persist_simulate_designs(job)
            except Exception:  # persistence must not crash the pump
                LOGGER.exception("Failed to persist designs for job %s", job.job_id)
        with self._lock:
            job.exit_code = exit_code
            job.state = state
            self._running.discard(job.job_id)
        self._safe_update_run(job.run_id, state)
        self._finish_stream(job)
        self._pump_queue()

    def _track_result(self, job: Job, line: str) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if isinstance(event, dict) and event.get("event") == "result":
            payload = event.get("payload")
            if isinstance(payload, dict):
                job.result_payload = payload

    # ------------------------------------------------------- design persist

    def _persist_simulate_designs(self, job: Job) -> None:
        payload = job.result_payload
        if not payload:
            return
        best = payload.get("best_design") or {}
        wing = best.get("wing") or {}
        propulsion = best.get("propulsion") or {}
        organic = payload.get("organic_refinement")
        artifacts_raw = payload.get("artifacts") or {}

        params = _wing_params(wing)
        if isinstance(organic, dict):
            profile = _organic_dihedral_profile(organic)
            if profile is not None:
                params["organic.dihedral_profile"] = profile
        metrics = _wing_metrics(wing)
        if propulsion:
            metrics["gross_mass_g"] = _num(propulsion.get("gross_mass_g"))
            metrics["range_km"] = _num(propulsion.get("weighted_range_km"))
            metrics["endurance_h"] = _num(propulsion.get("weighted_endurance_h"))
        combined = best.get("combined_score")
        if combined is not None:
            metrics["combined_score"] = _num(combined)
        score = _num(combined if combined is not None else wing.get("score"))
        feasible = bool(wing.get("feasible", False)) and bool(propulsion.get("feasible", True))
        source = "organic" if isinstance(organic, dict) else "pass1"

        record = self.store.append_design(
            run_id=job.run_id,
            source=source,
            params=params,
            metrics=metrics,
            score=score,
            feasible=feasible,
            label="best_design",
            artifacts=self._relative_artifacts(job, artifacts_raw),
        )
        self._emit_design_event(job, record.design_id, source, score, feasible)

        for candidate in payload.get("top_wing_candidates") or []:
            if not isinstance(candidate, dict):
                continue
            cand_score = _num(candidate.get("score"))
            cand_feasible = bool(candidate.get("feasible", False))
            cand_record = self.store.append_design(
                run_id=job.run_id,
                source="pass1",
                params=_wing_params(candidate),
                metrics=_wing_metrics(candidate),
                score=cand_score,
                feasible=cand_feasible,
                label="wing_candidate",
            )
            self._emit_design_event(job, cand_record.design_id, "pass1", cand_score, cand_feasible)

        self._safe_update_run_summary(
            job.run_id,
            {
                "best_score": score,
                "best_design_source": source,
                "design_count": 1 + len(payload.get("top_wing_candidates") or []),
            },
        )

    def _relative_artifacts(self, job: Job, artifacts_raw: dict[str, Any]) -> dict[str, str]:
        artifacts: dict[str, str] = {}
        for name, value in artifacts_raw.items():
            if not value:
                continue
            path = Path(str(value))
            if not path.is_absolute():
                path = self.repo_root / path
            try:
                artifacts[name] = str(path.resolve().relative_to(job.run_dir.resolve()))
            except ValueError:
                artifacts[name] = str(path)
        return artifacts

    def _emit_design_event(
        self, job: Job, design_id: str, source: str, score: float, feasible: bool
    ) -> None:
        self._emit_server_event(
            job,
            "design",
            {"design_id": design_id, "source": source, "score": score, "feasible": feasible},
        )

    # ----------------------------------------------------------- event plumb

    def _emit_server_event(self, job: Job, event: str, payload: dict[str, Any]) -> None:
        line = json.dumps({"contract_version": "1.1.0", "event": event, "payload": payload})
        self._append_event_line(job, line)

    def _append_event_line(self, job: Job, line: str) -> None:
        events_path = job.run_dir / "events.ndjson"
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        with self._lock:
            job.events.append(line)
            subscribers = list(self._subscribers.get(job.job_id, []))
        self._dispatch(subscribers, line)

    def _finish_stream(self, job: Job) -> None:
        with self._lock:
            subscribers = self._subscribers.pop(job.job_id, [])
        self._dispatch(subscribers, STREAM_END)

    def _dispatch(self, subscribers: list[asyncio.Queue[Any]], item: Any) -> None:
        loop = self.loop
        if loop is None or loop.is_closed() or not subscribers:
            return
        for queue in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:
                pass

    # --------------------------------------------------------------- helpers

    def _safe_update_run(self, run_id: str, status: str) -> None:
        try:
            self.store.update_run(run_id, status=status)
        except (KeyError, OSError, ValueError):
            LOGGER.exception("Failed to update run %s to %s", run_id, status)

    def _safe_update_run_summary(self, run_id: str, summary: dict[str, Any]) -> None:
        try:
            self.store.update_run(run_id, summary=summary)
        except (KeyError, OSError, ValueError):
            LOGGER.exception("Failed to update run %s summary", run_id)

    @staticmethod
    def _signal_group(process: subprocess.Popen[str], sig: signal.Signals) -> None:
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                process.send_signal(sig)
            except (ProcessLookupError, OSError):
                pass


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _wing_params(wing: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key in _WING_PARAM_KEYS:
        if key in wing:
            params[f"geometry.{key}"] = wing[key]
    if "cg_fraction_mac" in wing:
        params["cg_fraction_mac"] = wing["cg_fraction_mac"]
    return params


def _wing_metrics(wing: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for key in _WING_METRIC_KEYS:
        if key in wing:
            metrics[key] = _num(wing[key])
    return metrics


def _organic_dihedral_profile(organic: dict[str, Any]) -> list[list[float]] | None:
    best = organic.get("best_candidate")
    if not isinstance(best, dict):
        return None
    profile = best.get("dihedral_profile")
    if not isinstance(profile, list) or not profile:
        return None
    points: list[list[float]] = []
    for item in profile:
        if isinstance(item, dict) and "eta" in item and "angle_deg" in item:
            points.append([_num(item["eta"]), _num(item["angle_deg"])])
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            points.append([_num(item[0]), _num(item[1])])
        else:
            return None
    return points
