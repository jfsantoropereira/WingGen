"""High-fidelity STL export for wing geometry using airfoil lofting."""

from __future__ import annotations

from dataclasses import dataclass
from math import pi
from math import cos, radians, sin, tan
from pathlib import Path

from wingopt.geometry.airfoil import AirfoilPoint
from wingopt.geometry.planform import WingGeometry


@dataclass(frozen=True)
class Triangle:
    """Triangle primitive used for STL facets."""

    a: tuple[float, float, float]
    b: tuple[float, float, float]
    c: tuple[float, float, float]


def _cross(
    u: tuple[float, float, float],
    v: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )


def _normal(triangle: Triangle) -> tuple[float, float, float]:
    ux = triangle.b[0] - triangle.a[0]
    uy = triangle.b[1] - triangle.a[1]
    uz = triangle.b[2] - triangle.a[2]

    vx = triangle.c[0] - triangle.a[0]
    vy = triangle.c[1] - triangle.a[1]
    vz = triangle.c[2] - triangle.a[2]

    nx, ny, nz = _cross((ux, uy, uz), (vx, vy, vz))
    mag = (nx * nx + ny * ny + nz * nz) ** 0.5
    if mag <= 1e-12:
        return (0.0, 0.0, 1.0)
    return (nx / mag, ny / mag, nz / mag)


def _write_ascii_stl(triangles: list[Triangle], output: Path) -> None:
    with output.open("w", encoding="utf-8") as handle:
        handle.write("solid winggen\n")
        for tri in triangles:
            nx, ny, nz = _normal(tri)
            handle.write(f"  facet normal {nx:.8e} {ny:.8e} {nz:.8e}\n")
            handle.write("    outer loop\n")
            handle.write(f"      vertex {tri.a[0]:.8e} {tri.a[1]:.8e} {tri.a[2]:.8e}\n")
            handle.write(f"      vertex {tri.b[0]:.8e} {tri.b[1]:.8e} {tri.b[2]:.8e}\n")
            handle.write(f"      vertex {tri.c[0]:.8e} {tri.c[1]:.8e} {tri.c[2]:.8e}\n")
            handle.write("    endloop\n")
            handle.write("  endfacet\n")
        handle.write("endsolid winggen\n")


def _dedupe_airfoil(points: tuple[AirfoilPoint, ...]) -> list[tuple[float, float]]:
    coords = [(p.x, p.y) for p in points]
    if len(coords) < 4:
        raise ValueError("Airfoil requires at least 4 points")

    # Remove duplicated trailing-edge endpoint if present.
    x0, y0 = coords[0]
    x1, y1 = coords[-1]
    if abs(x0 - x1) < 1e-9 and abs(y0 - y1) < 1e-9:
        coords = coords[:-1]

    if len(coords) < 4:
        raise ValueError("Airfoil contour became degenerate after deduplication")
    return coords


