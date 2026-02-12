"""Optimization modules."""

from wingopt.optimizer.coordinator import CoordinatorResult, OptimizationCoordinator
from wingopt.optimizer.propulsion_optimizer import PropulsionCandidate, PropulsionOptimizer
from wingopt.optimizer.wing_optimizer import AirfoilComparison, WingCandidate, WingOptimizer

__all__ = [
    "AirfoilComparison",
    "CoordinatorResult",
    "OptimizationCoordinator",
    "PropulsionCandidate",
    "PropulsionOptimizer",
    "WingCandidate",
    "WingOptimizer",
]
