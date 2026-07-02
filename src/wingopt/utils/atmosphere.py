"""Atmosphere utilities with temperature/altitude/humidity parameterization."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp

R_DRY_AIR = 287.058  # J/(kg*K)
R_WATER_VAPOR = 461.495  # J/(kg*K)
G0 = 9.80665  # m/s^2
ISA_T0 = 288.15  # K
ISA_P0 = 101325.0  # Pa
ISA_L = 0.0065  # K/m
MU0 = 1.716e-5  # Pa*s
SUTHERLAND_T0 = 273.15  # K
SUTHERLAND_C = 111.0  # K


@dataclass(frozen=True)
class AtmosphereState:
    """Computed atmospheric state for a scenario."""

    temperature_k: float
    pressure_pa: float
    density_kgm3: float
    viscosity_pas: float
    relative_humidity: float


def isa_pressure(altitude_m: float) -> float:
    """Return ISA pressure at altitude in meters (troposphere approximation)."""
    if altitude_m < 0:
        altitude_m = 0
    factor = 1.0 - (ISA_L * altitude_m / ISA_T0)
    exponent = G0 / (R_DRY_AIR * ISA_L)
    return ISA_P0 * factor**exponent


def saturation_vapor_pressure_pa(temperature_c: float) -> float:
    """Tetens approximation for saturation vapor pressure."""
    return 610.78 * exp((17.2694 * temperature_c) / (temperature_c + 237.29))


def dynamic_viscosity_pa_s(temperature_k: float) -> float:
    """Sutherland dynamic viscosity model."""
    return MU0 * (temperature_k / SUTHERLAND_T0) ** 1.5 * (SUTHERLAND_T0 + SUTHERLAND_C) / (
        temperature_k + SUTHERLAND_C
    )


def build_atmosphere(
    temperature_c: float,
    altitude_m: float,
    relative_humidity: float,
    pressure_pa: float | None = None,
) -> AtmosphereState:
    """Build atmospheric properties for current conditions.

    Density includes humid-air correction through partial pressures.
    """

    if not (0.0 <= relative_humidity <= 1.0):
        raise ValueError("relative_humidity must be in [0, 1]")

    temp_k = temperature_c + 273.15
    pressure = pressure_pa if pressure_pa is not None else isa_pressure(altitude_m)

    vapor_pressure = relative_humidity * saturation_vapor_pressure_pa(temperature_c)
    dry_air_pressure = max(pressure - vapor_pressure, 1.0)
    density = dry_air_pressure / (R_DRY_AIR * temp_k) + vapor_pressure / (R_WATER_VAPOR * temp_k)
    viscosity = dynamic_viscosity_pa_s(temp_k)

    return AtmosphereState(
        temperature_k=temp_k,
        pressure_pa=pressure,
        density_kgm3=density,
        viscosity_pas=viscosity,
        relative_humidity=relative_humidity,
    )
