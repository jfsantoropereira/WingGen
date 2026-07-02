#!/usr/bin/env python3
"""Parameter-sweep / bounded-optimize runner (NDJSON event contract v1.1)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wingopt.config import ConfigError, load_config  # noqa: E402
from wingopt.store import RunStore  # noqa: E402
from wingopt.sweeps import (  # noqa: E402
    OptimizeSpec,
    SpecError,
    SweepRunner,
    SweepSpec,
    parse_spec,
    validate_sweep_against_config,
)

CONTRACT_VERSION = "1.1.0"

EXIT_OK = 0
EXIT_SPEC_ERROR = 2
EXIT_RUNTIME_ERROR = 3


def emit_event(event_type: str, payload: dict[str, Any], ndjson: bool) -> None:
    """Write one contract event to stdout (NDJSON or pretty JSON)."""
    event = {
        "contract_version": CONTRACT_VERSION,
        "event": event_type,
        "payload": payload,
    }
    if ndjson:
        sys.stdout.write(json.dumps(event) + "\n")
    else:
        sys.stdout.write(json.dumps(event, indent=2) + "\n")
    sys.stdout.flush()


def _load_spec(spec_path: Path) -> tuple[dict[str, Any], SweepSpec | OptimizeSpec]:
    try:
        raw = json.loads(spec_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SpecError(f"Cannot read spec file {spec_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SpecError(f"Spec file {spec_path} is not valid JSON: {exc}") from exc
    return raw, parse_spec(raw)


def _mark_run_failed(store: RunStore, run_id: str, message: str) -> None:
    try:
        store.update_run(run_id, status="failed", summary={"error": message})
    except (KeyError, ValueError, OSError):
        pass


def run(
    config_path: Path,
    spec_path: Path,
    runs_root: Path,
    run_id: str | None,
    label: str,
    data_dir: Path,
    ndjson: bool = True,
) -> int:
    """Execute the sweep/optimize job; returns the process exit code."""
    emit_event("progress", {"stage": "load_config", "percent": 2}, ndjson)
    try:
        config = load_config(config_path)
    except (FileNotFoundError, ConfigError, ValueError) as exc:
        emit_event("error", {"message": str(exc), "stage": "load_config"}, ndjson)
        return EXIT_SPEC_ERROR

    store = RunStore(runs_root)
    emit_event("progress", {"stage": "load_spec", "percent": 5}, ndjson)
    try:
        spec_raw, spec = _load_spec(spec_path)
        if isinstance(spec, SweepSpec):
            validate_sweep_against_config(spec, config)
    except SpecError as exc:
        emit_event("error", {"message": str(exc), "stage": "load_spec"}, ndjson)
        if run_id:
            _mark_run_failed(store, run_id, str(exc))
        return EXIT_SPEC_ERROR

    kind = spec.kind
    try:
        try:
            run_record = store.get_run(run_id) if run_id else None
        except KeyError:
            run_record = None
        if run_record is None:
            run_record = store.create_run(
                kind,
                label=label,
                config={"config_path": str(config_path), "spec": spec_raw},
                run_id=run_id,
            )
    except (OSError, ValueError, FileExistsError) as exc:
        emit_event("error", {"message": str(exc), "stage": "attach_run"}, ndjson)
        return EXIT_RUNTIME_ERROR

    emit_event(
        "run_info",
        {"run_id": run_record.run_id, "kind": kind, "label": run_record.label or label},
        ndjson,
    )

    def emit(event_type: str, payload: dict[str, Any]) -> None:
        emit_event(event_type, payload, ndjson)

    runner = SweepRunner(
        config=config,
        data_dir=data_dir,
        store=store,
        run_id=run_record.run_id,
        emit=emit,
    )
    try:
        if isinstance(spec, SweepSpec):
            payload, summary = runner.run_sweep(spec)
        else:
            payload, summary = runner.run_optimize(spec)
    except Exception as exc:
        _mark_run_failed(store, run_record.run_id, str(exc))
        emit_event("error", {"message": str(exc), "stage": "run"}, ndjson)
        return EXIT_RUNTIME_ERROR

    store.update_run(run_record.run_id, status="completed", summary=summary)
    emit_event("result", payload, ndjson)
    emit_event("progress", {"stage": "complete", "percent": 100}, ndjson)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run WingGen parameter sweep or bounded optimize")
    parser.add_argument("--config", type=Path, default=Path("configs/default_wing.toml"))
    parser.add_argument("--spec", type=Path, required=True, help="SweepSpec/OptimizeSpec JSON file")
    parser.add_argument("--runs-root", type=Path, default=Path("outputs/runs"))
    parser.add_argument("--run-id", type=str, default=None, help="Attach to existing run id")
    parser.add_argument("--label", type=str, default="")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--json", action="store_true", help="Emit pretty JSON events instead of NDJSON"
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run(
        config_path=args.config,
        spec_path=args.spec,
        runs_root=args.runs_root,
        run_id=args.run_id,
        label=args.label,
        data_dir=args.data_dir,
        ndjson=not args.json,
    )


if __name__ == "__main__":
    raise SystemExit(main())
