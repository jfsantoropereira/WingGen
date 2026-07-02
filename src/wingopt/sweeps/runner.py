"""Grid-sweep and bounded-optimize engines persisting to the run store.

Reuses the existing physics stack: :class:`WingOptimizer` evaluates the
configured geometry as a single candidate (``wing_only``), and
:class:`PropulsionOptimizer` matches the configured motor/prop/battery for
``full`` mode. Every evaluated point is persisted through
:class:`wingopt.store.RunStore` and mirrored on the NDJSON event stream via an
``emit(event, payload)`` callback supplied by the CLI.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from wingopt.config.models import BoundRange, ConfigError, WingGenConfig
from wingopt.optimizer.coordinator import OptimizationCoordinator
from wingopt.optimizer.propulsion_optimizer import PropulsionCandidate, PropulsionOptimizer
from wingopt.optimizer.wing_optimizer import WingCandidate, WingOptimizer
from wingopt.store import DesignRecord, RunStore
from wingopt.sweeps.paths import apply_parameters
from wingopt.sweeps.spec import OPTIMIZE_VARIABLE_MAP, OptimizeSpec, SweepSpec

EmitFn = Callable[[str, dict[str, Any]], None]

#: Score assigned to points whose evaluation failed (JSON-safe stand-in for -inf).
FAILED_POINT_SCORE = -1.0e9

_TOP_DESIGNS = 10


def _vlm_stub_note(spec_fidelity: str, emit: EmitFn) -> None:
    if spec_fidelity == "vlm":
        emit(
            "progress",
            {
                "stage": "evaluate",
                "percent": 10,
                "note": "vlm fidelity not yet integrated; using polar_llt",
            },
        )


def _wing_metrics(wing: WingCandidate) -> dict[str, float]:
    return {
        "cruise_ld": wing.cruise_ld,
        "cruise_cd": wing.cruise_cd,
        "static_margin": wing.static_margin,
        "stall_speed_kmh": wing.stall_speed_kmh,
        "total_mass_g": wing.total_mass_g,
        "wing_area_m2": wing.wing_area_m2,
        "aspect_ratio": wing.aspect_ratio,
        "wing_loading_gdm2": wing.wing_loading_gdm2,
        "trim_elevon_deg": wing.trim_elevon_deg,
        "lateral_stability_index": wing.lateral_stability_index,
        "structural_mass_g": wing.structural_mass_g,
        "wing_score": wing.score,
    }


def _propulsion_metrics(prop: PropulsionCandidate) -> dict[str, float]:
    return {
        "range_km": prop.weighted_range_km,
        "endurance_h": prop.weighted_endurance_h,
        "worst_case_range_km": prop.worst_case_range_km,
        "gross_mass_g": prop.gross_mass_g,
        "cruise_current_a": prop.cruise_current_a,
        "cruise_throttle": prop.cruise_throttle,
        "propulsion_score": prop.score,
    }


def _design_payload(record: DesignRecord) -> dict[str, Any]:
    return asdict(record)


class SweepRunner:
    """Executes sweep/optimize specs against a config, persisting each design.

    One instance owns one store run directory for the duration of the job.
    Wing/propulsion optimizer instances are created once (airfoil polars are
    loaded eagerly) and re-pointed at each per-point config, which only swaps
    frozen dataclass references.
    """

    def __init__(
        self,
        config: WingGenConfig,
        data_dir: Path,
        store: RunStore,
        run_id: str,
        emit: EmitFn,
    ) -> None:
        self.config = config
        self.data_dir = Path(data_dir)
        self.store = store
        self.run_id = run_id
        self.emit = emit
        self._wing_optimizer: WingOptimizer | None = None
        self._prop_optimizer: PropulsionOptimizer | None = None

    # ------------------------------------------------------------------ sweep

    def run_sweep(self, spec: SweepSpec) -> tuple[dict[str, Any], dict[str, Any]]:
        """Run the cartesian grid sweep.

        Returns:
            ``(result_payload, run_summary)`` — the contract ``result`` event
            payload and the summary persisted on the run record.
        """
        _vlm_stub_note(spec.fidelity, self.emit)
        total = spec.total_points
        progress_stride = max(1, total // 20)
        feasible_points = 0

        combos = itertools.product(*(axis.values for axis in spec.axes))
        for index, combo in enumerate(combos):
            params = {axis.path: value for axis, value in zip(spec.axes, combo, strict=True)}
            metrics, score, feasible, label = self._evaluate_point(params, spec)
            if feasible:
                feasible_points += 1
            record = self.store.append_design(
                self.run_id,
                source="sweep_point",
                params=params,
                metrics=metrics,
                score=score,
                feasible=feasible,
                label=label,
            )
            self.emit(
                "sweep_point",
                {
                    "index": index,
                    "total": total,
                    "params": params,
                    "metrics": metrics,
                    "score": score,
                    "feasible": feasible,
                    "design_id": record.design_id,
                },
            )
            if (index + 1) % progress_stride == 0 or index + 1 == total:
                self.emit(
                    "progress",
                    {
                        "stage": "sweep",
                        "percent": 10 + int(85.0 * (index + 1) / total),
                        "note": f"point {index + 1}/{total}",
                    },
                )

        ranked = self.store.list_designs(run_id=self.run_id, limit=_TOP_DESIGNS)
        best = ranked[0] if ranked else None
        payload = {
            "run_id": self.run_id,
            "total_points": total,
            "feasible_points": feasible_points,
            "best": _design_payload(best) if best else None,
            "top": [_design_payload(record) for record in ranked],
        }
        summary = {
            "total_points": total,
            "feasible_points": feasible_points,
            "best_score": best.score if best else None,
            "objective": spec.objective,
        }
        return payload, summary

    def _evaluate_point(
        self,
        params: dict[str, Any],
        spec: SweepSpec,
    ) -> tuple[dict[str, Any], float, bool, str]:
        """Evaluate one grid point; never raises for value-domain failures."""
        try:
            point_config = apply_parameters(self.config, params)
        except ConfigError as exc:
            return {}, FAILED_POINT_SCORE, False, f"config_error: {exc}"

        wing = self._evaluate_wing(point_config)
        if wing is None:
            return {}, FAILED_POINT_SCORE, False, "evaluation_failed: wing"

        metrics = _wing_metrics(wing)
        feasible = wing.feasible
        combined = wing.score

        if spec.mode == "full":
            prop = self._evaluate_propulsion(point_config, wing)
            if prop is None:
                return metrics, FAILED_POINT_SCORE, False, "evaluation_failed: propulsion"
            metrics.update(_propulsion_metrics(prop))
            feasible = wing.feasible and prop.feasible
            # Mirror the coordinator's coupled ranking weights.
            combined = 0.45 * wing.score + 0.55 * prop.score

        metrics["combined_score"] = combined
        score = float(metrics[spec.objective])
        return metrics, score, feasible, ""

    def _evaluate_wing(self, config: WingGenConfig) -> WingCandidate | None:
        """Evaluate the config's own geometry as a single wing candidate.

        Reuses ``WingOptimizer._evaluate_candidate`` (the same physics path as
        pass-1 optimization) on the configured geometry rather than a sampled
        one. The CG is placed at the midpoint of the configured
        ``design_space.wing.cg_fraction_mac`` range, since the config has no
        explicit CG location.
        """
        if self._wing_optimizer is None:
            self._wing_optimizer = WingOptimizer(config=config, data_dir=self.data_dir)
        optimizer = self._wing_optimizer
        optimizer.config = config

        geometry = config.geometry
        cg_bounds = config.design_space.wing.cg_fraction_mac
        wing_cfg = {
            "wingspan_m": geometry.wingspan_m,
            "root_chord_m": geometry.root_chord_m,
            "tip_chord_m": geometry.tip_chord_m,
            "sweep_deg": geometry.sweep_deg,
            "dihedral_deg": geometry.dihedral_deg,
            "root_incidence_deg": geometry.root_incidence_deg,
            "tip_incidence_deg": geometry.tip_incidence_deg,
            "cg_fraction_mac": 0.5 * (cg_bounds.minimum + cg_bounds.maximum),
        }
        return optimizer._evaluate_candidate(wing_cfg=wing_cfg, airfoil_name=geometry.airfoil)

    def _evaluate_propulsion(
        self,
        config: WingGenConfig,
        wing: WingCandidate,
    ) -> PropulsionCandidate | None:
        """Match the configured motor/prop/battery against the wing candidate."""
        if self._prop_optimizer is None:
            self._prop_optimizer = PropulsionOptimizer(config=config, data_dir=self.data_dir)
        optimizer = self._prop_optimizer
        optimizer.config = config
        prop_path = self.data_dir / "props" / config.propulsion.prop.data_file
        if not prop_path.exists():
            return None
        return optimizer._evaluate_combination(
            wing=wing,
            motor=config.propulsion.motor,
            prop_path=prop_path,
            battery_parallel=config.propulsion.battery.parallel,
        )

    # --------------------------------------------------------------- optimize

    def run_optimize(self, spec: OptimizeSpec) -> tuple[dict[str, Any], dict[str, Any]]:
        """Run the bounded optimization via the existing coordinator.

        Maps spec variables onto design-space bound overrides, scales the wing
        optimizer budget so the total wing evaluations across coupling
        iterations stay within ``spec.max_evaluations``, then persists the
        best coupled design plus the top wing candidates.
        """
        config = self._config_with_overrides(spec)
        iterations = config.optimizer.coordinator.max_coupling_iterations
        per_iteration = max(1, spec.max_evaluations // iterations)
        self.emit(
            "progress",
            {
                "stage": "optimize",
                "percent": 15,
                "note": (
                    f"budget {spec.max_evaluations} evaluations -> "
                    f"{per_iteration}/coupling iteration x {iterations}"
                ),
            },
        )

        coordinator = OptimizationCoordinator(config=config, data_dir=str(self.data_dir))
        result = coordinator.run()
        self.emit(
            "progress",
            {"stage": "optimize", "percent": 80, "note": "coordinator converged"},
        )

        prop_by_signature: dict[str, PropulsionCandidate] = {}
        for prop in result.propulsion_candidates:
            existing = prop_by_signature.get(prop.wing_signature)
            if existing is None or prop.score > existing.score:
                prop_by_signature[prop.wing_signature] = prop

        best_record = self._persist_optimize_design(
            result.best_design.wing,
            result.best_design.propulsion,
            spec.objective,
            label="best",
            combined_score=result.best_design.combined_score,
        )

        top_records: list[DesignRecord] = [best_record]
        best_wing = result.best_design.wing
        for wing in result.wing_candidates[:_TOP_DESIGNS]:
            if wing is best_wing:
                continue
            prop = prop_by_signature.get(_wing_signature(wing))
            top_records.append(
                self._persist_optimize_design(wing, prop, spec.objective, label="candidate")
            )

        payload = {
            "run_id": self.run_id,
            "objective": spec.objective,
            "evaluations_budget": spec.max_evaluations,
            "coupling_iterations": len(result.iterations),
            "best": _design_payload(best_record),
            "top": [_design_payload(record) for record in top_records[:_TOP_DESIGNS]],
        }
        summary = {
            "total_points": len(top_records),
            "feasible_points": sum(1 for record in top_records if record.feasible),
            "best_score": best_record.score,
            "objective": spec.objective,
        }
        return payload, summary

    def _config_with_overrides(self, spec: OptimizeSpec) -> WingGenConfig:
        """Return the config with design-space bounds and budget overridden."""
        design_space = self.config.design_space
        sections: dict[str, Any] = {
            "wing": design_space.wing,
            "propulsion": design_space.propulsion,
            "environment": design_space.environment,
        }
        for path, (minimum, maximum) in spec.variables.items():
            section_name, field_name = OPTIMIZE_VARIABLE_MAP[path]
            section = sections[section_name]
            sections[section_name] = replace(
                section, **{field_name: BoundRange(minimum, maximum)}
            )
        design_space = replace(
            design_space,
            wing=sections["wing"],
            propulsion=sections["propulsion"],
            environment=sections["environment"],
        )

        iterations = self.config.optimizer.coordinator.max_coupling_iterations
        per_iteration = max(1, spec.max_evaluations // iterations)
        wing_settings = replace(self.config.optimizer.wing, max_evaluations=per_iteration)
        prop_settings = replace(self.config.optimizer.propulsion, max_evaluations=per_iteration)
        if spec.seed is not None:
            wing_settings = replace(wing_settings, seed=spec.seed)
            prop_settings = replace(prop_settings, seed=spec.seed + 1)
        optimizer = replace(
            self.config.optimizer, wing=wing_settings, propulsion=prop_settings
        )
        return replace(self.config, design_space=design_space, optimizer=optimizer)

    def _persist_optimize_design(
        self,
        wing: WingCandidate,
        prop: PropulsionCandidate | None,
        objective: str,
        label: str,
        combined_score: float | None = None,
    ) -> DesignRecord:
        """Persist one optimize design (wing, optionally coupled) and emit ``design``."""
        params: dict[str, Any] = {
            "geometry.wingspan_m": wing.wingspan_m,
            "geometry.root_chord_m": wing.root_chord_m,
            "geometry.tip_chord_m": wing.tip_chord_m,
            "geometry.sweep_deg": wing.sweep_deg,
            "geometry.dihedral_deg": wing.dihedral_deg,
            "geometry.root_incidence_deg": wing.root_incidence_deg,
            "geometry.tip_incidence_deg": wing.tip_incidence_deg,
            "geometry.airfoil": wing.airfoil,
            "geometry.cg_fraction_mac": wing.cg_fraction_mac,
        }
        metrics = _wing_metrics(wing)
        feasible = wing.feasible
        if prop is not None:
            params["propulsion.battery.parallel"] = prop.battery_parallel
            params["propulsion.motor.name"] = prop.motor_name
            params["propulsion.prop.name"] = prop.prop_name
            metrics.update(_propulsion_metrics(prop))
            feasible = wing.feasible and prop.feasible
            if combined_score is None:
                combined_score = 0.45 * wing.score + 0.55 * prop.score
        if combined_score is None:
            combined_score = wing.score
        metrics["combined_score"] = combined_score

        objective_value = metrics.get(objective)
        score = float(objective_value) if objective_value is not None else FAILED_POINT_SCORE
        record = self.store.append_design(
            self.run_id,
            source="optimize",
            params=params,
            metrics=metrics,
            score=score,
            feasible=feasible,
            label=label,
        )
        self.emit(
            "design",
            {
                "design_id": record.design_id,
                "source": record.source,
                "score": record.score,
                "feasible": record.feasible,
            },
        )
        return record


def _wing_signature(wing: WingCandidate) -> str:
    """Wing identity key matching ``PropulsionOptimizer._wing_signature``."""
    return PropulsionOptimizer._wing_signature(wing)


__all__ = [
    "FAILED_POINT_SCORE",
    "EmitFn",
    "SweepRunner",
]
