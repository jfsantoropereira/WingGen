"""Coordinator coupling wing and propulsion optimizers."""

from __future__ import annotations

from dataclasses import dataclass

from wingopt.config.models import WingGenConfig
from wingopt.optimizer.propulsion_optimizer import PropulsionCandidate, PropulsionOptimizer
from wingopt.optimizer.wing_optimizer import AirfoilComparison, WingCandidate, WingOptimizer


@dataclass(frozen=True)
class CouplingIteration:
    """One coordinator iteration summary."""

    iteration: int
    best_combined_score: float
    best_weighted_range_km: float
    best_endurance_h: float


@dataclass(frozen=True)
class IntegratedDesign:
    """Coupled wing+propulsion winning design."""

    wing: WingCandidate
    propulsion: PropulsionCandidate
    combined_score: float


@dataclass(frozen=True)
class CoordinatorResult:
    """Coordinator output with history."""

    best_design: IntegratedDesign
    iterations: tuple[CouplingIteration, ...]
    wing_candidates: tuple[WingCandidate, ...]
    propulsion_candidates: tuple[PropulsionCandidate, ...]
    airfoil_comparison: tuple[AirfoilComparison, ...]


class OptimizationCoordinator:
    """High-level optimizer that couples separate wing and propulsion modules."""

    def __init__(self, config: WingGenConfig, data_dir: str = "data") -> None:
        self.config = config
        self.wing_optimizer = WingOptimizer(config=config, data_dir=data_dir)
        self.propulsion_optimizer = PropulsionOptimizer(config=config, data_dir=data_dir)

    def run(self) -> CoordinatorResult:
        """Run iterative coupling until convergence or max iterations."""

        max_iters = self.config.optimizer.coordinator.max_coupling_iterations
        tolerance = self.config.optimizer.coordinator.convergence_tolerance

        history: list[CouplingIteration] = []
        previous_best_range: float | None = None

        wing_candidates: tuple[WingCandidate, ...] = tuple()
        propulsion_candidates: tuple[PropulsionCandidate, ...] = tuple()
        airfoil_comparison: tuple[AirfoilComparison, ...] = tuple()
        best_design: IntegratedDesign | None = None

        for iteration in range(1, max_iters + 1):
            wing_candidates = self.wing_optimizer.optimize(top_k=10)
            airfoil_comparison = self.wing_optimizer.airfoil_comparison()
            propulsion_input = tuple(candidate for candidate in wing_candidates if candidate.feasible)
            if not propulsion_input:
                propulsion_input = wing_candidates
            propulsion_candidates = self.propulsion_optimizer.optimize_for_wings(propulsion_input, top_k=12)

            if not wing_candidates or not propulsion_candidates:
                raise RuntimeError("Optimization produced no valid candidates")

            best_design = self._select_best_pair(wing_candidates, propulsion_candidates)

            history.append(
                CouplingIteration(
                    iteration=iteration,
                    best_combined_score=best_design.combined_score,
                    best_weighted_range_km=best_design.propulsion.weighted_range_km,
                    best_endurance_h=best_design.propulsion.weighted_endurance_h,
                )
            )

            if previous_best_range is not None:
                delta = abs(best_design.propulsion.weighted_range_km - previous_best_range)
                denom = max(abs(previous_best_range), 1e-6)
                rel_change = delta / denom
                if rel_change <= tolerance:
                    break
            previous_best_range = best_design.propulsion.weighted_range_km

        if best_design is None:
            raise RuntimeError("Failed to produce integrated design")

        return CoordinatorResult(
            best_design=best_design,
            iterations=tuple(history),
            wing_candidates=wing_candidates,
            propulsion_candidates=propulsion_candidates,
            airfoil_comparison=airfoil_comparison,
        )

    @staticmethod
    def _select_best_pair(
        wings: tuple[WingCandidate, ...],
        props: tuple[PropulsionCandidate, ...],
    ) -> IntegratedDesign:
        wing_by_signature = {
            (
                f"{w.airfoil}|{w.wingspan_m:.4f}|{w.root_chord_m:.4f}|{w.tip_chord_m:.4f}|"
                f"{w.sweep_deg:.3f}|{w.dihedral_deg:.3f}|"
                f"{w.root_incidence_deg:.3f}|{w.tip_incidence_deg:.3f}|{w.cg_fraction_mac:.4f}"
            ): w
            for w in wings
        }

        best_prop = None
        best_score = float("-inf")
        best_feasible = None
        best_feasible_score = float("-inf")
        for prop in props:
            wing = wing_by_signature.get(prop.wing_signature)
            if wing is None:
                continue
            combined = 0.45 * wing.score + 0.55 * prop.score
            pair = (wing, prop)
            if wing.feasible and prop.feasible and combined > best_feasible_score:
                best_feasible_score = combined
                best_feasible = pair
            if combined > best_score:
                best_score = combined
                best_prop = pair

        if best_feasible is not None:
            wing, prop = best_feasible
            return IntegratedDesign(wing=wing, propulsion=prop, combined_score=best_feasible_score)

        if best_prop is None:
            # Fallback: simply pair top-ranked of each list
            top_wing = max(wings, key=lambda w: w.score)
            top_prop = max(props, key=lambda p: p.score)
            return IntegratedDesign(wing=top_wing, propulsion=top_prop, combined_score=0.5 * (top_wing.score + top_prop.score))

        wing, prop = best_prop
        return IntegratedDesign(wing=wing, propulsion=prop, combined_score=best_score)
