"""Wing geometry generation and derived metrics."""

from __future__ import annotations

from dataclasses import dataclass
from math import radians, tan

from wingopt.config.models import GeometryConfig


@dataclass(frozen=True)
class SpanStation:
    """Chord value at a spanwise station.

    Attributes:
        y_m: Spanwise coordinate (m), positive to starboard.
        chord_m: Local chord at y (m).
    """

    y_m: float
    chord_m: float


@dataclass(frozen=True)
class QuarterChordPoint:
    """Quarter-chord location in planform coordinates.

    Attributes:
        x_m: Longitudinal coordinate (m).
        y_m: Spanwise coordinate (m).
    """

    x_m: float
    y_m: float


@dataclass(frozen=True)
class ElevonSurface:
    """Geometric definition of one elevon surface."""

    name: str
    side: str
    y_start_m: float
    y_end_m: float
    area_m2: float
    moment_arm_m: float


@dataclass(frozen=True)
class WingGeometry:
    """Derived geometry for the wing planform."""

    wingspan_m: float
    root_chord_m: float
    tip_chord_m: float
    area_m2: float
    aspect_ratio: float
    mac_m: float
    taper_ratio: float
    sweep_deg: float
    twist_deg: float
    stations: tuple[SpanStation, ...]
    quarter_chord_line: tuple[QuarterChordPoint, ...]
    elevons: tuple[ElevonSurface, ...]


class GeometryError(ValueError):
    """Raised for invalid geometry operations."""


def _local_chord(y_abs: float, semi_span: float, root_chord: float, tip_chord: float) -> float:
    if semi_span <= 0:
        raise GeometryError("semi_span must be > 0")
    if not (0.0 <= y_abs <= semi_span):
        raise GeometryError(f"|y|={y_abs} outside [0, {semi_span}]")
    ratio = y_abs / semi_span
    return root_chord + ratio * (tip_chord - root_chord)


def _build_elevons(cfg: GeometryConfig, semi_span: float) -> tuple[ElevonSurface, ...]:
    elevon_span = cfg.elevons.span_fraction * semi_span
    y0 = semi_span - elevon_span
    y_split = y0 + elevon_span * cfg.elevons.split_ratio

    # Use mid-span chord at each elevon piece for area estimation.
    y_inner_mid = 0.5 * (y0 + y_split)
    y_outer_mid = 0.5 * (y_split + semi_span)
    inner_chord = _local_chord(y_inner_mid, semi_span, cfg.root_chord_m, cfg.tip_chord_m)
    outer_chord = _local_chord(y_outer_mid, semi_span, cfg.root_chord_m, cfg.tip_chord_m)

    inner_area = (y_split - y0) * inner_chord * cfg.elevons.chord_fraction
    outer_area = (semi_span - y_split) * outer_chord * cfg.elevons.chord_fraction

    def signed(side: str, start: float, end: float) -> tuple[float, float]:
        return ((start, end) if side == "right" else (-end, -start))

    surfaces: list[ElevonSurface] = []
    for side in ("left", "right"):
        y1, y2 = signed(side, y0, y_split)
        surfaces.append(
            ElevonSurface(
                name=f"{side}_inner",
                side=side,
                y_start_m=y1,
                y_end_m=y2,
                area_m2=inner_area,
                moment_arm_m=0.25 * inner_chord,
            )
        )

        y3, y4 = signed(side, y_split, semi_span)
        surfaces.append(
            ElevonSurface(
                name=f"{side}_outer",
                side=side,
                y_start_m=y3,
                y_end_m=y4,
                area_m2=outer_area,
                moment_arm_m=0.25 * outer_chord,
            )
        )

    return tuple(surfaces)


def compute_planform(cfg: GeometryConfig, stations: int = 31) -> WingGeometry:
    """Compute trapezoidal wing metrics and discretized geometry.

    Args:
        cfg: Geometry configuration.
        stations: Number of chord stations across full span.

    Returns:
        Fully derived wing geometry object.
    """

    if stations < 5:
        raise GeometryError("stations must be >= 5")

    b = cfg.wingspan_m
    cr = cfg.root_chord_m
    ct = cfg.tip_chord_m
    semi_span = b / 2.0
    taper = ct / cr

    area = semi_span * (cr + ct)
    aspect_ratio = b * b / area
    mac = (2.0 / 3.0) * cr * (1.0 + taper + taper * taper) / (1.0 + taper)

    sweep_rad = radians(cfg.sweep_deg)

    station_data: list[SpanStation] = []
    quarter_chord_data: list[QuarterChordPoint] = []
    for i in range(stations):
        fraction = -1.0 + 2.0 * i / (stations - 1)
        y = fraction * semi_span
        chord = _local_chord(abs(y), semi_span, cr, ct)
        station_data.append(SpanStation(y_m=y, chord_m=chord))

        x_qc = abs(y) * tan(sweep_rad)
        if y < 0:
            x_qc = x_qc
        quarter_chord_data.append(QuarterChordPoint(x_m=x_qc, y_m=y))

    elevons = _build_elevons(cfg, semi_span)

    return WingGeometry(
        wingspan_m=b,
        root_chord_m=cr,
        tip_chord_m=ct,
        area_m2=area,
        aspect_ratio=aspect_ratio,
        mac_m=mac,
        taper_ratio=taper,
        sweep_deg=cfg.sweep_deg,
        twist_deg=cfg.twist_deg,
        stations=tuple(station_data),
        quarter_chord_line=tuple(quarter_chord_data),
        elevons=elevons,
    )
