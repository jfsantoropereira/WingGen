"""Rebuild wing meshes from stored design parameters.

Mirrors ``_export_best_design_stl`` in ``scripts/simulate.py``: geometry is
reconstructed from the design record's dotted ``geometry.*`` params (falling
back to the default config), lofted with the recorded airfoil coordinate set,
and honoring an optional ``organic.dihedral_profile`` param. The lofted ASCII
STL from :func:`wingopt.viz.export_wing_stl` is converted to binary STL for
compact download and cached in the run's artifacts directory.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Any

from wingopt.config.models import GeometryConfig, WingGenConfig
from wingopt.geometry.airfoil import load_airfoil_coordinates
from wingopt.geometry.planform import compute_planform
from wingopt.store import DesignRecord, RunStore
from wingopt.viz import export_wing_stl

MIN_SPAN_SECTIONS = 9
MAX_SPAN_SECTIONS = 801
MIN_PROFILE_POINTS = 41
MAX_PROFILE_POINTS = 1601

_AIRFOIL_NAME_RE = re.compile(r"^[a-z0-9_\-]+$")
_VERTEX_RE = re.compile(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)")
_NORMAL_RE = re.compile(r"facet normal\s+(\S+)\s+(\S+)\s+(\S+)")


class MeshError(ValueError):
    """Raised when a mesh cannot be rebuilt from a design record."""


def _param_float(params: dict[str, Any], key: str, fallback: float) -> float:
    value = params.get(key, fallback)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise MeshError(f"Design param {key!r} is not numeric: {value!r}") from exc


def _dihedral_profile(params: dict[str, Any]) -> tuple[tuple[float, float], ...] | None:
    raw = params.get("organic.dihedral_profile")
    if not isinstance(raw, list) or not raw:
        return None
    points: list[tuple[float, float]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise MeshError("organic.dihedral_profile must be a list of [eta, deg] pairs")
        points.append((float(item[0]), float(item[1])))
    return tuple(points)


def validate_resolution(span_sections: int, profile_points: int) -> None:
    """Validate requested mesh resolution against hard bounds.

    Raises:
        MeshError: If either value falls outside the allowed range.
    """
    if not (MIN_SPAN_SECTIONS <= span_sections <= MAX_SPAN_SECTIONS):
        raise MeshError(
            f"span_sections must be in [{MIN_SPAN_SECTIONS}, {MAX_SPAN_SECTIONS}]"
        )
    if not (MIN_PROFILE_POINTS <= profile_points <= MAX_PROFILE_POINTS):
        raise MeshError(
            f"profile_points must be in [{MIN_PROFILE_POINTS}, {MAX_PROFILE_POINTS}]"
        )


def ascii_stl_to_binary(ascii_path: Path, binary_path: Path) -> Path:
    """Convert an ASCII STL file to little-endian binary STL."""
    facets: list[tuple[tuple[float, float, float], ...]] = []
    normal: tuple[float, float, float] = (0.0, 0.0, 0.0)
    vertices: list[tuple[float, float, float]] = []
    with ascii_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if line.startswith("facet normal"):
                match = _NORMAL_RE.search(line)
                if match:
                    normal = (float(match[1]), float(match[2]), float(match[3]))
                vertices = []
            elif line.startswith("vertex"):
                match = _VERTEX_RE.search(line)
                if match:
                    vertices.append((float(match[1]), float(match[2]), float(match[3])))
            elif line.startswith("endfacet"):
                if len(vertices) == 3:
                    facets.append((normal, vertices[0], vertices[1], vertices[2]))
    header = b"WingGen Studio binary STL".ljust(80, b" ")
    with binary_path.open("wb") as out:
        out.write(header)
        out.write(struct.pack("<I", len(facets)))
        for facet_normal, a, b, c in facets:
            out.write(struct.pack("<12f", *facet_normal, *a, *b, *c))
            out.write(struct.pack("<H", 0))
    return binary_path


def build_design_mesh(
    store: RunStore,
    design: DesignRecord,
    default_config: WingGenConfig,
    data_dir: Path,
    span_sections: int,
    profile_points: int,
) -> Path:
    """Build (or fetch from cache) the binary STL mesh for a design record.

    Args:
        store: Run store used for artifact path resolution.
        design: Design record with dotted ``geometry.*`` params.
        default_config: Fallback for params the design does not carry.
        data_dir: Repo ``data/`` directory holding airfoil coordinates.
        span_sections: Sections across the full span (odd count enforced
            downstream by the exporter).
        profile_points: Points per closed airfoil profile.

    Returns:
        Path to the cached binary STL artifact.

    Raises:
        MeshError: On invalid resolution, params, or unknown airfoil.
    """
    validate_resolution(span_sections, profile_points)

    artifact_name = f"{design.design_id}-{span_sections}x{profile_points}.stl"
    output_path = store.artifact_path(design.run_id, artifact_name)
    if output_path.exists():
        return output_path

    defaults = default_config.geometry
    params = design.params
    airfoil = str(params.get("geometry.airfoil", defaults.airfoil)).lower()
    if not _AIRFOIL_NAME_RE.match(airfoil):
        raise MeshError(f"Invalid airfoil name: {airfoil!r}")
    airfoil_path = data_dir / "airfoils" / "coordinates" / f"{airfoil}.dat"
    if not airfoil_path.is_file():
        raise MeshError(f"Unknown airfoil: {airfoil!r}")

    geometry_cfg = GeometryConfig(
        wingspan_m=_param_float(params, "geometry.wingspan_m", defaults.wingspan_m),
        root_chord_m=_param_float(params, "geometry.root_chord_m", defaults.root_chord_m),
        tip_chord_m=_param_float(params, "geometry.tip_chord_m", defaults.tip_chord_m),
        sweep_deg=_param_float(params, "geometry.sweep_deg", defaults.sweep_deg),
        dihedral_deg=_param_float(params, "geometry.dihedral_deg", defaults.dihedral_deg),
        root_incidence_deg=_param_float(
            params, "geometry.root_incidence_deg", defaults.root_incidence_deg
        ),
        tip_incidence_deg=_param_float(
            params, "geometry.tip_incidence_deg", defaults.tip_incidence_deg
        ),
        airfoil=airfoil,
        airfoil_candidates=defaults.airfoil_candidates,
        elevons=defaults.elevons,
    )
    geometry = compute_planform(geometry_cfg)
    _, airfoil_coords = load_airfoil_coordinates(airfoil_path)

    ascii_path = output_path.with_suffix(".ascii.stl.tmp")
    try:
        export_wing_stl(
            geometry=geometry,
            airfoil_coordinates=airfoil_coords,
            output_path=ascii_path,
            span_sections=span_sections,
            profile_points=profile_points,
            dihedral_profile=_dihedral_profile(params),
        )
        ascii_stl_to_binary(ascii_path, output_path)
    finally:
        ascii_path.unlink(missing_ok=True)
    return output_path
