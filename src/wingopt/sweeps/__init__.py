"""Parameter-sweep and bounded-optimization engine (STUDIO_CONTRACT.md section 6)."""

from wingopt.sweeps.paths import ConfigPathError, apply_parameters, set_config_value
from wingopt.sweeps.runner import FAILED_POINT_SCORE, SweepRunner
from wingopt.sweeps.spec import (
    MAX_GRID_POINTS,
    OPTIMIZE_VARIABLE_MAP,
    OptimizeSpec,
    ParameterAxis,
    SpecError,
    SweepSpec,
    parse_spec,
    validate_sweep_against_config,
)

__all__ = [
    "FAILED_POINT_SCORE",
    "MAX_GRID_POINTS",
    "OPTIMIZE_VARIABLE_MAP",
    "ConfigPathError",
    "OptimizeSpec",
    "ParameterAxis",
    "SpecError",
    "SweepRunner",
    "SweepSpec",
    "apply_parameters",
    "parse_spec",
    "set_config_value",
    "validate_sweep_against_config",
]
