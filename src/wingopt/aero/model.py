"""Aerodynamic model (fast LLT-inspired tier) with trim support."""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, log10, pi, radians, sqrt

from wingopt.geometry.airfoil import AirfoilData
from wingopt.geometry.planform import WingGeometry
from wingopt.utils.atmosphere import AtmosphereState


@dataclass(frozen=True)
class AeroCondition:
    """Flight condition for aerodynamic evaluation."""

    speed_ms: float
    alpha_deg: float
    elevon_deflection_deg: float
    cg_x_fraction_mac: float


@dataclass(frozen=True)
class AeroResult:
    """Aerodynamic outputs at a flight condition."""

    cl: float
    cd: float
    cdi: float
    cdp: float
    cd0: float
    cm: float
    ld: float
    reynolds: float
    trim_elevon_deg: float
    span_loading_npm: tuple[float, ...]


class AeroModel:
    """Tier-1 wing aerodynamic model with airfoil-polar coupling."""

    def __init__(self, geometry: WingGeometry, airfoil: AirfoilData) -> None:
        self.geometry = geometry
        self.airfoil = airfoil

    def _coeffs_2d(self, alpha_deg: float) -> tuple[float, float, float]:
        polar = self.airfoil.interpolate_polar(alpha_deg)
        return polar.cl, polar.cd, polar.cm

    def _cl_3d(self, alpha_deg: float) -> float:
        cl2d, _, _ = self._coeffs_2d(alpha_deg)
        ar = self.geometry.aspect_ratio
        sweep_factor = cos(radians(self.geometry.sweep_deg)) ** 0.7
        finite_wing_factor = ar / (ar + 2.0)
        return cl2d * finite_wing_factor * sweep_factor

    def _estimate_cd0(self, atmosphere: AtmosphereState, speed_ms: float) -> tuple[float, float]:
        # Friction-based drag buildup from representative wetted area and form factors.
        reynolds = atmosphere.density_kgm3 * speed_ms * self.geometry.mac_m / atmosphere.viscosity_pas
        reynolds = max(reynolds, 1.0)
        cf = 0.455 / (log10(reynolds) ** 2.58)
        wetted_area = 1.9 * self.geometry.area_m2
        ff_wing = 1.18
        ff_components = 1.0
        cd0_wing = cf * ff_wing * wetted_area / self.geometry.area_m2
        cd0_components = 0.0022 * ff_components
        return cd0_wing + cd0_components, reynolds

    def evaluate(self, condition: AeroCondition, atmosphere: AtmosphereState, trim: bool = False) -> AeroResult:
        """Evaluate aerodynamic coefficients and derived metrics."""
        if condition.speed_ms <= 0:
            raise ValueError("speed_ms must be > 0")

        cl = self._cl_3d(condition.alpha_deg)
        cl2d, cd_airfoil, cm_airfoil = self._coeffs_2d(condition.alpha_deg)

        e = max(0.6, 1.78 * (1 - 0.045 * self.geometry.aspect_ratio**0.68) - 0.64)
        cdi = cl * cl / (pi * self.geometry.aspect_ratio * e)
        cd0, reynolds = self._estimate_cd0(atmosphere, condition.speed_ms)
        cdp = max(cd_airfoil * 0.22, 0.0)

        cm_delta_per_deg = -0.012
        ac_fraction = 0.25
        cm_no_elevon = cm_airfoil - (condition.cg_x_fraction_mac - ac_fraction) * cl
        trim_elevon = 0.0
        if trim:
            trim_elevon = -cm_no_elevon / cm_delta_per_deg
            cm = 0.0
        else:
            cm = cm_no_elevon + cm_delta_per_deg * condition.elevon_deflection_deg
            trim_elevon = -cm_no_elevon / cm_delta_per_deg

        trim_drag_penalty = 0.00025 * trim_elevon * trim_elevon if trim else 0.0
        cd = cd0 + cdp + cdi + trim_drag_penalty
        ld = cl / cd if cd > 0 else 0.0

        span_loading = self._span_loading_distribution(
            cl=cl,
            atmosphere=atmosphere,
            speed_ms=condition.speed_ms,
        )

        return AeroResult(
            cl=cl,
            cd=cd,
            cdi=cdi,
            cdp=cdp,
            cd0=cd0,
            cm=cm,
            ld=ld,
            reynolds=reynolds,
            trim_elevon_deg=trim_elevon,
            span_loading_npm=span_loading,
        )

    def _span_loading_distribution(
        self,
        cl: float,
        atmosphere: AtmosphereState,
        speed_ms: float,
        stations: int = 21,
    ) -> tuple[float, ...]:
        b = self.geometry.wingspan_m
        q = 0.5 * atmosphere.density_kgm3 * speed_ms * speed_ms
        total_lift = q * self.geometry.area_m2 * cl

        y_values = [(-b / 2.0) + i * b / (stations - 1) for i in range(stations)]
        weights = [sqrt(max(0.0, 1.0 - (2.0 * y / b) ** 2)) for y in y_values]
        area_weight = sum(weights)
        if area_weight <= 0:
            return tuple(0.0 for _ in y_values)
        return tuple(total_lift * w / area_weight / (b / stations) for w in weights)

    def solve_alpha_for_cl(
        self,
        target_cl: float,
        alpha_min_deg: float | None = None,
        alpha_max_deg: float | None = None,
        tolerance: float = 1e-4,
        max_iter: int = 80,
    ) -> float:
        """Solve angle of attack that achieves target CL by bisection."""

        alphas = sorted(point.alpha_deg for point in self.airfoil.polars)
        lo = alphas[0] if alpha_min_deg is None else alpha_min_deg
        hi = alphas[-1] if alpha_max_deg is None else alpha_max_deg
        if lo >= hi:
            raise ValueError("alpha search bounds must satisfy min < max")
        cl_lo = self._cl_3d(lo)
        cl_hi = self._cl_3d(hi)
        if target_cl < min(cl_lo, cl_hi) or target_cl > max(cl_lo, cl_hi):
            raise ValueError(
                f"target_cl={target_cl:.3f} outside achievable range [{cl_lo:.3f}, {cl_hi:.3f}]"
            )

        for _ in range(max_iter):
            mid = 0.5 * (lo + hi)
            cl_mid = self._cl_3d(mid)
            error = cl_mid - target_cl
            if abs(error) <= tolerance:
                return mid
            if (cl_lo - target_cl) * error <= 0:
                hi = mid
                cl_hi = cl_mid
            else:
                lo = mid
                cl_lo = cl_mid

        return 0.5 * (lo + hi)

    def trim_for_level_flight(
        self,
        weight_n: float,
        speed_ms: float,
        atmosphere: AtmosphereState,
        cg_x_fraction_mac: float,
    ) -> AeroResult:
        """Solve alpha/elevon trim for steady level flight at given speed."""

        q = 0.5 * atmosphere.density_kgm3 * speed_ms * speed_ms
        if q <= 0:
            raise ValueError("Dynamic pressure must be > 0")
        target_cl = weight_n / (q * self.geometry.area_m2)
        alpha = self.solve_alpha_for_cl(target_cl)
        condition = AeroCondition(
            speed_ms=speed_ms,
            alpha_deg=alpha,
            elevon_deflection_deg=0.0,
            cg_x_fraction_mac=cg_x_fraction_mac,
        )
        return self.evaluate(condition, atmosphere, trim=True)