def _resample_closed_profile(
    coords: list[tuple[float, float]],
    target_points: int,
) -> list[tuple[float, float]]:
    if target_points < 20:
        raise ValueError("target_points must be >= 20")

    le_index = min(range(len(coords)), key=lambda i: coords[i][0])
    upper_raw = coords[: le_index + 1]
    lower_raw = coords[le_index:]

    if len(upper_raw) < 3 or len(lower_raw) < 3:
        raise ValueError("Airfoil contour does not contain enough upper/lower points")

    def compress_surface(
        points: list[tuple[float, float]],
        prefer_upper: bool,
    ) -> list[tuple[float, float]]:
        buckets: dict[float, list[float]] = {}
        for x, y in points:
            key = round(x, 8)
            buckets.setdefault(key, []).append(y)
        out: list[tuple[float, float]] = []
        for key in sorted(buckets.keys()):
            ys = buckets[key]
            out.append((key, max(ys) if prefer_upper else min(ys)))
        return out

    upper = compress_surface(upper_raw, prefer_upper=True)
    lower = compress_surface(lower_raw, prefer_upper=False)

    def interp(surface: list[tuple[float, float]], x: float) -> float:
        if x <= surface[0][0]:
            return surface[0][1]
        if x >= surface[-1][0]:
            return surface[-1][1]
        for i in range(len(surface) - 1):
            x0, y0 = surface[i]
            x1, y1 = surface[i + 1]
            if x0 <= x <= x1:
                if x1 == x0:
                    return y0
                t = (x - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)
        return surface[-1][1]

    half = max(20, target_points // 2)
    x_dense = [0.5 * (1.0 - cos(pi * i / half)) for i in range(half + 1)]  # [0, 1]

    upper_desc = [(x, interp(upper, x)) for x in reversed(x_dense)]  # TE -> LE
    lower_asc = [(x, interp(lower, x)) for x in x_dense]  # LE -> TE

    contour = upper_desc + lower_asc[1:]  # avoid duplicate LE point

    cleaned: list[tuple[float, float]] = []
    for point in contour:
        if not cleaned:
            cleaned.append(point)
            continue
        px, py = cleaned[-1]
        if abs(px - point[0]) < 1e-12 and abs(py - point[1]) < 1e-12:
            continue
        cleaned.append(point)

    return cleaned


def _section_at_y(
    geometry: WingGeometry,
    y: float,
    profile_xy: list[tuple[float, float]],
    dihedral_integral: tuple[tuple[float, ...], tuple[float, ...]] | None = None,
) -> list[tuple[float, float, float]]:
    semi = geometry.wingspan_m / 2.0
    if semi <= 0:
        raise ValueError("Invalid wingspan")

    y_abs = abs(y)
    ratio = y_abs / semi

    chord = geometry.root_chord_m + ratio * (geometry.tip_chord_m - geometry.root_chord_m)
    x_qc = y_abs * tan(radians(geometry.sweep_deg))
    if dihedral_integral is None:
        z_dihedral = y_abs * tan(radians(geometry.dihedral_deg))
    else:
        eta_grid, z_grid = dihedral_integral
        z_factor = _interp_profile_value(eta_grid, z_grid, ratio)
        z_dihedral = semi * z_factor

    local_incidence_deg = geometry.root_incidence_deg + (
        (geometry.tip_incidence_deg - geometry.root_incidence_deg) * ratio
    )
    incidence_rad = radians(-local_incidence_deg)

    section: list[tuple[float, float, float]] = []
    for x_norm, y_norm in profile_xy:
        x_local = x_norm * chord
        z_local = y_norm * chord

        # Rotate around local quarter-chord axis.
        dx = x_local - 0.25 * chord
        dz = z_local
        x_rot = 0.25 * chord + dx * cos(incidence_rad) - dz * sin(incidence_rad)
        z_rot = dz * cos(incidence_rad) + dx * sin(incidence_rad)

        x_global = (x_qc - 0.25 * chord) + x_rot
        y_global = y
        z_global = z_dihedral + z_rot
        section.append((x_global, y_global, z_global))

    return section


def _interp_profile_value(
    x_points: tuple[float, ...],
    y_points: tuple[float, ...],
    x: float,
) -> float:
    if len(x_points) != len(y_points) or not x_points:
        raise ValueError("Invalid interpolation profile")
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


def _build_dihedral_integral_profile(
    dihedral_profile: tuple[tuple[float, float], ...] | None,
) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
    if not dihedral_profile:
        return None
    ordered = sorted(dihedral_profile, key=lambda item: item[0])
    eta_points = tuple(item[0] for item in ordered)
    if abs(eta_points[0]) > 1e-9 or abs(eta_points[-1] - 1.0) > 1e-9:
        raise ValueError("dihedral_profile must start at eta=0 and end at eta=1")
    if any(b <= a for a, b in zip(eta_points, eta_points[1:])):
        raise ValueError("dihedral_profile eta values must be strictly increasing")

    angle_points = tuple(item[1] for item in ordered)
    samples = 801
    eta_grid = tuple(i / (samples - 1) for i in range(samples))
    angle_grid = tuple(_interp_profile_value(eta_points, angle_points, eta) for eta in eta_grid)

    integral: list[float] = [0.0]
    for i in range(1, len(eta_grid)):
        d_eta = eta_grid[i] - eta_grid[i - 1]
        m0 = tan(radians(angle_grid[i - 1]))
        m1 = tan(radians(angle_grid[i]))
        integral.append(integral[-1] + 0.5 * d_eta * (m0 + m1))

    return eta_grid, tuple(integral)


def _surface_tris(
    sec0: list[tuple[float, float, float]],
    sec1: list[tuple[float, float, float]],
) -> list[Triangle]:
    tris: list[Triangle] = []
    count = len(sec0)
    if count != len(sec1):
        raise ValueError("Section point counts must match")

    for j in range(count):
        jn = (j + 1) % count
        p00 = sec0[j]
        p01 = sec0[jn]
        p10 = sec1[j]
        p11 = sec1[jn]

        tris.append(Triangle(a=p00, b=p10, c=p11))
        tris.append(Triangle(a=p00, b=p11, c=p01))
    return tris


def _signed_area_xz(section: list[tuple[float, float, float]]) -> float:
    area = 0.0
    for i in range(len(section)):
        x0, _, z0 = section[i]
        x1, _, z1 = section[(i + 1) % len(section)]
        area += x0 * z1 - x1 * z0
    return 0.5 * area


def _cross2d(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _point_in_triangle_2d(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> bool:
    eps = 1e-12
    c1 = _cross2d(a, b, p)
    c2 = _cross2d(b, c, p)
    c3 = _cross2d(c, a, p)
    has_neg = (c1 < -eps) or (c2 < -eps) or (c3 < -eps)
    has_pos = (c1 > eps) or (c2 > eps) or (c3 > eps)
    return not (has_neg and has_pos)


def _triangulate_cap_indices(section: list[tuple[float, float, float]]) -> list[tuple[int, int, int]]:
    if len(section) < 3:
        raise ValueError("Cap section requires at least 3 points")

    points2d = [(p[0], p[2]) for p in section]
    ccw = _signed_area_xz(section) > 0.0

    remaining = list(range(len(section)))
    result: list[tuple[int, int, int]] = []
    guard = 0
    max_guard = len(section) * len(section)

    while len(remaining) > 3 and guard < max_guard:
        guard += 1
        ear_found = False
        count = len(remaining)
        for i in range(count):
            i_prev = remaining[(i - 1) % count]
            i_curr = remaining[i]
            i_next = remaining[(i + 1) % count]

            a = points2d[i_prev]
            b = points2d[i_curr]
            c = points2d[i_next]
            turn = _cross2d(a, b, c)
            if ccw and turn <= 1e-12:
                continue
            if (not ccw) and turn >= -1e-12:
                continue

            contains = False
            for j in remaining:
                if j in {i_prev, i_curr, i_next}:
                    continue
                if _point_in_triangle_2d(points2d[j], a, b, c):
                    contains = True
                    break
            if contains:
                continue

            result.append((i_prev, i_curr, i_next))
            del remaining[i]
            ear_found = True
            break

        if not ear_found:
            break

    if len(remaining) == 3:
        result.append((remaining[0], remaining[1], remaining[2]))

    if len(result) != len(section) - 2:
        # Fallback to robust fan triangulation from a guaranteed vertex.
        anchor = remaining[0] if remaining else 0
        others = [idx for idx in range(len(section)) if idx != anchor]
        result = []
        for i in range(len(others) - 1):
            result.append((anchor, others[i], others[i + 1]))

    return result


def _cap_tris(section: list[tuple[float, float, float]], reverse: bool) -> list[Triangle]:
    indices = _triangulate_cap_indices(section)
    tris: list[Triangle] = []
    for ia, ib, ic in indices:
        a = section[ia]
        b = section[ib]
        c = section[ic]
        if reverse:
            tris.append(Triangle(a=a, b=c, c=b))
        else:
            tris.append(Triangle(a=a, b=b, c=c))
    return tris


def export_wing_stl(
    geometry: WingGeometry,
    airfoil_coordinates: tuple[AirfoilPoint, ...],
    output_path: str | Path,
    span_sections: int = 81,
    profile_points: int = 161,
    dihedral_profile: tuple[tuple[float, float], ...] | None = None,
) -> Path:
    """Export a watertight high-resolution wing STL using the selected airfoil.

    Args:
        geometry: Wing geometry to export.
        airfoil_coordinates: Normalized airfoil coordinates used by the simulation.
        output_path: Destination STL path.
        span_sections: Number of sections across full span.
        profile_points: Number of points per closed airfoil profile section.

    Returns:
        Path to exported STL file.
    """

    if span_sections < 5:
        raise ValueError("span_sections must be >= 5")
    if span_sections % 2 == 0:
        # Keep a center section at y=0 for symmetric lofting.
        span_sections += 1

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    coords = _dedupe_airfoil(airfoil_coordinates)
    profile = _resample_closed_profile(coords, target_points=profile_points)
    dihedral_integral = _build_dihedral_integral_profile(dihedral_profile)

    semi = geometry.wingspan_m / 2.0
    sections: list[list[tuple[float, float, float]]] = []
    for i in range(span_sections):
        frac = i / (span_sections - 1)
        y = -semi + frac * (2.0 * semi)
        sections.append(
            _section_at_y(
                geometry=geometry,
                y=y,
                profile_xy=profile,
                dihedral_integral=dihedral_integral,
            )
        )

    triangles: list[Triangle] = []
    for i in range(span_sections - 1):
        triangles.extend(_surface_tris(sections[i], sections[i + 1]))

    # Tip caps at both wing ends.
    triangles.extend(_cap_tris(sections[0], reverse=True))
    triangles.extend(_cap_tris(sections[-1], reverse=False))

    _write_ascii_stl(triangles, output)
    return output
