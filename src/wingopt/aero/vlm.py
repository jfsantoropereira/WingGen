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
    """Discretized VLM panel and wake geometry arrays.

    ``rings`` are the vortex rings displaced a quarter panel downstream of the
    panel edges (Katz & Plotkin ch. 12); ``wake_rings`` extend the trailing-edge
    ring row far downstream so the trailing-edge rings shed their circulation
    into the wake (discrete Kutta condition).
    """

    rings: FloatArray          # (n_panels, 4, 3) ring corners
    wake_rings: FloatArray     # (n_strips, 4, 3) wake ring corners
    te_indices: FloatArray     # (n_strips,) int — panel index of each TE ring
    upstream_index: FloatArray  # (n_panels,) int — chordwise upstream panel or -1
    controls: FloatArray
    normals: FloatArray
    span_widths: FloatArray
    chord: FloatArray
    eta: FloatArray            # per-panel strip midpoint eta in [-1, 1]
    bound_left: FloatArray     # ring leading-segment endpoints (panel c/4 line)
    bound_right: FloatArray
    te_edge_points: FloatArray  # (n_strips + 1, 3) wing TE points per strip edge


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
        alpha_rad = radians(alpha_deg)
        freestream = np.array([cos(alpha_rad), 0.0, -np.sin(alpha_rad)], dtype=np.float64)
        aic = self._build_aic(lattice)
        rhs = -lattice.normals @ freestream
        gamma = solve_linear(aic, rhs)
        return _coefficients(geometry, lattice, gamma, alpha_rad, self.backend)

    def _build_aic(self, lattice: _Lattice) -> FloatArray:
        """Assemble the dense AIC including wake-ring shedding columns.

        Each trailing-edge ring's column also carries its wake ring's
        influence, because both share the same circulation (steady Kutta
        condition).
        """

        cp = self._ops.asarray(lattice.controls)
        nn = self._ops.asarray(lattice.normals)
        rings = self._ops.asarray(lattice.rings)
        wake = self._ops.asarray(lattice.wake_rings)
        xp = self._ops.module
        induced = _ring_velocity_backend(xp, cp[:, None, :], rings[None, :, :, :])
        aic = self._ops.to_numpy((induced * nn[:, None, :]).sum(axis=2))
        wake_induced = _ring_velocity_backend(xp, cp[:, None, :], wake[None, :, :, :])
        wake_aic = self._ops.to_numpy((wake_induced * nn[:, None, :]).sum(axis=2))
        te_columns = np.asarray(lattice.te_indices, dtype=np.int64)
        aic[:, te_columns] += wake_aic
        return aic


#: Wake length in wingspans; far enough for converged influence, close enough
#: to stay accurate in the float32 MLX path.
_WAKE_SPANS = 25.0


