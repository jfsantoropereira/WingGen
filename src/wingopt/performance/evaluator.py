"""Mission performance evaluation across atmospheric scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

from wingopt.aero.model import AeroModel
from wingopt.config.models import EnvironmentScenario, MissionConfig
from wingopt.propulsion.model import PropulsionModel
from wingopt.utils.atmosphere import build_atmosphere
from wingopt.utils.units import g_to_kg, kmh_to_ms

G = 9.80665


@dataclass(frozen=True)
class ScenarioPerformance:
    """Performance metrics for one atmosphere scenario."""

    scenario_name: str
    range_km: float
    endurance_h: float
    current_a: float
    drag_n: float
    thrust_available_n: float
    cl_required: float
    cd_total: float
    lift_to_drag: float
    stall_speed_ms: float
    rate_of_climb_ms: float
    cruise_throttle: float
    feasible: bool


@dataclass(frozen=True)
class AggregatePerformance:
    """Aggregated scenario metrics."""

    weighted_range_km: float
    weighted_endurance_h: float
    worst_case_range_km: float
    worst_case_endurance_h: float
    scenarios: tuple[ScenarioPerformance, ...]


class PerformanceEvaluator:
    """Combine aero + propulsion + mass model for mission performance."""

    def __init__(self, aero: AeroModel, propulsion: PropulsionModel, mission: MissionConfig) -> None:
        self.aero = aero
        self.propulsion = propulsion
        self.mission = mission

    def evaluate_scenario(
        self,
        scenario: EnvironmentScenario,
        gross_mass_g: float,
        cg_fraction_mac: float,
    ) -> ScenarioPerformance:
        """Evaluate performance for one environmental scenario."""

        atmosphere = build_atmosphere(
            temperature_c=scenario.temperature_c,
            altitude_m=scenario.altitude_m,
            pressure_pa=scenario.pressure_pa,
            relative_humidity=scenario.relative_humidity,
        )

        speed_cruise = kmh_to_ms(self.mission.cruise_speed_kmh)
        weight_n = g_to_kg(gross_mass_g) * G

        trim = self.aero.trim_for_level_flight(
            weight_n=weight_n,
            speed_ms=speed_cruise,
            atmosphere=atmosphere,
            cg_x_fraction_mac=cg_fraction_mac,
        )

        q = 0.5 * atmosphere.density_kgm3 * speed_cruise * speed_cruise
        drag_n = q * self.aero.geometry.area_m2 * trim.cd
        cl_required = q and (weight_n / (q * self.aero.geometry.area_m2))

        operating = self.propulsion.solve_operating_point(
            airspeed_ms=speed_cruise,
            density_kgm3=atmosphere.density_kgm3,
            soc=0.8,
        )

        thrust_available = operating.thrust_n
        cruise_throttle = drag_n / max(thrust_available, 1e-6)
        cruise_throttle = max(0.0, min(1.2, cruise_throttle))

        # Approximate current scaling with thrust demand.
        cruise_current = operating.current_a * (cruise_throttle**1.15)
        cruise_current = max(cruise_current, self.propulsion.motor.i0_amp)

        endurance = self.propulsion.estimate_endurance(
            cruise_current_a=cruise_current,
            cruise_speed_ms=speed_cruise,
        )

        excess_thrust = thrust_available - drag_n
        climb_rate = max(0.0, excess_thrust * speed_cruise / max(weight_n, 1e-9))

        cl_max = max(p.cl for p in self.aero.airfoil.polars) * (
            self.aero.geometry.aspect_ratio / (self.aero.geometry.aspect_ratio + 2.0)
        )
        stall_speed = sqrt(2.0 * weight_n / (atmosphere.density_kgm3 * self.aero.geometry.area_m2 * cl_max))

        feasible = (
            thrust_available >= drag_n
            and cruise_throttle <= 1.0
            and not operating.current_limit_exceeded
        )

        return ScenarioPerformance(
            scenario_name=scenario.name,
            range_km=endurance.range_km,
            endurance_h=endurance.endurance_h,
            current_a=cruise_current,
            drag_n=drag_n,
            thrust_available_n=thrust_available,
            cl_required=cl_required,
            cd_total=trim.cd,
            lift_to_drag=trim.ld,
            stall_speed_ms=stall_speed,
            rate_of_climb_ms=climb_rate,
            cruise_throttle=cruise_throttle,
            feasible=feasible,
        )

    def aggregate(
        self,
        scenarios: tuple[EnvironmentScenario, ...],
        gross_mass_g: float,
        cg_fraction_mac: float,
    ) -> AggregatePerformance:
        """Run all scenarios and aggregate weighted/worst-case metrics."""

        results = tuple(
            self.evaluate_scenario(
                scenario=scenario,
                gross_mass_g=gross_mass_g,
                cg_fraction_mac=cg_fraction_mac,
            )
            for scenario in scenarios
        )

        weight_sum = sum(s.weight for s in scenarios)
        weighted_range = sum(r.range_km * s.weight for r, s in zip(results, scenarios)) / max(weight_sum, 1e-9)
        weighted_endurance = sum(r.endurance_h * s.weight for r, s in zip(results, scenarios)) / max(
            weight_sum, 1e-9
        )

        worst_range = min(r.range_km for r in results)
        worst_endurance = min(r.endurance_h for r in results)

        return AggregatePerformance(
            weighted_range_km=weighted_range,
            weighted_endurance_h=weighted_endurance,
            worst_case_range_km=worst_range,
            worst_case_endurance_h=worst_endurance,
            scenarios=results,
        )
