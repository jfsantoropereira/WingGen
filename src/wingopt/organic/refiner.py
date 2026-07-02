"""Evolutionary organic wing refinement (pass 2)."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from math import atan, degrees, radians, tan
from pathlib import Path

from wingopt.config.models import GeometryConfig, WingGenConfig
from wingopt.geometry.airfoil import AirfoilData, load_airfoil_library
from wingopt.geometry.planform import compute_planform
from wingopt.optimizer.wing_optimizer import WingCandidate
from wingopt.organic.cfd_engine import CfdEvaluation, build_cfd_engine


@dataclass(frozen=True)
class DihedralControlPoint:
    """One control point in normalized semi-span coordinates."""

    eta: float
    angle_deg: float


@dataclass(frozen=True)
class OrganicCandidate:
    """Evaluated pass-2 candidate."""

    dihedral_profile: tuple[DihedralControlPoint, ...]
    effective_dihedral_deg: float
    score: float
    feasible: bool
    source: str
    drag_coefficient: float
    lift_to_drag: float
    trim_elevon_deg: float
    static_margin: float
    lateral_stability_index: float


@dataclass(frozen=True)
class OrganicGenerationSummary:
    """Evolution summary for one generation."""

    generation: int
    feasible_count: int
    best_score: float
    best_effective_dihedral_deg: float


@dataclass(frozen=True)
class OrganicRefinementResult:
    """Output of pass-2 organic refinement."""

    baseline_dihedral_deg: float
    best_candidate: OrganicCandidate
    generations: tuple[OrganicGenerationSummary, ...]


class OrganicRefiner:
    """Pass-2 evolutionary refinement for non-constant dihedral."""

    def __init__(self, config: WingGenConfig, data_dir: str | Path = "data") -> None:
        self.config = config
        self.data_dir = Path(data_dir)
        self.airfoils: dict[str, AirfoilData] = load_airfoil_library(
            self.data_dir / "airfoils",
            candidates=self.config.geometry.airfoil_candidates,
        )
        self.cfd_engine = build_cfd_engine(config)

    def refine(
        self,
        wing: WingCandidate,
        progress_hook: Callable[[OrganicGenerationSummary], None] | None = None,
    ) -> OrganicRefinementResult:
        """Run pass-2 local organic refinement for a baseline wing candidate."""

        profile_cfg = self.config.organic_refinement.dihedral_profile
        etas = profile_cfg.eta_control_points
        rng = random.Random(self.config.organic_refinement.seed)
        low = profile_cfg.angle_bounds_deg.minimum
        high = profile_cfg.angle_bounds_deg.maximum

        baseline = tuple(wing.dihedral_deg for _ in etas)
        population = [baseline]
        while len(population) < self.config.organic_refinement.population_size:
            population.append(
                self._mutate(
                    parent=baseline,
                    baseline=baseline,
                    rng=rng,
                    mutation_scale=0.25 * (high - low),
                )
            )

        all_candidates: list[OrganicCandidate] = []
        history: list[OrganicGenerationSummary] = []
        elite_count = self.config.organic_refinement.elite_count

        for generation in range(1, self.config.organic_refinement.generations + 1):
            evaluated = [self._evaluate(wing=wing, etas=etas, angles=angles) for angles in population]
            evaluated = sorted(evaluated, key=self._rank_key, reverse=True)
            all_candidates.extend(evaluated)

            best = evaluated[0]
            feasible_count = sum(1 for candidate in evaluated if candidate.feasible)
            history.append(
                OrganicGenerationSummary(
                    generation=generation,
                    feasible_count=feasible_count,
                    best_score=best.score,
                    best_effective_dihedral_deg=best.effective_dihedral_deg,
                )
            )
            if progress_hook is not None:
                progress_hook(history[-1])

            if generation == self.config.organic_refinement.generations:
                break

            mating_pool = evaluated[: max(3, len(evaluated) // 2)]
            next_population: list[tuple[float, ...]] = [
                tuple(point.angle_deg for point in candidate.dihedral_profile)
                for candidate in mating_pool[:elite_count]
            ]
            while len(next_population) < self.config.organic_refinement.population_size:
                parent_a = rng.choice(mating_pool)
                parent_b = rng.choice(mating_pool)
                child = self._crossover(
                    a=tuple(point.angle_deg for point in parent_a.dihedral_profile),
                    b=tuple(point.angle_deg for point in parent_b.dihedral_profile),
                    baseline=baseline,
                    rng=rng,
                )
                child = self._mutate(
                    parent=child,
                    baseline=baseline,
                    rng=rng,
                    mutation_scale=0.15 * (high - low),
                )
                next_population.append(child)
            population = next_population

        best_candidate = max(all_candidates, key=self._rank_key)
        return OrganicRefinementResult(
            baseline_dihedral_deg=wing.dihedral_deg,
            best_candidate=best_candidate,
            generations=tuple(history),
        )

    def _evaluate(
        self,
        wing: WingCandidate,
        etas: tuple[float, ...],
        angles: tuple[float, ...],
    ) -> OrganicCandidate:
        profile = tuple(
            DihedralControlPoint(eta=eta, angle_deg=angle_deg)
            for eta, angle_deg in zip(etas, angles)
        )
        dihedral_profile = tuple((point.eta, point.angle_deg) for point in profile)
        effective_dihedral = self._effective_dihedral_deg(dihedral_profile)

        geometry_cfg = GeometryConfig(
            wingspan_m=wing.wingspan_m,
            root_chord_m=wing.root_chord_m,
            tip_chord_m=wing.tip_chord_m,
            sweep_deg=wing.sweep_deg,
            dihedral_deg=effective_dihedral,
            root_incidence_deg=wing.root_incidence_deg,
            tip_incidence_deg=wing.tip_incidence_deg,
            airfoil=wing.airfoil,
            airfoil_candidates=self.config.geometry.airfoil_candidates,
            elevons=self.config.geometry.elevons,
        )
        geometry = compute_planform(geometry_cfg)
        airfoil = self.airfoils[wing.airfoil]

        eval_result: CfdEvaluation = self.cfd_engine.evaluate(
            geometry=geometry,
            airfoil=airfoil,
            cg_fraction_mac=wing.cg_fraction_mac,
            gross_mass_g=wing.total_mass_g,
            dihedral_profile=dihedral_profile,
        )

        smoothness = self._smoothness_penalty(angles)
        smoothness_weight = self.config.organic_refinement.dihedral_profile.smoothness_weight
        score = (
            eval_result.lift_to_drag * 25.0
            - eval_result.drag_coefficient * 180.0
            - abs(eval_result.trim_elevon_deg) * 1.2
            + eval_result.static_margin * 120.0
            + eval_result.lateral_stability_index * 20.0
            - smoothness_weight * smoothness * 25.0
        )
        feasible = eval_result.feasible and eval_result.static_margin >= self.config.stability.min_static_margin
        if not feasible:
            score -= 1000.0

        return OrganicCandidate(
            dihedral_profile=profile,
            effective_dihedral_deg=effective_dihedral,
            score=score,
            feasible=feasible,
            source=eval_result.source,
            drag_coefficient=eval_result.drag_coefficient,
            lift_to_drag=eval_result.lift_to_drag,
            trim_elevon_deg=eval_result.trim_elevon_deg,
            static_margin=eval_result.static_margin,
            lateral_stability_index=eval_result.lateral_stability_index,
        )

    def _crossover(
        self,
        a: tuple[float, ...],
        b: tuple[float, ...],
        baseline: tuple[float, ...],
        rng: random.Random,
    ) -> tuple[float, ...]:
        child: list[float] = []
        for idx, (left, right) in enumerate(zip(a, b)):
            if idx == 0:
                child.append(self._clamp_root(left, baseline[0]))
                continue
            if rng.random() < self.config.organic_refinement.crossover_rate:
                weight = rng.random()
                value = left * weight + right * (1.0 - weight)
            else:
                value = left
            child.append(self._clamp_angle(value))
        return tuple(child)

    def _mutate(
        self,
        parent: tuple[float, ...],
        baseline: tuple[float, ...],
        rng: random.Random,
        mutation_scale: float,
    ) -> tuple[float, ...]:
        mutated: list[float] = []
        for idx, value in enumerate(parent):
            if idx == 0:
                mutated.append(self._clamp_root(value, baseline[0]))
                continue
            if rng.random() < self.config.organic_refinement.mutation_rate:
                value += rng.uniform(-mutation_scale, mutation_scale)
            mutated.append(self._clamp_angle(value))
        return tuple(mutated)

    def _clamp_angle(self, value: float) -> float:
        bounds = self.config.organic_refinement.dihedral_profile.angle_bounds_deg
        return max(bounds.minimum, min(bounds.maximum, value))

    def _clamp_root(self, value: float, baseline_root: float) -> float:
        lock = self.config.organic_refinement.dihedral_profile.root_lock_deg
        low = baseline_root - lock
        high = baseline_root + lock
        return self._clamp_angle(max(low, min(high, value)))

    @staticmethod
    def _smoothness_penalty(angles: tuple[float, ...]) -> float:
        if len(angles) < 3:
            return 0.0
        value = 0.0
        for i in range(1, len(angles) - 1):
            second_diff = angles[i + 1] - 2.0 * angles[i] + angles[i - 1]
            value += second_diff * second_diff
        return value

    @staticmethod
    def _effective_dihedral_deg(profile: tuple[tuple[float, float], ...]) -> float:
        ordered = sorted(profile, key=lambda item: item[0])
        eta_points = tuple(item[0] for item in ordered)
        angle_points = tuple(item[1] for item in ordered)
        samples = 401
        eta_grid = [i / (samples - 1) for i in range(samples)]
        integral = 0.0
        prev_eta = eta_grid[0]
        prev_slope = tan(radians(OrganicRefiner._interp_linear(eta_points, angle_points, prev_eta)))
        for eta in eta_grid[1:]:
            slope = tan(radians(OrganicRefiner._interp_linear(eta_points, angle_points, eta)))
            d_eta = eta - prev_eta
            integral += 0.5 * d_eta * (prev_slope + slope)
            prev_eta = eta
            prev_slope = slope
        return degrees(atan(integral))

    @staticmethod
    def _interp_linear(
        x_points: tuple[float, ...],
        y_points: tuple[float, ...],
        x: float,
    ) -> float:
        if x <= x_points[0]:
            return y_points[0]
        if x >= x_points[-1]:
            return y_points[-1]
        for i in range(len(x_points) - 1):
            x0 = x_points[i]
            x1 = x_points[i + 1]
            if x0 <= x <= x1:
                y0 = y_points[i]
                y1 = y_points[i + 1]
                if x1 == x0:
                    return y0
                t = (x - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)
        return y_points[-1]

    @staticmethod
    def _rank_key(candidate: OrganicCandidate) -> tuple[int, float]:
        return (1 if candidate.feasible else 0, candidate.score)
