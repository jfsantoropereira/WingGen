"""Unit conversion helpers."""

from __future__ import annotations

INCH_TO_M = 0.0254
KMH_TO_MS = 1000.0 / 3600.0
G_TO_KG = 1.0 / 1000.0


def kmh_to_ms(speed_kmh: float) -> float:
    """Convert speed from km/h to m/s."""
    return speed_kmh * KMH_TO_MS


def inch_to_m(value_in: float) -> float:
    """Convert length from inches to meters."""
    return value_in * INCH_TO_M


def g_to_kg(mass_g: float) -> float:
    """Convert mass from grams to kilograms."""
    return mass_g * G_TO_KG
