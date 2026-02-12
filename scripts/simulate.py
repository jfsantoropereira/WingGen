#!/usr/bin/env python3
"""Structured simulation runner for orchestration/UI."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wingopt.config import ConfigError, load_config
from wingopt.optimizer import OptimizationCoordinator

CONTRACT_VERSION = "1.0.0"


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def emit_event(event_type: str, payload: dict[str, Any], ndjson: bool) -> None:
    event = {
        "contract_version": CONTRACT_VERSION,
        "event": event_type,
        "payload": payload,
    }
    if ndjson:
        sys.stdout.write(json.dumps(event) + "\n")
        sys.stdout.flush()
    else:
        sys.stdout.write(json.dumps(event, indent=2) + "\n")
        sys.stdout.flush()


def run(config_path: Path, data_dir: Path, ndjson: bool = True) -> int:
    emit_event("progress", {"stage": "load_config", "percent": 5}, ndjson)
    try:
        config = load_config(config_path)
    except (FileNotFoundError, ConfigError, ValueError) as exc:
        emit_event("error", {"message": str(exc), "stage": "load_config"}, ndjson)
        return 2

    emit_event("progress", {"stage": "initialize_coordinator", "percent": 20}, ndjson)
    coordinator = OptimizationCoordinator(config=config, data_dir=str(data_dir))

    try:
        emit_event("progress", {"stage": "run_optimization", "percent": 45}, ndjson)
        result = coordinator.run()
        emit_event("progress", {"stage": "finalize", "percent": 95}, ndjson)
    except Exception as exc:
        emit_event("error", {"message": str(exc), "stage": "run_optimization"}, ndjson)
        return 3

    emit_event(
        "result",
        {
            "best_design": _to_jsonable(result.best_design),
            "iterations": _to_jsonable(result.iterations),
            "top_wing_candidates": _to_jsonable(result.wing_candidates[:5]),
            "top_propulsion_candidates": _to_jsonable(result.propulsion_candidates[:5]),
        },
        ndjson,
    )
    emit_event("progress", {"stage": "complete", "percent": 100}, ndjson)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run WingGen optimization")
    parser.add_argument("--config", type=Path, default=Path("configs/default_wing.toml"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--json", action="store_true", help="Emit pretty JSON events instead of NDJSON")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run(config_path=args.config, data_dir=args.data_dir, ndjson=not args.json)


if __name__ == "__main__":
    raise SystemExit(main())
