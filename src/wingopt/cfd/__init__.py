"""Volumetric CFD solvers for WingGen."""

from wingopt.cfd.lbm import LbmResult, LbmSolver
from wingopt.cfd.voxelize import VoxelizedWing, voxelize_wing

__all__ = ["LbmResult", "LbmSolver", "VoxelizedWing", "voxelize_wing"]
