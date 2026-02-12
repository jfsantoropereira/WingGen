"""Geometry package."""

from wingopt.geometry.airfoil import AirfoilData, AirfoilDataError, load_airfoil_library
from wingopt.geometry.planform import WingGeometry, compute_planform

__all__ = [
    "AirfoilData",
    "AirfoilDataError",
    "WingGeometry",
    "compute_planform",
    "load_airfoil_library",
]
