"""Airfoil coordinate and polar data loading."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


class AirfoilDataError(ValueError):
    """Raised for invalid/missing airfoil data."""


@dataclass(frozen=True)
class AirfoilPoint:
    """2D coordinate for airfoil shape."""

    x: float
    y: float


@dataclass(frozen=True)
class PolarPoint:
    """Single aerodynamic polar sample."""

    alpha_deg: float
    cl: float
    cd: float
    cm: float
    reynolds: float


@dataclass(frozen=True)
class AirfoilData:
    """Aggregated airfoil coordinates and polars."""

    name: str
    coordinates: tuple[AirfoilPoint, ...]
    polars: tuple[PolarPoint, ...]

    def interpolate_polar(self, alpha_deg: float) -> PolarPoint:
        """Linearly interpolate polar values for ``alpha_deg``.

        Raises:
            AirfoilDataError: If alpha is outside available range.
        """

        if len(self.polars) < 2:
            raise AirfoilDataError(f"Not enough polar points for {self.name}")

        sorted_polars = sorted(self.polars, key=lambda p: p.alpha_deg)
        if alpha_deg < sorted_polars[0].alpha_deg or alpha_deg > sorted_polars[-1].alpha_deg:
            raise AirfoilDataError(
                f"alpha {alpha_deg} outside [{sorted_polars[0].alpha_deg}, {sorted_polars[-1].alpha_deg}]"
            )

        for p0, p1 in zip(sorted_polars[:-1], sorted_polars[1:]):
            if p0.alpha_deg <= alpha_deg <= p1.alpha_deg:
                if p1.alpha_deg == p0.alpha_deg:
                    return p0
                t = (alpha_deg - p0.alpha_deg) / (p1.alpha_deg - p0.alpha_deg)
                return PolarPoint(
                    alpha_deg=alpha_deg,
                    cl=p0.cl + t * (p1.cl - p0.cl),
                    cd=p0.cd + t * (p1.cd - p0.cd),
                    cm=p0.cm + t * (p1.cm - p0.cm),
                    reynolds=p0.reynolds + t * (p1.reynolds - p0.reynolds),
                )

        raise AirfoilDataError(f"Could not interpolate polar for alpha={alpha_deg}")


def load_airfoil_coordinates(path: str | Path) -> tuple[str, tuple[AirfoilPoint, ...]]:
    """Load a Selig `.dat` airfoil coordinate file."""
    coord_path = Path(path)
    if not coord_path.exists():
        raise AirfoilDataError(f"Missing coordinate file: {coord_path}")

    lines = [line.strip() for line in coord_path.read_text().splitlines() if line.strip()]
    if len(lines) < 3:
        raise AirfoilDataError(f"Malformed coordinate file: {coord_path}")

    name = lines[0].strip().lower()
    points: list[AirfoilPoint] = []
    for raw in lines[1:]:
        cols = raw.split()
        if len(cols) < 2:
            continue
        points.append(AirfoilPoint(x=float(cols[0]), y=float(cols[1])))

    if len(points) < 4:
        raise AirfoilDataError(f"Insufficient coordinates in {coord_path}")
    return name, tuple(points)


def load_airfoil_polars(path: str | Path) -> tuple[PolarPoint, ...]:
    """Load polar table CSV for a single airfoil."""
    polar_path = Path(path)
    if not polar_path.exists():
        raise AirfoilDataError(f"Missing polar file: {polar_path}")

    points: list[PolarPoint] = []
    with polar_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"alpha", "cl", "cd", "cm", "re"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise AirfoilDataError(f"Polar file missing required columns: {polar_path}")

        for row in reader:
            points.append(
                PolarPoint(
                    alpha_deg=float(row["alpha"]),
                    cl=float(row["cl"]),
                    cd=float(row["cd"]),
                    cm=float(row["cm"]),
                    reynolds=float(row["re"]),
                )
            )

    if len(points) < 4:
        raise AirfoilDataError(f"Polar file has too few rows: {polar_path}")
    return tuple(points)


def load_airfoil_library(base_dir: str | Path, candidates: tuple[str, ...]) -> dict[str, AirfoilData]:
    """Load coordinate + polar data for all candidate airfoils."""
    root = Path(base_dir)
    data: dict[str, AirfoilData] = {}
    for candidate in candidates:
        name = candidate.lower()
        coord_name, coords = load_airfoil_coordinates(root / "coordinates" / f"{name}.dat")
        polars = load_airfoil_polars(root / "polars" / f"{name}.csv")
        data[name] = AirfoilData(name=coord_name, coordinates=coords, polars=polars)
    return data