def _build_lattice(
    geometry: WingGeometry,
    settings: VlmSettings,
    dihedral_profile: tuple[tuple[float, float], ...] | None,
    airfoil_camber: tuple[tuple[float, float], ...] | None,
    elevon_deg: float,
) -> _Lattice:
    """Build vortex rings, controls, and wake on the mean camber surface.

    Rings are displaced a quarter panel-chord downstream of the panel edges,
    with control points at the panel three-quarter chord (Katz & Plotkin
    ch. 12.3). The trailing-edge ring row extends a quarter panel beyond the
    trailing edge, and one wake ring per span strip carries the trailing-edge
    circulation ``_WAKE_SPANS`` spans downstream.
    """

    ns = settings.spanwise_panels
    nc = settings.chordwise_panels
    b2 = 0.5 * geometry.wingspan_m
    y_edges = np.linspace(-b2, b2, ns + 1, dtype=np.float64)
    x_edges = np.linspace(0.0, 1.0, nc + 1, dtype=np.float64)
    dx = 1.0 / nc
    # Ring chordwise stations: panel edges shifted a quarter panel downstream.
    x_rings = x_edges + 0.25 * dx

    n_panels = ns * nc
    rings = np.empty((n_panels, 4, 3), dtype=np.float64)
    controls = np.empty((n_panels, 3), dtype=np.float64)
    normals = np.empty((n_panels, 3), dtype=np.float64)
    span_widths = np.empty(n_panels, dtype=np.float64)
    chord = np.empty(n_panels, dtype=np.float64)
    eta = np.empty(n_panels, dtype=np.float64)
    bound_left = np.empty((n_panels, 3), dtype=np.float64)
    bound_right = np.empty((n_panels, 3), dtype=np.float64)
    upstream_index = np.empty(n_panels, dtype=np.int64)
    te_indices = np.empty(ns, dtype=np.int64)
    wake_rings = np.empty((ns, 4, 3), dtype=np.float64)
    te_edge_points = np.empty((ns + 1, 3), dtype=np.float64)

    wake_dx = _WAKE_SPANS * geometry.wingspan_m

    def surf(y_m: float, xf: float) -> FloatArray:
        return _surface_point(geometry, y_m, xf, dihedral_profile, airfoil_camber, elevon_deg)

    for i_edge, y_m in enumerate(y_edges):
        te_edge_points[i_edge] = surf(y_m, 1.0)

    index = 0
    for i_span in range(ns):
        yl = y_edges[i_span]
        yr = y_edges[i_span + 1]
        y_mid = 0.5 * (yl + yr)
        for i_chord in range(nc):
            xa = x_rings[i_chord]
            xb = x_rings[i_chord + 1]
            q00 = surf(yl, xa)
            q10 = surf(yl, xb)
            q11 = surf(yr, xb)
            q01 = surf(yr, xa)
            rings[index] = np.array([q00, q10, q11, q01], dtype=np.float64)
            controls[index] = surf(y_mid, x_edges[i_chord] + 0.75 * dx)
            # Normal from the underlying camber-surface panel (not the ring).
            p00 = surf(yl, x_edges[i_chord])
            p10 = surf(yl, x_edges[i_chord + 1])
            p01 = surf(yr, x_edges[i_chord])
            n_vec = np.cross(p10 - p00, p01 - p00)
            normals[index] = n_vec / np.linalg.norm(n_vec)
            span_widths[index] = yr - yl
            chord[index] = _local_chord(geometry, abs(y_mid) / b2)
            eta[index] = y_mid / b2
            bound_left[index] = q00
            bound_right[index] = q01
            upstream_index[index] = index - 1 if i_chord > 0 else -1
            index += 1
        te_index = index - 1
        te_indices[i_span] = te_index
        te_left = rings[te_index, 1]
        te_right = rings[te_index, 2]
        far_left = te_left + np.array([wake_dx, 0.0, 0.0])
        far_right = te_right + np.array([wake_dx, 0.0, 0.0])
        # Corner order mirrors the panel rings so the shared segment between
        # the TE ring and its wake ring cancels at equal circulation.
        wake_rings[i_span] = np.array([te_left, far_left, far_right, te_right], dtype=np.float64)

    return _Lattice(
        rings=rings,
        wake_rings=wake_rings,
        te_indices=te_indices,
        upstream_index=upstream_index,
        controls=controls,
        normals=normals,
        span_widths=span_widths,
        chord=chord,
        eta=eta,
        bound_left=bound_left,
        bound_right=bound_right,
        te_edge_points=te_edge_points,
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
    # Root leading edge is the coordinate origin; the quarter-chord line
    # starts at 0.25 * root chord and sweeps aft.
    x_qc = 0.25 * geometry.root_chord_m + abs(y_m) * tan(radians(geometry.sweep_deg))
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
    alpha_rad: float,
    backend: str,
) -> VlmResult:
    """Integrate ring circulation into force, moment, drag, and spanload.

    Near-field lift and moment come from Kutta-Joukowski on each ring's
    leading (bound) segment carrying the ring-difference circulation
    ``gamma_i - gamma_upstream``. Induced drag comes from a discrete
    Trefftz-plane evaluation of the shed wake (Katz & Plotkin ch. 8/12),
    which supports non-planar (dihedral) geometry.
    """

    freestream = np.array([cos(alpha_rad), 0.0, -np.sin(alpha_rad)], dtype=np.float64)
    upstream = np.asarray(lattice.upstream_index, dtype=np.int64)
    gamma_bound = gamma.copy()
    has_upstream = upstream >= 0
    gamma_bound[has_upstream] -= gamma[upstream[has_upstream]]

    forces = (
        np.cross(
            np.broadcast_to(freestream, lattice.bound_right.shape),
            lattice.bound_right - lattice.bound_left,
        )
        * gamma_bound[:, None]
    )
    lift_dir = np.array([np.sin(alpha_rad), 0.0, cos(alpha_rad)], dtype=np.float64)
    lift = float((forces * lift_dir).sum())
    cl = 2.0 * lift / geometry.area_m2

    bound_mid = 0.5 * (lattice.bound_left + lattice.bound_right)
    moments = np.cross(bound_mid, forces)
    cm = 2.0 * float(moments[:, 1].sum()) / (geometry.area_m2 * geometry.mac_m)

    cdi = _trefftz_cdi(geometry, lattice, gamma)
    eta, loading = _span_loading(geometry, lattice, gamma)
    return VlmResult(
        float(cl), float(cdi), float(cm), float("nan"), tuple(eta), tuple(loading), backend
    )


