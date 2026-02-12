"""Utility package."""

from wingopt.utils.atmosphere import AtmosphereState, build_atmosphere
from wingopt.utils.units import g_to_kg, inch_to_m, kmh_to_ms

__all__ = ["AtmosphereState", "build_atmosphere", "g_to_kg", "inch_to_m", "kmh_to_ms"]
