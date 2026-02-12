#!/usr/bin/env python3
"""Mock external CFD runner contract for organic refinement integration tests.

This script emulates an external CFD engine by reading a JSON input payload
and writing the expected result JSON consumed by `ExternalCommandCfdEngine`.
"""

from __future__ import annotations

import argparse
import json
from math import sqrt
from pathlib import Path


def _weighted_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _curvature(values: list[float]) -> float:
    if len(values) < 3:
        return 0.0
    total = 0.0
    for i in range(1, len(values) - 1):
        total += abs(values[i + 1] - 2.0 * values[i] + values[i - 1])
    return total


def evaluate(input_payload: dict) -> dict:
    geometry = input_payload["geometry"]
    profile = input_payload["dihedral_profile"]
    angles = [float(point["angle_deg"]) for point in profile]
    mean_dihedral = _weighted_mean(angles)
    curvature = _curvature(angles)
    sweep = float(geometry["sweep_deg"])
    root_incidence = float(geometry["root_incidence_deg"])
    tip_incidence = float(geometry["tip_incidence_deg"])
    cg = float(input_payload["cg_fraction_mac"])

    base_cd = 0.0198 + 0.00005 * (mean_dihedral - 8.0) ** 2
    smoothness_penalty = 0.00010 * curvature
    sweep_penalty = 0.00002 * max(0.0, sweep - 30.0)
    cd = base_cd + smoothness_penalty + sweep_penalty

    ld = max(5.0, 10.5 - 55.0 * (cd - 0.02))
    trim = abs(root_incidence - tip_incidence) * 0.11 + max(0.0, cg - 0.25) * 7.0

    static_margin = 0.24 + 0.0018 * sweep - cg
    lateral_stability = 0.12 * mean_dihedral + 0.01 * sweep

    feasible = (
        static_margin >= 0.05
        and lateral_stability >= 0.50
        and trim <= 25.0
        and cd <= 0.06
    )

    return {
        "drag_coefficient": cd,
        "lift_to_drag": ld,
        "trim_elevon_deg": trim,
        "static_margin": static_margin,
        "lateral_stability_index": lateral_stability,
        "feasible": feasible,
        "solver_info": {
            "engine": input_payload.get("engine", "mock"),
            "cells": int(120_000 + 800 * curvature + 200 * abs(mean_dihedral)),
            "residual_l2": 1e-5 / sqrt(max(1.0, ld)),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mock external CFD runner")
    parser.add_argument("--engine", required=True)
    parser.add_argument("--input-json", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    payload = json.loads(args.input_json.read_text(encoding="utf-8"))
    payload["engine"] = args.engine
    result = evaluate(payload)

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