def _trefftz_cdi(geometry: WingGeometry, lattice: _Lattice, gamma: FloatArray) -> float:
    """Discrete Trefftz-plane induced drag from shed trailing vorticity.

    Strip circulation equals the trailing-edge ring circulation (chordwise ring
    differences telescope). Trailing vortex filaments sit at the strip-edge
    trailing-edge points projected onto the y-z plane; each induces a 2-D
    point-vortex velocity in the Trefftz plane. Induced drag integrates
    ``Gamma * w_normal`` along the (possibly dihedraled) span trace.
    """

    te_indices = np.asarray(lattice.te_indices, dtype=np.int64)
    strip_gamma = gamma[te_indices]
    edges_yz = lattice.te_edge_points[:, 1:3]  # (ns+1, 2): (y, z)
    # Trailing filament strengths: circulation jump across each strip edge.
    padded = np.concatenate(([0.0], strip_gamma, [0.0]))
    filament = padded[1:] - padded[:-1]  # (ns+1,)

    mid_yz = 0.5 * (edges_yz[:-1] + edges_yz[1:])  # (ns, 2)
    strip_vec = edges_yz[1:] - edges_yz[:-1]  # (ns, 2)
    strip_len = np.linalg.norm(strip_vec, axis=1)
    # In-plane normal to the span trace (unit), pointing +z for a flat wing.
    normal = np.stack((-strip_vec[:, 1], strip_vec[:, 0]), axis=1)
    normal /= np.maximum(strip_len[:, None], _EPS)

    rel = mid_yz[:, None, :] - edges_yz[None, :, :]  # (ns, ns+1, 2)
    r_sq = (rel * rel).sum(axis=2)
    # 2-D point vortex: v = Gamma/(2*pi) * (-dz, dy)/r^2 ... perpendicular field.
    v_y = -rel[:, :, 1] / np.maximum(r_sq, _EPS)
    v_z = rel[:, :, 0] / np.maximum(r_sq, _EPS)
    w_y = (v_y * filament[None, :]).sum(axis=1) / (2.0 * pi)
    w_z = (v_z * filament[None, :]).sum(axis=1) / (2.0 * pi)
    wash_normal = w_y * normal[:, 0] + w_z * normal[:, 1]

    # Di = (rho/2) * sum Gamma_i * w_n_i * ds_i  (unit V, rho): CDi = Di / (q S).
    di = 0.5 * float(np.sum(strip_gamma * wash_normal * strip_len))
    return float(2.0 * di / geometry.area_m2)


def _span_loading(
    geometry: WingGeometry, lattice: _Lattice, gamma: FloatArray
) -> tuple[list[float], list[float]]:
    """Per-strip normalized loading ``cl*c/MAC`` from strip circulation."""

    te_indices = np.asarray(lattice.te_indices, dtype=np.int64)
    strip_gamma = gamma[te_indices]
    stations = [float(lattice.eta[i]) for i in te_indices]
    loading = [2.0 * float(g) / geometry.mac_m for g in strip_gamma]
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
