"""File-backed run/design store.

Layout (under a configurable ``runs_root`` directory)::

    runs_root/
      <run_id>/
        run.json          # RunRecord (kind, status, label, config snapshot, summary)
        designs.jsonl     # one DesignRecord JSON object per line, appended by run owner
        events.ndjson     # raw NDJSON event stream captured from the runner (optional)
        artifacts/        # STL and other exported files

There is intentionally no shared index file: each run directory is
self-describing and listing scans ``runs_root``. Exactly one process owns a
run directory for writing (the runner that created it), so appends never
contend across processes.

Design IDs are ``"<run_id>-d<seq:04d>"`` and are globally unique because run
IDs embed a timestamp plus random suffix.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VALID_RUN_KINDS = ("simulate", "sweep", "optimize")
VALID_RUN_STATUSES = ("running", "completed", "failed", "cancelled")


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def new_run_id(kind: str) -> str:
    """Return a unique, path-safe, sortable run identifier."""
    if kind not in VALID_RUN_KINDS:
        raise ValueError(f"Unknown run kind: {kind!r}")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{kind}-{stamp}-{secrets.token_hex(2)}"


@dataclass(frozen=True)
class RunRecord:
    """Metadata for one run (simulate / sweep / optimize job)."""

    run_id: str
    kind: str
    status: str
    label: str
    created_at: str
    updated_at: str
    config: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> RunRecord:
        return RunRecord(
            run_id=str(raw["run_id"]),
            kind=str(raw["kind"]),
            status=str(raw["status"]),
            label=str(raw.get("label", "")),
            created_at=str(raw["created_at"]),
            updated_at=str(raw.get("updated_at", raw["created_at"])),
            config=dict(raw.get("config", {})),
            summary=dict(raw.get("summary", {})),
        )


@dataclass(frozen=True)
class DesignRecord:
    """One evaluated wing design with parameters, metrics, and artifacts.

    Attributes:
        design_id: Globally unique ID ("<run_id>-d<seq:04d>").
        run_id: Owning run.
        source: Producing stage ("pass1", "organic", "sweep_point", "optimize").
        label: Optional human label.
        created_at: UTC ISO timestamp.
        params: Design parameters (config-path keyed, e.g. "geometry.wingspan_m").
        metrics: Evaluated metrics (e.g. "cruise_ld", "range_km", "static_margin").
        score: Scalar ranking score (higher is better).
        feasible: Whether all hard constraints passed.
        artifacts: Artifact name -> path relative to the run directory.
    """

    design_id: str
    run_id: str
    source: str
    label: str
    created_at: str
    params: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    feasible: bool = False
    artifacts: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> DesignRecord:
        return DesignRecord(
            design_id=str(raw["design_id"]),
            run_id=str(raw["run_id"]),
            source=str(raw.get("source", "unknown")),
            label=str(raw.get("label", "")),
            created_at=str(raw.get("created_at", "")),
            params=dict(raw.get("params", {})),
            metrics=dict(raw.get("metrics", {})),
            score=float(raw.get("score", 0.0)),
            feasible=bool(raw.get("feasible", False)),
            artifacts={str(k): str(v) for k, v in raw.get("artifacts", {}).items()},
        )


class RunStore:
    """Reader/writer for the file-backed run store rooted at ``runs_root``."""

    def __init__(self, runs_root: Path | str) -> None:
        self.root = Path(runs_root)

    # ---------------------------------------------------------------- writes

    def create_run(
        self,
        kind: str,
        label: str = "",
        config: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> RunRecord:
        """Create a run directory and its initial ``run.json``."""
        rid = run_id or new_run_id(kind)
        if kind not in VALID_RUN_KINDS:
            raise ValueError(f"Unknown run kind: {kind!r}")
        run_dir = self.root / rid
        if run_dir.exists():
            raise FileExistsError(f"Run directory already exists: {run_dir}")
        (run_dir / "artifacts").mkdir(parents=True)
        now = _utc_now_iso()
        record = RunRecord(
            run_id=rid,
            kind=kind,
            status="running",
            label=label,
            created_at=now,
            updated_at=now,
            config=config or {},
            summary={},
        )
        self._write_run_record(record)
        return record

    def update_run(
        self,
        run_id: str,
        status: str | None = None,
        summary: dict[str, Any] | None = None,
        label: str | None = None,
    ) -> RunRecord:
        """Update run status/summary/label and bump ``updated_at``."""
        record = self.get_run(run_id)
        if status is not None and status not in VALID_RUN_STATUSES:
            raise ValueError(f"Unknown run status: {status!r}")
        updated = RunRecord(
            run_id=record.run_id,
            kind=record.kind,
            status=status if status is not None else record.status,
            label=label if label is not None else record.label,
            created_at=record.created_at,
            updated_at=_utc_now_iso(),
            config=record.config,
            summary=summary if summary is not None else record.summary,
        )
        self._write_run_record(updated)
        return updated

    def append_design(
        self,
        run_id: str,
        source: str,
        params: dict[str, Any],
        metrics: dict[str, Any],
        score: float,
        feasible: bool,
        label: str = "",
        artifacts: dict[str, str] | None = None,
    ) -> DesignRecord:
        """Append one design record to the run's ``designs.jsonl``."""
        run_dir = self._run_dir(run_id)
        designs_path = run_dir / "designs.jsonl"
        seq = 0
        if designs_path.exists():
            with designs_path.open("r", encoding="utf-8") as handle:
                seq = sum(1 for line in handle if line.strip())
        record = DesignRecord(
            design_id=f"{run_id}-d{seq:04d}",
            run_id=run_id,
            source=source,
            label=label,
            created_at=_utc_now_iso(),
            params=params,
            metrics=metrics,
            score=float(score),
            feasible=bool(feasible),
            artifacts=artifacts or {},
        )
        with designs_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record)) + "\n")
        return record

    def artifact_path(self, run_id: str, name: str) -> Path:
        """Return the absolute path for a named artifact inside a run."""
        if "/" in name or "\\" in name or name.startswith("."):
            raise ValueError(f"Invalid artifact name: {name!r}")
        return self._run_dir(run_id) / "artifacts" / name

    # ----------------------------------------------------------------- reads

    def get_run(self, run_id: str) -> RunRecord:
        path = self._run_dir(run_id) / "run.json"
        with path.open("r", encoding="utf-8") as handle:
            return RunRecord.from_dict(json.load(handle))

    def list_runs(self, kind: str | None = None) -> list[RunRecord]:
        """List all runs, newest first."""
        records: list[RunRecord] = []
        if not self.root.exists():
            return records
        for run_json in sorted(self.root.glob("*/run.json")):
            try:
                with run_json.open("r", encoding="utf-8") as handle:
                    record = RunRecord.from_dict(json.load(handle))
            except (json.JSONDecodeError, KeyError, OSError):
                continue
            if kind is None or record.kind == kind:
                records.append(record)
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    def list_designs(
        self,
        run_id: str | None = None,
        feasible_only: bool = False,
        sort_by: str = "score",
        descending: bool = True,
        limit: int | None = None,
    ) -> list[DesignRecord]:
        """List designs across one or all runs, ranked by ``sort_by``.

        ``sort_by`` may be "score" or any numeric metric key; designs missing
        the key sort last.
        """
        run_ids = [run_id] if run_id else [r.run_id for r in self.list_runs()]
        designs: list[DesignRecord] = []
        for rid in run_ids:
            path = self._run_dir(rid) / "designs.jsonl"
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = DesignRecord.from_dict(json.loads(line))
                    except (json.JSONDecodeError, KeyError):
                        continue
                    if feasible_only and not record.feasible:
                        continue
                    designs.append(record)

        def sort_key(record: DesignRecord) -> tuple[int, float]:
            if sort_by == "score":
                return (0, record.score)
            value = record.metrics.get(sort_by)
            if isinstance(value, (int, float)):
                return (0, float(value))
            return (1, 0.0) if descending else (1, float("inf"))

        designs.sort(key=sort_key, reverse=descending)
        if limit is not None:
            designs = designs[: max(limit, 0)]
        return designs

    def get_design(self, design_id: str) -> DesignRecord:
        """Look up a single design by its globally unique ID."""
        run_id, _, _ = design_id.rpartition("-d")
        if not run_id:
            raise KeyError(f"Malformed design_id: {design_id!r}")
        for record in self.list_designs(run_id=run_id, descending=False):
            if record.design_id == design_id:
                return record
        raise KeyError(f"Design not found: {design_id!r}")

    # -------------------------------------------------------------- internal

    def _run_dir(self, run_id: str) -> Path:
        if "/" in run_id or "\\" in run_id or run_id.startswith("."):
            raise ValueError(f"Invalid run_id: {run_id!r}")
        run_dir = self.root / run_id
        if not run_dir.is_dir():
            raise KeyError(f"Unknown run: {run_id!r}")
        return run_dir

    def _write_run_record(self, record: RunRecord) -> None:
        path = self.root / record.run_id / "run.json"
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(asdict(record), handle, indent=2)
        tmp.replace(path)
