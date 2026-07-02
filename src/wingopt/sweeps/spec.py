"""SweepSpec / OptimizeSpec parsing and validation (STUDIO_CONTRACT.md section 6).

Specs are plain JSON documents. :func:`parse_spec` turns a parsed JSON dict
into a typed spec object, raising :class:`SpecError` with a precise message on
any contract violation (unknown parameter path root, ``min > max``,
``steps < 1``, grid cap exceeded, unknown objective, ...).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import prod
from typing import Any

from wingopt.config.models import ConfigError, WingGenConfig
from wingopt.sweeps.paths import ConfigPathError, set_config_value


class SpecError(ValueError):
    """Raised when a sweep/optimize spec is invalid."""


MAX_GRID_POINTS = 2000
VALID_MODES = ("wing_only", "full")
VALID_FIDELITIES = ("polar_llt", "vlm")
VALID_OBJECTIVES = ("combined_score", "range_km", "endurance_h", "cruise_ld")
FULL_MODE_OBJECTIVES = ("range_km", "endurance_h")
SWEEPABLE_ROOTS = frozenset(
    {
        "geometry",
        "mission",
        "environment",
        "components",
        "mass",
        "propulsion",
        "structure",
        "stability",
    }
)

# Dotted config paths accepted as bounded-optimize variables, mapped onto
# (design_space section, bound field) overrides in WingGenConfig.design_space.
OPTIMIZE_VARIABLE_MAP: dict[str, tuple[str, str]] = {
    "geometry.wingspan_m": ("wing", "wingspan_m"),
    "geometry.root_chord_m": ("wing", "root_chord_m"),
    "geometry.tip_chord_m": ("wing", "tip_chord_m"),
    "geometry.sweep_deg": ("wing", "sweep_deg"),
    "geometry.dihedral_deg": ("wing", "dihedral_deg"),
    "geometry.root_incidence_deg": ("wing", "root_incidence_deg"),
    "geometry.tip_incidence_deg": ("wing", "tip_incidence_deg"),
    "geometry.cg_fraction_mac": ("wing", "cg_fraction_mac"),
    "propulsion.prop.diameter_in": ("propulsion", "prop_diameter_in"),
    "propulsion.prop.pitch_in": ("propulsion", "prop_pitch_in"),
    "propulsion.battery.parallel": ("propulsion", "battery_parallel"),
    "environment.temperature_c": ("environment", "temperature_c"),
    "environment.altitude_m": ("environment", "altitude_m"),
    "components.payload_weight_g": ("environment", "payload_weight_g"),
}


@dataclass(frozen=True)
class ParameterAxis:
    """One sweep axis: a dotted config path and its explicit grid values."""

    path: str
    values: tuple[Any, ...]


@dataclass(frozen=True)
class SweepSpec:
    """Validated grid-sweep specification."""

    axes: tuple[ParameterAxis, ...]
    mode: str = "wing_only"
    fidelity: str = "polar_llt"
    objective: str = "combined_score"
    kind: str = "sweep"

    @property
    def total_points(self) -> int:
        """Number of cartesian grid points."""
        return prod(len(axis.values) for axis in self.axes)


@dataclass(frozen=True)
class OptimizeSpec:
    """Validated bounded-optimization specification."""

    variables: dict[str, tuple[float, float]] = field(default_factory=dict)
    max_evaluations: int = 400
    objective: str = "combined_score"
    seed: int | None = None
    kind: str = "optimize"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _linspace(minimum: float, maximum: float, steps: int) -> tuple[float, ...]:
    if steps == 1:
        return (minimum,)
    span = maximum - minimum
    return tuple(minimum + span * i / (steps - 1) for i in range(steps))


def _parse_axis(raw: Any, index: int) -> ParameterAxis:
    if not isinstance(raw, dict):
        raise SpecError(f"parameters[{index}] must be an object")
    path = raw.get("path")
    if not isinstance(path, str) or not path:
        raise SpecError(f"parameters[{index}].path must be a non-empty string")
    root = path.split(".", 1)[0]
    if root not in SWEEPABLE_ROOTS:
        raise SpecError(
            f"Unknown parameter path {path!r}: root {root!r} is not sweepable "
            f"(allowed: {', '.join(sorted(SWEEPABLE_ROOTS))})"
        )

    has_values = "values" in raw
    has_range = any(key in raw for key in ("min", "max", "steps"))
    if has_values and has_range:
        raise SpecError(f"parameters[{index}] ({path}): give either 'values' or min/max/steps")
    if has_values:
        values = raw["values"]
        if not isinstance(values, list) or not values:
            raise SpecError(f"parameters[{index}] ({path}): 'values' must be a non-empty list")
        for value in values:
            if not (_is_number(value) or isinstance(value, str)):
                raise SpecError(
                    f"parameters[{index}] ({path}): values must be numbers or strings"
                )
        return ParameterAxis(path=path, values=tuple(values))
    if not has_range:
        raise SpecError(f"parameters[{index}] ({path}): give either 'values' or min/max/steps")

    missing = [key for key in ("min", "max", "steps") if key not in raw]
    if missing:
        raise SpecError(f"parameters[{index}] ({path}): missing {', '.join(missing)}")
    minimum, maximum, steps = raw["min"], raw["max"], raw["steps"]
    if not (_is_number(minimum) and _is_number(maximum)):
        raise SpecError(f"parameters[{index}] ({path}): min/max must be numbers")
    if not isinstance(steps, int) or isinstance(steps, bool) or steps < 1:
        raise SpecError(f"parameters[{index}] ({path}): steps must be an integer >= 1")
    if minimum > maximum:
        raise SpecError(f"parameters[{index}] ({path}): min ({minimum}) > max ({maximum})")
    return ParameterAxis(path=path, values=_linspace(float(minimum), float(maximum), steps))


def _parse_sweep(raw: dict[str, Any]) -> SweepSpec:
    parameters = raw.get("parameters")
    if not isinstance(parameters, list) or not parameters:
        raise SpecError("sweep spec requires a non-empty 'parameters' list")
    axes = tuple(_parse_axis(item, i) for i, item in enumerate(parameters))
    seen: set[str] = set()
    for axis in axes:
        if axis.path in seen:
            raise SpecError(f"Duplicate parameter path {axis.path!r}")
        seen.add(axis.path)

    total = prod(len(axis.values) for axis in axes)
    if total > MAX_GRID_POINTS:
        raise SpecError(
            f"Grid has {total} points, exceeding the hard cap of {MAX_GRID_POINTS}"
        )

    evaluation = raw.get("evaluation", {})
    if not isinstance(evaluation, dict):
        raise SpecError("'evaluation' must be an object")
    mode = evaluation.get("mode", "wing_only")
    if mode not in VALID_MODES:
        raise SpecError(f"evaluation.mode must be one of {VALID_MODES}, got {mode!r}")
    fidelity = evaluation.get("fidelity", "polar_llt")
    if fidelity not in VALID_FIDELITIES:
        raise SpecError(f"evaluation.fidelity must be one of {VALID_FIDELITIES}, got {fidelity!r}")

    objective = raw.get("objective", "combined_score")
    if objective not in VALID_OBJECTIVES:
        raise SpecError(f"objective must be one of {VALID_OBJECTIVES}, got {objective!r}")
    if mode == "wing_only" and objective in FULL_MODE_OBJECTIVES:
        raise SpecError(
            f"objective {objective!r} requires evaluation.mode 'full' (got 'wing_only')"
        )

    return SweepSpec(axes=axes, mode=mode, fidelity=fidelity, objective=objective)


def _parse_optimize(raw: dict[str, Any]) -> OptimizeSpec:
    variables_raw = raw.get("variables")
    if not isinstance(variables_raw, dict) or not variables_raw:
        raise SpecError("optimize spec requires a non-empty 'variables' object")
    variables: dict[str, tuple[float, float]] = {}
    for path, bounds in variables_raw.items():
        if path not in OPTIMIZE_VARIABLE_MAP:
            raise SpecError(
                f"Unknown optimize variable {path!r} "
                f"(supported: {', '.join(sorted(OPTIMIZE_VARIABLE_MAP))})"
            )
        if (
            not isinstance(bounds, (list, tuple))
            or len(bounds) != 2
            or not all(_is_number(b) for b in bounds)
        ):
            raise SpecError(f"variables[{path!r}] must be [min, max]")
        minimum, maximum = float(bounds[0]), float(bounds[1])
        if minimum > maximum:
            raise SpecError(f"variables[{path!r}]: min ({minimum}) > max ({maximum})")
        variables[path] = (minimum, maximum)

    budget = raw.get("budget")
    if not isinstance(budget, dict) or "max_evaluations" not in budget:
        raise SpecError("optimize spec requires budget.max_evaluations")
    max_evaluations = budget["max_evaluations"]
    if not isinstance(max_evaluations, int) or isinstance(max_evaluations, bool):
        raise SpecError("budget.max_evaluations must be an integer >= 1")
    if max_evaluations < 1:
        raise SpecError("budget.max_evaluations must be an integer >= 1")

    objective = raw.get("objective", "combined_score")
    if objective not in VALID_OBJECTIVES:
        raise SpecError(f"objective must be one of {VALID_OBJECTIVES}, got {objective!r}")

    seed = raw.get("seed")
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
        raise SpecError("seed must be an integer")

    return OptimizeSpec(
        variables=variables,
        max_evaluations=max_evaluations,
        objective=objective,
        seed=seed,
    )


def parse_spec(raw: Any) -> SweepSpec | OptimizeSpec:
    """Parse a JSON spec document into a validated spec object.

    Raises:
        SpecError: On any structural or semantic violation.
    """
    if not isinstance(raw, dict):
        raise SpecError("Spec must be a JSON object")
    kind = raw.get("kind")
    if kind == "sweep":
        return _parse_sweep(raw)
    if kind == "optimize":
        return _parse_optimize(raw)
    raise SpecError(f"spec.kind must be 'sweep' or 'optimize', got {kind!r}")


def validate_sweep_against_config(spec: SweepSpec, config: WingGenConfig) -> None:
    """Check that every axis path applies cleanly to the given config.

    Applies the first grid value of each axis to a scratch copy of the config
    so that unknown fields and type mismatches fail before any evaluation.
    ``geometry.airfoil`` values must come from ``geometry.airfoil_candidates``.

    Raises:
        SpecError: If a path is unknown/not a leaf, or an airfoil value is
            outside the configured candidate list.
    """
    for axis in spec.axes:
        if axis.path == "geometry.airfoil":
            for value in axis.values:
                if value not in config.geometry.airfoil_candidates:
                    raise SpecError(
                        f"geometry.airfoil value {value!r} is not in airfoil_candidates "
                        f"{list(config.geometry.airfoil_candidates)}"
                    )
        try:
            set_config_value(config, axis.path, axis.values[0])
        except ConfigPathError as exc:
            raise SpecError(str(exc)) from exc
        except ConfigError:
            # Domain violations (e.g. out-of-range values) are legitimate grid
            # points; they surface per point as infeasible during the sweep.
            continue
