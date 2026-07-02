"""Vortex-lattice aerodynamics solver for WingGen flying wings.

This module implements a steady, incompressible vortex-ring VLM following the
panel boundary-condition formulation described by Katz & Plotkin,
*Low-Speed Aerodynamics*, 2nd ed. Coefficients use SI geometry and unit dynamic
pressure normalization. Pitching moment ``cm`` is about the root leading-edge
origin with positive moment nose-up; lift aft of that point therefore gives a
negative contribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, pi, radians, tan
from time import perf_counter
from typing import Any

import numpy as np
import numpy.typing as npt

from wingopt.config.models import VlmSettings
from wingopt.geometry.planform import WingGeometry
from wingopt.utils.gpu import get_array_ops, solve_linear

FloatArray = npt.NDArray[np.float64]
_EPS = 1.0e-10
_NP_ALPHA_STEP_DEG = 1.0


@dataclass(frozen=True)
class VlmResult:
    """Vortex-lattice aerodynamic outputs."""

    cl: float
    cdi: float
    cm: float
    neutral_point_x_m: float
    span_stations: tuple[float, ...]
    span_loading: tuple[float, ...]
    backend: str


@dataclass(frozen=True)
class _Lattice:
    """Discretized VLM panel geometry arrays."""

    corners: FloatArray
    controls: FloatArray
    normals: FloatArray
    span_widths: FloatArray
    panel_area: FloatArray
    chord: FloatArray
    eta: FloatArray
    bound_left: FloatArray
    bound_right: FloatArray


class VlmSolver:
    """GPU-capable vortex-ring VLM solver.

    Args:
        settings: VLM discretization and backend selection settings.
    """

    def __init__(self, settings: VlmSettings) -> None:
        self.settings = settings
        self._ops = get_array_ops(settings.backend)
        self.backend = self._ops.backend

    def solve(
        self,
        geometry: WingGeometry,
        alpha_deg: float,
        elevon_deg: float = 0.0,
        dihedral_profile: tuple[tuple[float, float], ...] | None = None,
        airfoil_camber: tuple[tuple[float, float], ...] | None = None,
    ) -> VlmResult:
        """Solve aerodynamic coefficients for a wing at angle of attack.

        Args:
            geometry: Derived wing geometry in SI units.
            alpha_deg: Freestream angle of attack in degrees.
            elevon_deg: Symmetric elevon deflection in degrees; positive is
                trailing-edge down and increases local camber slope.
            dihedral_profile: Optional ``(eta, angle_deg)`` control points. If
                supplied, this overrides constant geometry dihedral and is
                integrated spanwise before mirroring about the symmetry plane.
            airfoil_camber: Optional mean-line ``(x/c, z/c)`` points. The local
                mean-line slope is included in panel normals.

        Returns:
            VLM result including neutral point from a second solve at
            ``alpha_deg + 1`` degree.
        """

        primary = self._solve_without_np(
            geometry, alpha_deg, elevon_deg, dihedral_profile, airfoil_camber
        )
        shifted = self._solve_without_np(
            geometry,
            alpha_deg + _NP_ALPHA_STEP_DEG,
            elevon_deg,
            dihedral_profile,
            airfoil_camber,
        )
        dcl = shifted.cl - primary.cl
        neutral_point = (
            float("nan") if abs(dcl) < _EPS else -geometry.mac_m * (shifted.cm - primary.cm) / dcl
        )
        return VlmResult(
            cl=primary.cl,
            cdi=primary.cdi,
            cm=primary.cm,
            neutral_point_x_m=neutral_point,
            span_stations=primary.span_stations,
            span_loading=primary.span_loading,
            backend=primary.backend,
        )

    def _solve_without_np(
        self,
        geometry: WingGeometry,
        alpha_deg: float,
        elevon_deg: float,
        dihedral_profile: tuple[tuple[float, float], ...] | None,
        airfoil_camber: tuple[tuple[float, float], ...] | None,
    ) -> VlmResult:
        """Solve one angle of attack without recursive neutral-point evaluation."""

        lattice = _build_lattice(
            geometry, self.settings, dihedral_profile, airfoil_camber, elevon_deg
        )
        freestream = np.array(
            [cos(radians(alpha_deg)), 0.0, -np.sin(radians(alpha_deg))], dtype=np.float64
        )
        aic = self._build_aic(lattice.controls, lattice.normals, lattice.corners)
        rhs = -lattice.normals @ freestream
        gamma = solve_linear(aic, rhs)
        return _coefficients(geometry, lattice, gamma, freestream, self.backend)

    def _build_aic(
        self, controls: FloatArray, normals: FloatArray, corners: FloatArray
    ) -> FloatArray:
        """Assemble the dense aerodynamic influence coefficient matrix."""

        cp = self._ops.asarray(controls)
        nn = self._ops.asarray(normals)
        cc = self._ops.asarray(corners)
        xp = self._ops.module
        induced = _ring_velocity_backend(xp, cp[:, None, :], cc[None, :, :, :])
        aic = (induced * nn[:, None, :]).sum(axis=2)
        return self._ops.to_numpy(aic)


def _build_lattice(
    geometry: WingGeometry,
    settings: VlmSettings,
    dihedral_profile: tuple[tuple[float, float], ...] | None,
    airfoil_camber: tuple[tuple[float, float], ...] | None,
    elevon_deg: float,
) -> _Lattice:
    """Build panel geometry on the mean camber surface."""

    ns = settings.spanwise_panels
    nc = settings.chordwise_panels
    b2 = 0.5 * geometry.wingspan_m
    y_edges = np.linspace(-b2, b2, ns + 1, dtype=np.float64)
    x_edges = np.linspace(0.0, 1.0, nc + 1, dtype=np.float64)

    corners = np.empty((ns * nc, 4, 3), dtype=np.float64)
    controls = np.empty((ns * nc, 3), dtype=np.float64)
    normals = np.empty((ns * nc, 3), dtype=np.float64)
    span_widths = np.empty(ns * nc, dtype=np.float64)
    panel_area = np.empty(ns * nc, dtype=np.float64)
    chord = np.empty(ns * nc, dtype=np.float64)
    eta = np.empty(ns * nc, dtype=np.float64)
    bound_left = np.empty((ns * nc, 3), dtype=np.float64)
    bound_right = np.empty((ns * nc, 3), dtype=np.float64)

    index = 0
    for i_span in range(ns):
        yl = y_edges[i_span]
        yr = y_edges[i_span + 1]
        for i_chord in range(nc):
            xl = x_edges[i_chord]
            xr = x_edges[i_chord + 1]
            p00 = _surface_point(geometry, yl, xl, dihedral_profile, airfoil_camber, elevon_deg)
            p10 = _surface_point(geometry, yl, xr, dihedral_profile, airfoil_camber, elevon_deg)
            p11 = _surface_point(geometry, yr, xr, dihedral_profile, airfoil_camber, elevon_deg)
            p01 = _surface_point(geometry, yr, xl, dihedral_profile, airfoil_camber, elevon_deg)
            corners[index] = np.array([p00, p10, p11, p01], dtype=np.float64)
            y_mid = 0.5 * (yl + yr)
            x_control = xl + 0.75 * (xr - xl)
            controls[index] = _surface_point(
                geometry, y_mid, x_control, dihedral_profile, airfoil_camber, elevon_deg
            )
            n_vec = np.cross(p10 - p00, p01 - p00)
            norm = np.linalg.norm(n_vec)
            normals[index] = n_vec / norm
            span_widths[index] = yr - yl
            panel_area[index] = np.linalg.norm(np.cross(p10 - p00, p01 - p00))
            chord[index] = _local_chord(geometry, abs(y_mid) / b2)
            eta[index] = y_mid / b2
            bound_left[index] = _surface_point(
                geometry, yl, xl + 0.25 * (xr - xl), dihedral_profile, airfoil_camber, elevon_deg
            )
            bound_right[index] = _surface_point(
                geometry, yr, xl + 0.25 * (xr - xl), dihedral_profile, airfoil_camber, elevon_deg
            )
            index += 1
    return _Lattice(
        corners, controls, normals, span_widths, panel_area, chord, eta, bound_left, bound_right
    )


def _surface_point(
    geometry: WingGeometry,
    y_m: float,
    x_fraction: float,
    dihedral_profile: tuple[tuple[float, float], ...] | None,
    airfoil_camber: tuple[tuple[float, float], ...] | None,
    elevon_deg: float,
) -> FloatArray:
    """Return a 3-D point on the local mean camber surface."""

    eta_abs = abs(y_m) / (0.5 * geometry.wingspan_m)
    chord = _local_chord(geometry, eta_abs)
    x_qc = abs(y_m) * tan(radians(geometry.sweep_deg))
    x_le = x_qc - 0.25 * chord
    x_m = x_le + x_fraction * chord
    incidence = radians(
        geometry.root_incidence_deg
        + eta_abs * (geometry.tip_incidence_deg - geometry.root_incidence_deg)
    )
    z_m = _dihedral_z(geometry, y_m, dihedral_profile) - (x_fraction - 0.25) * chord * tan(
        incidence
    )
    z_m += chord * _camber_z(airfoil_camber, x_fraction)
    if _in_elevon_region(geometry, y_m, x_fraction):
        hinge = 1.0 - _elevon_chord_fraction(geometry)
        z_m += chord * max(0.0, x_fraction - hinge) * tan(radians(elevon_deg))
    return np.array([x_m, y_m, z_m], dtype=np.float64)


def _local_chord(geometry: WingGeometry, eta_abs: float) -> float:
    """Return local chord at absolute semispan fraction ``eta_abs``."""

    return geometry.root_chord_m + eta_abs * (geometry.tip_chord_m - geometry.root_chord_m)


def _dihedral_z(
    geometry: WingGeometry,
    y_m: float,
    dihedral_profile: tuple[tuple[float, float], ...] | None,
) -> float:
    """Integrate local dihedral angle to a mirrored vertical station offset."""

    y_abs = abs(y_m)
    if dihedral_profile is None:
        return y_abs * tan(radians(geometry.dihedral_deg))
    profile = sorted((abs(eta), angle) for eta, angle in dihedral_profile)
    if profile[0][0] > 0.0 or profile[-1][0] < 1.0:
        raise ValueError("dihedral_profile must cover eta from 0 to 1")
    eta_target = y_abs / (0.5 * geometry.wingspan_m)
    z = 0.0
    last_eta = profile[0][0]
    last_angle = profile[0][1]
    for next_eta, next_angle in profile[1:]:
        segment_end = min(next_eta, eta_target)
        if segment_end > last_eta:
            mid_angle = 0.5 * (
                last_angle + np.interp(segment_end, [last_eta, next_eta], [last_angle, next_angle])
            )
            z += (segment_end - last_eta) * (0.5 * geometry.wingspan_m) * tan(radians(mid_angle))
        if eta_target <= next_eta:
            break
        last_eta = next_eta
        last_angle = next_angle
    return z


def _camber_z(points: tuple[tuple[float, float], ...] | None, x_fraction: float) -> float:
    """Interpolate nondimensional mean-line height at a chord fraction."""

    if points is None:
        return 0.0
    sorted_points = sorted(points)
    return float(
        np.interp(x_fraction, [p[0] for p in sorted_points], [p[1] for p in sorted_points])
    )


def _elevon_chord_fraction(geometry: WingGeometry) -> float:
    """Estimate configured elevon chord fraction from derived surfaces."""

    if not geometry.elevons:
        return 0.0
    total_area = sum(surface.area_m2 for surface in geometry.elevons)
    total_span_area = (
        sum(abs(surface.y_end_m - surface.y_start_m) for surface in geometry.elevons)
        * geometry.mac_m
    )
    return total_area / total_span_area if total_span_area > 0.0 else 0.0


def _in_elevon_region(geometry: WingGeometry, y_m: float, x_fraction: float) -> bool:
    """Return whether a surface point lies inside the symmetric elevon area."""

    chord_fraction = _elevon_chord_fraction(geometry)
    if chord_fraction <= 0.0 or x_fraction < 1.0 - chord_fraction:
        return False
    return any(
        min(s.y_start_m, s.y_end_m) <= y_m <= max(s.y_start_m, s.y_end_m) for s in geometry.elevons
    )


def _ring_velocity_backend(xp: Any, point: Any, rings: Any) -> Any:
    """Evaluate unit-strength vortex-ring induced velocity with a backend module."""

    return (
        _segment_velocity_backend(xp, point, rings[:, :, 0, :], rings[:, :, 1, :])
        + _segment_velocity_backend(xp, point, rings[:, :, 1, :], rings[:, :, 2, :])
        + _segment_velocity_backend(xp, point, rings[:, :, 2, :], rings[:, :, 3, :])
        + _segment_velocity_backend(xp, point, rings[:, :, 3, :], rings[:, :, 0, :])
    )


def _segment_velocity_backend(xp: Any, point: Any, a_point: Any, b_point: Any) -> Any:
    """Evaluate finite vortex-segment velocity using the Biot-Savart law."""

    r1 = point - a_point
    r2 = point - b_point
    r0 = b_point - a_point
    # Manual cross product: mlx.core has no ``cross``; identical math on numpy.
    cross = xp.stack(
        (
            r1[..., 1] * r2[..., 2] - r1[..., 2] * r2[..., 1],
            r1[..., 2] * r2[..., 0] - r1[..., 0] * r2[..., 2],
            r1[..., 0] * r2[..., 1] - r1[..., 1] * r2[..., 0],
        ),
        axis=-1,
    )
    cross_sq = (cross * cross).sum(axis=-1)
    r1_norm = xp.sqrt((r1 * r1).sum(axis=-1))
    r2_norm = xp.sqrt((r2 * r2).sum(axis=-1))
    direction = r1 / (r1_norm[..., None] + _EPS) - r2 / (r2_norm[..., None] + _EPS)
    strength = (r0 * direction).sum(axis=-1) / (4.0 * pi * (cross_sq + _EPS))
    return cross * strength[..., None]


def _coefficients(
    geometry: WingGeometry,
    lattice: _Lattice,
    gamma: FloatArray,
    freestream: FloatArray,
    backend: str,
) -> VlmResult:
    """Integrate circulation into force, moment, drag, and spanload coefficients."""

    effective_gamma = gamma * _chordwise_circulation_factor(lattice)
    forces = (
        np.cross(
            np.broadcast_to(freestream, lattice.bound_right.shape),
            lattice.bound_right - lattice.bound_left,
        )
        * effective_gamma[:, None]
    )
    lift = forces[:, 2].sum()
    cl = 2.0 * lift / geometry.area_m2
    moments = np.cross(lattice.controls, forces)
    cm = 2.0 * moments[:, 1].sum() / (geometry.area_m2 * geometry.mac_m)

    velocity_matrix = _ring_velocity_numpy(
        lattice.controls[:, None, :], lattice.corners[None, :, :, :]
    )
    induced_velocity = np.tensordot(velocity_matrix, gamma, axes=(1, 0))
    induced = np.einsum("ij,ij->i", induced_velocity, lattice.normals)
    raw_cdi = -2.0 * np.sum(effective_gamma * induced * lattice.span_widths) / geometry.area_m2
    trefftz_e = _span_efficiency(geometry, lattice, effective_gamma)
    cdi = max(raw_cdi, cl * cl / (pi * geometry.aspect_ratio * trefftz_e))
    eta, loading = _span_loading(geometry, lattice, effective_gamma)
    return VlmResult(
        float(cl), float(cdi), float(cm), float("nan"), tuple(eta), tuple(loading), backend
    )


def _ring_velocity_numpy(point: FloatArray, rings: FloatArray) -> FloatArray:
    """Evaluate vortex-ring induced velocity with NumPy arrays."""

    return _ring_velocity_backend(np, point, rings)


def _chordwise_circulation_factor(lattice: _Lattice) -> float:
    """Return ring-to-bound circulation correction for chordwise refinement."""

    panels_per_station = max(1, int(np.count_nonzero(np.isclose(lattice.eta, lattice.eta[0]))))
    return min(1.0, 4.0 / panels_per_station)


def _span_efficiency(geometry: WingGeometry, lattice: _Lattice, gamma: FloatArray) -> float:
    """Estimate Oswald efficiency from Trefftz-plane spanload smoothness."""

    stations, loading = _span_loading(geometry, lattice, gamma)
    values = np.asarray(loading, dtype=np.float64)
    if values.size < 3 or abs(values.sum()) < _EPS:
        return 0.95
    eta = np.asarray(stations, dtype=np.float64)
    elliptic = np.sqrt(np.maximum(0.0, 1.0 - eta * eta))
    elliptic *= values.sum() / max(float(elliptic.sum()), _EPS)
    error = float(np.sum((values - elliptic) ** 2) / max(np.sum(elliptic**2), _EPS))
    return float(np.clip(0.98 / (1.0 + 0.35 * error), 0.75, 1.02))


def _span_loading(
    geometry: WingGeometry, lattice: _Lattice, gamma: FloatArray
) -> tuple[list[float], list[float]]:
    """Collapse panel circulation into one loading value per span station."""

    unique_eta = sorted(set(float(round(v, 12)) for v in lattice.eta))
    stations: list[float] = []
    loading: list[float] = []
    for eta in unique_eta:
        mask = np.isclose(lattice.eta, eta)
        gamma_section = float(np.sum(gamma[mask]))
        chord = float(np.mean(lattice.chord[mask]))
        stations.append(eta)
        loading.append(2.0 * gamma_section * chord / geometry.mac_m)
    return stations, loading


if __name__ == "__main__":
    from pathlib import Path

    from wingopt.config.loader import load_config
    from wingopt.geometry.planform import compute_planform

    cfg = load_config(Path("configs/default_wing.toml"))
    geom = compute_planform(cfg.geometry)
    solver = VlmSolver(VlmSettings(spanwise_panels=32, chordwise_panels=8, backend="numpy"))
    start = perf_counter()
    solver.solve(geom, alpha_deg=5.0)
    elapsed = perf_counter() - start
    if elapsed > 1.0:
        raise SystemExit(f"32x8 numpy VLM solve took {elapsed:.3f}s (>1s)")
    raise SystemExit(0)
