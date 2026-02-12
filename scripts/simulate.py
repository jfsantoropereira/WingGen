#!/usr/bin/env python3
"""Structured simulation runner for orchestration/UI."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass, replace
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wingopt.config import ConfigError, load_config
from wingopt.config.models import GeometryConfig
from wingopt.geometry.airfoil import load_airfoil_coordinates
from wingopt.geometry.planform import compute_planform
from wingopt.optimizer import OptimizationCoordinator
from wingopt.viz import export_wing_stl

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


def _export_best_design_stl(config, wing, data_dir: Path, stl_out: Path) -> Path:
    geometry_cfg = GeometryConfig(
        wingspan_m=wing.wingspan_m,
        root_chord_m=wing.root_chord_m,
        tip_chord_m=wing.tip_chord_m,
        sweep_deg=wing.sweep_deg,
        dihedral_deg=wing.dihedral_deg,
        twist_deg=wing.twist_deg,
        airfoil=wing.airfoil,
        airfoil_candidates=config.geometry.airfoil_candidates,
        elevons=config.geometry.elevons,
    )
    geometry = compute_planform(geometry_cfg)
    _, airfoil_coords = load_airfoil_coordinates(
        data_dir / "airfoils" / "coordinates" / f"{wing.airfoil}.dat"
    )
    return export_wing_stl(
        geometry=geometry,
        airfoil_coordinates=airfoil_coords,
        output_path=stl_out,
    )


def run(
    config_path: Path,
    data_dir: Path,
    stl_out: Path,
    ndjson: bool = True,
    cruise_speed_override_kmh: float | None = None,
    payload_override_g: float | None = None,
) -> int:
    emit_event("progress", {"stage": "load_config", "percent": 5}, ndjson)
    try:
        config = load_config(config_path)
    except (FileNotFoundError, ConfigError, ValueError) as exc:
        emit_event("error", {"message": str(exc), "stage": "load_config"}, ndjson)
        return 2

    if cruise_speed_override_kmh is not None:
        requested_cruise = float(cruise_speed_override_kmh)
        max_safe_cruise = config.mission.max_speed_kmh - 0.1
        if max_safe_cruise <= 0:
            emit_event(
                "error",
                {"message": "Mission max_speed_kmh must be > 0.1 for cruise override", "stage": "load_config"},
                ndjson,
            )
            return 2
        clamped_cruise = min(requested_cruise, max_safe_cruise)
        clamped_cruise = max(clamped_cruise, 1.0)
        if abs(clamped_cruise - requested_cruise) > 1e-9:
            emit_event(
                "progress",
                {
                    "stage": "normalize_inputs",
                    "percent": 8,
                    "note": (
                        f"Requested cruise {requested_cruise:.2f} km/h exceeded "
                        f"max-safe {max_safe_cruise:.2f}; clamped to {clamped_cruise:.2f}"
                    ),
                },
                ndjson,
            )
        config = replace(
            config,
            mission=replace(config.mission, cruise_speed_kmh=clamped_cruise),
        )

    if payload_override_g is not None:
        config = replace(
            config,
            components=replace(config.components, payload_weight_g=float(payload_override_g)),
        )

    emit_event("progress", {"stage": "initialize_coordinator", "percent": 20}, ndjson)
    coordinator = OptimizationCoordinator(config=config, data_dir=str(data_dir))

    try:
        emit_event("progress", {"stage": "run_optimization", "percent": 45}, ndjson)
        result = coordinator.run()
        emit_event("progress", {"stage": "finalize", "percent": 95}, ndjson)
    except Exception as exc:
        emit_event("error", {"message": str(exc), "stage": "run_optimization"}, ndjson)
        return 3

    emit_event("progress", {"stage": "export_stl", "percent": 97}, ndjson)
    try:
        stl_path = _export_best_design_stl(
            config,
            result.best_design.wing,
            data_dir=data_dir,
            stl_out=stl_out,
        )
    except Exception as exc:
        emit_event("error", {"message": str(exc), "stage": "export_stl"}, ndjson)
        return 4

    emit_event(
        "result",
        {
            "best_design": _to_jsonable(result.best_design),
            "iterations": _to_jsonable(result.iterations),
            "top_wing_candidates": _to_jsonable(result.wing_candidates[:5]),
            "top_propulsion_candidates": _to_jsonable(result.propulsion_candidates[:5]),
            "airfoil_comparison": _to_jsonable(result.airfoil_comparison),
            "artifacts": {"stl_file": str(stl_path)},
        },
        ndjson,
    )
    emit_event("progress", {"stage": "complete", "percent": 100}, ndjson)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run WingGen optimization")
    parser.add_argument("--config", type=Path, default=Path("configs/default_wing.toml"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--stl-out", type=Path, default=Path("outputs/best_wing.stl"))
    parser.add_argument("--override-mission-cruise-kmh", type=float, default=None)
    parser.add_argument("--override-payload-g", type=float, default=None)
    parser.add_argument("--json", action="store_true", help="Emit pretty JSON events instead of NDJSON")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run(
        config_path=args.config,
        data_dir=args.data_dir,
        stl_out=args.stl_out,
        ndjson=not args.json,
        cruise_speed_override_kmh=args.override_mission_cruise_kmh,
        payload_override_g=args.override_payload_g,
    )


if __name__ == "__main__":
    raise SystemExit(main())
