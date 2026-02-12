"""Longitudinal stability and trim checks for flying wings."""

from __future__ import annotations

from dataclasses import dataclass
from math import radians, tan

from wingopt.aero.model import AeroModel
from wingopt.config.models import StabilityConfig
from wingopt.utils.atmosphere import AtmosphereState


@dataclass(frozen=True)
class StabilityResult:
    """Static margin and trim/control outputs."""

    neutral_point_fraction_mac: float
    cg_fraction_mac: float
    forward_cg_fraction_mac: float
    aft_cg_fraction_mac: float
    static_margin: float
    forward_static_margin: float
    aft_static_margin: float
    lateral_stability_index: float
    lateral_stability_ok: bool
    trim_elevon_deg: float
    hinge_moment_nm: float
    min_speed_control_ok: bool
    max_speed_control_ok: bool
    cg_envelope_control_ok: bool


class StabilityAnalyzer:
    """Stability analysis for tailless wing concepts."""

    SERVO_MAX_TORQUE_NM = 0.30  # high-torque 9g metal-gear class
    MIN_LATERAL_STABILITY_INDEX = 0.50

    def __init__(self, aero_model: AeroModel, stability: StabilityConfig) -> None:
        self.aero_model = aero_model
        self.stability = stability

    def estimate_neutral_point_fraction_mac(self) -> float:
        """Approximate neutral-point location as MAC fraction."""
        geom = self.aero_model.geometry
        taper = geom.taper_ratio
        sweep_correction = 0.05 * tan(radians(geom.sweep_deg))
        taper_correction = 0.04 * (0.6 - taper)
        return 0.25 + sweep_correction + taper_correction

    def analyze(
        self,
        atmosphere: AtmosphereState,
        weight_n: float,
        cruise_speed_ms: float,
        min_speed_ms: float,
        max_speed_ms: float,
        cg_fraction_mac: float,
    ) -> StabilityResult:
        """Run static margin + trim/control checks."""

        np_fraction = self.estimate_neutral_point_fraction_mac()
        forward_cg = max(0.05, cg_fraction_mac - self.stability.max_cg_travel_fraction)
        aft_cg = min(0.60, cg_fraction_mac + self.stability.max_cg_travel_fraction)
        if forward_cg >= aft_cg:
            forward_cg = max(0.05, cg_fraction_mac - 0.01)
            aft_cg = min(0.60, cg_fraction_mac + 0.01)

        static_margin = np_fraction - cg_fraction_mac
        forward_static_margin = np_fraction - forward_cg
        aft_static_margin = np_fraction - aft_cg
        lateral_index = self._estimate_lateral_stability_index()
        lateral_ok = lateral_index >= self.MIN_LATERAL_STABILITY_INDEX

        cruise_trim = self._trim_elevon_deg(
            speed_ms=cruise_speed_ms,
            atmosphere=atmosphere,
            weight_n=weight_n,
            cg_fraction_mac=cg_fraction_mac,
        )
        min_trim = self._trim_elevon_deg(
            speed_ms=min_speed_ms,
            atmosphere=atmosphere,
            weight_n=weight_n,
            cg_fraction_mac=cg_fraction_mac,
        )
        max_trim = self._trim_elevon_deg(
            speed_ms=max_speed_ms,
            atmosphere=atmosphere,
            weight_n=weight_n,
            cg_fraction_mac=cg_fraction_mac,
        )

        trim_limit_deg = 25.0
        min_trim_ok = min_trim is not None and abs(min_trim) <= trim_limit_deg
        max_trim_ok = max_trim is not None and abs(max_trim) <= trim_limit_deg

        hinge_moment = 1e9
        if max_trim is not None:
            hinge_moment = self._estimate_hinge_moment(
                speed_ms=max_speed_ms,
                density_kgm3=atmosphere.density_kgm3,
                elevon_deflection_deg=max_trim,
            )
            max_trim_ok = max_trim_ok and hinge_moment <= self.SERVO_MAX_TORQUE_NM

        cg_envelope_control_ok, worst_hinge = self._cg_envelope_control_ok(
            atmosphere=atmosphere,
            weight_n=weight_n,
            min_speed_ms=min_speed_ms,
            max_speed_ms=max_speed_ms,
            cg_points=(forward_cg, aft_cg),
            deflection_limit_deg=trim_limit_deg,
        )
        hinge_moment = max(hinge_moment, worst_hinge)

        return StabilityResult(
            neutral_point_fraction_mac=np_fraction,
            cg_fraction_mac=cg_fraction_mac,
            forward_cg_fraction_mac=forward_cg,
            aft_cg_fraction_mac=aft_cg,
            static_margin=static_margin,
            forward_static_margin=forward_static_margin,
            aft_static_margin=aft_static_margin,
            lateral_stability_index=lateral_index,
            lateral_stability_ok=lateral_ok,
            trim_elevon_deg=(cruise_trim if cruise_trim is not None else 99.0),
            hinge_moment_nm=hinge_moment,
            min_speed_control_ok=min_trim_ok,
            max_speed_control_ok=max_trim_ok,
            cg_envelope_control_ok=cg_envelope_control_ok,
        )

    def constraints_satisfied(self, result: StabilityResult) -> bool:
        """Check if stability and control constraints are satisfied."""
        return (
            result.static_margin >= self.stability.min_static_margin
            and result.aft_static_margin >= self.stability.min_static_margin
            and result.lateral_stability_ok
            and result.min_speed_control_ok
            and result.max_speed_control_ok
            and result.cg_envelope_control_ok
        )

    def _estimate_lateral_stability_index(self) -> float:
        geom = self.aero_model.geometry
        return 0.12 * geom.dihedral_deg + 0.01 * geom.sweep_deg

    def _trim_elevon_deg(
        self,
        speed_ms: float,
        atmosphere: AtmosphereState,
        weight_n: float,
        cg_fraction_mac: float,
    ) -> float | None:
        try:
            trim = self.aero_model.trim_for_level_flight(
                weight_n=weight_n,
                speed_ms=speed_ms,
                atmosphere=atmosphere,
                cg_x_fraction_mac=cg_fraction_mac,
            )
            return trim.trim_elevon_deg
        except Exception:
            return None

    def _cg_envelope_control_ok(
        self,
        atmosphere: AtmosphereState,
        weight_n: float,
        min_speed_ms: float,
        max_speed_ms: float,
        cg_points: tuple[float, float],
        deflection_limit_deg: float,
    ) -> tuple[bool, float]:
        ok = True
        worst_hinge = 0.0
        for cg in cg_points:
            for speed in (min_speed_ms, max_speed_ms):
                trim = self._trim_elevon_deg(
                    speed_ms=speed,
                    atmosphere=atmosphere,
                    weight_n=weight_n,
                    cg_fraction_mac=cg,
                )
                if trim is None or abs(trim) > deflection_limit_deg:
                    ok = False
                    continue
                if speed == max_speed_ms:
                    hinge = self._estimate_hinge_moment(
                        speed_ms=speed,
                        density_kgm3=atmosphere.density_kgm3,
                        elevon_deflection_deg=trim,
                    )
                    worst_hinge = max(worst_hinge, hinge)
                    if hinge > self.SERVO_MAX_TORQUE_NM:
                        ok = False
        return ok, worst_hinge

    def _estimate_hinge_moment(self, speed_ms: float, density_kgm3: float, elevon_deflection_deg: float) -> float:
        q = 0.5 * density_kgm3 * speed_ms * speed_ms
        mean_area = sum(e.area_m2 for e in self.aero_model.geometry.elevons) / len(
            self.aero_model.geometry.elevons
        )
        mean_chord = self.aero_model.geometry.mac_m * 0.22
        ch_delta = 0.008
        return q * mean_area * mean_chord * ch_delta * abs(radians(elevon_deflection_deg))
