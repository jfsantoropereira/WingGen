"""Wing voxelization utilities for lattice-Boltzmann CFD."""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, radians, sin

import numpy as np
from numpy.typing import NDArray

from wingopt.geometry.airfoil import AirfoilPoint
from wingopt.geometry.planform import WingGeometry
from wingopt.viz.stl_export import (
    _build_dihedral_integral_profile,
    _dedupe_airfoil,
    _resample_closed_profile,
    _section_at_y,
)


@dataclass(frozen=True)
class VoxelizedWing:
    """Solid wing occupancy and lattice metric data.

    Attributes:
        solid: Boolean occupancy in ``(x, y, z)`` lattice order for a half-wing.
        dx_m: Uniform lattice spacing in meters.
        origin_m: Physical coordinate of lattice index ``(0, 0, 0)`` in meters.
        bounds_m: Physical bounding box ``((xmin, ymin, zmin), (xmax, ymax, zmax))``.
    """

    solid: NDArray[np.bool]
    dx_m: float
    origin_m: tuple[float, float, float]
    bounds_m: tuple[tuple[float, float, float], tuple[float, float, float]]


def voxelize_wing(
    geometry: WingGeometry,
    airfoil_coordinates: tuple[AirfoilPoint, ...],
    resolution: tuple[int, int, int],
    alpha_deg: float = 0.0,
    dihedral_profile: tuple[tuple[float, float], ...] | None = None,
) -> VoxelizedWing:
    """Rasterize a half-wing into a solid occupancy lattice.

    The section-lofting math mirrors :mod:`wingopt.viz.stl_export`; the complete
    loft is rotated by angle of attack before direct per-section polygon fill.
    The method is conservative for LBM bounce-back cells and intentionally keeps
    clearance around the body for inlet, outlet, and far-field boundaries.

    Args:
        geometry: Wing geometry to voxelize.
        airfoil_coordinates: Closed Selig-style normalized airfoil coordinates.
        resolution: Lattice resolution ``(nx, ny, nz)``.
        alpha_deg: Angle of attack in degrees, applied as a pitch rotation.
        dihedral_profile: Optional ``(eta, angle_deg)`` spanwise dihedral profile.

    Returns:
        Occupancy grid and lattice metric data.
    """

    nx, ny, nz = resolution
    if min(resolution) < 8:
        raise ValueError("resolution entries must be >= 8")

    profile = _resample_closed_profile(_dedupe_airfoil(airfoil_coordinates), target_points=121)
    dihedral_integral = _build_dihedral_integral_profile(dihedral_profile)
    semi = geometry.wingspan_m / 2.0
    span_sections = max(25, min(ny * 2, 161))
    y_sections = np.linspace(0.0, semi, span_sections)
    alpha = radians(alpha_deg)

    sections: list[NDArray[np.float64]] = []
    for y in y_sections:
        section = np.asarray(
            _section_at_y(geometry, float(y), profile, dihedral_integral), dtype=float
        )
        x = section[:, 0].copy()
        z = section[:, 2].copy()
        section[:, 0] = x * cos(alpha) + z * sin(alpha)
        section[:, 2] = -x * sin(alpha) + z * cos(alpha)
        sections.append(section)

    all_points = np.vstack(sections)
    mins = all_points.min(axis=0)
    maxs = all_points.max(axis=0)
    chord = geometry.root_chord_m
    margin = np.array([0.85 * chord, 0.04 * semi, 0.75 * chord])
    mins = mins - margin
    maxs = maxs + margin
    mins[1] = 0.0
    maxs[1] = semi + margin[1]
    spans = np.maximum(maxs - mins, 1e-6)
    dx = float(np.max(spans / np.array([nx - 5, ny - 3, nz - 5], dtype=float)))
    center = 0.5 * (mins + maxs)
    lattice_span = dx * np.array([nx - 1, ny - 1, nz - 1], dtype=float)
    origin = center - 0.5 * lattice_span
    origin[1] = 0.0

    solid = np.zeros((nx, ny, nz), dtype=bool)
    y_grid = origin[1] + np.arange(ny) * dx
    for j, y in enumerate(y_grid):
        if y < -0.5 * dx or y > semi + 0.5 * dx:
            continue
        idx = int(np.argmin(np.abs(y_sections - y)))
        poly = sections[idx][:, [0, 2]]
        x_min, z_min = poly.min(axis=0) - 0.5 * dx
        x_max, z_max = poly.max(axis=0) + 0.5 * dx
        ix0 = max(1, int(np.floor((x_min - origin[0]) / dx)))
        ix1 = min(nx - 2, int(np.ceil((x_max - origin[0]) / dx)))
        iz0 = max(1, int(np.floor((z_min - origin[2]) / dx)))
        iz1 = min(nz - 2, int(np.ceil((z_max - origin[2]) / dx)))
        if ix1 < ix0 or iz1 < iz0:
            continue
        xs = origin[0] + np.arange(ix0, ix1 + 1) * dx
        zs = origin[2] + np.arange(iz0, iz1 + 1) * dx
        inside = _points_in_polygon(xs[:, None], zs[None, :], poly)
        solid[ix0 : ix1 + 1, j, iz0 : iz1 + 1] |= inside

    bounds = (tuple(origin), tuple(origin + lattice_span))
    return VoxelizedWing(solid=solid, dx_m=dx, origin_m=tuple(origin), bounds_m=bounds)


def _points_in_polygon(
    x: NDArray[np.float64], z: NDArray[np.float64], polygon: NDArray[np.float64]
) -> NDArray[np.bool]:
    """Return vectorized even-odd polygon containment for x-z query arrays."""

    inside = np.zeros(np.broadcast_shapes(x.shape, z.shape), dtype=bool)
    xq = np.broadcast_to(x, inside.shape)
    zq = np.broadcast_to(z, inside.shape)
    x0 = polygon[-1, 0]
    z0 = polygon[-1, 1]
    for x1, z1 in polygon:
        crosses = ((z0 > zq) != (z1 > zq)) & (
            xq < (x1 - x0) * (zq - z0) / (z1 - z0 + 1e-30) + x0
        )
        inside ^= crosses
        x0 = float(x1)
        z0 = float(z1)
    return inside
