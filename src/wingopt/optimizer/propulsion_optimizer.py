"""Propulsion-focused optimizer module (motor/prop/battery domain)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from wingopt.aero.model import AeroModel
from wingopt.config.models import (
    BatteryConfig,
    GeometryConfig,
    MotorConfig,
    PropellerConfig,
    WingGenConfig,
)
from wingopt.geometry.airfoil import AirfoilData, load_airfoil_library
from wingopt.geometry.planform import compute_planform
from wingopt.optimizer.wing_optimizer import WingCandidate
from wingopt.performance.evaluator import AggregatePerformance, PerformanceEvaluator
from wingopt.propulsion.model import PropulsionError, PropulsionModel


@dataclass(frozen=True)
class PropulsionCandidate:
    """Candidate propulsion solution for a wing."""

    wing_airfoil: str
    wing_signature: str
    wing_score: float
    motor_name: str
    prop_name: str
    battery_parallel: int
    gross_mass_g: float
    cruise_current_a: float
    cruise_throttle: float
    weighted_range_km: float
    weighted_endurance_h: float
    worst_case_range_km: float
    feasible: bool
    score: float


class PropulsionOptimizer:
    """Optimize propulsion hardware against wing-derived performance demand."""

    def __init__(self, config: WingGenConfig, data_dir: str | Path = "data") -> None:
        self.config = config
        self.data_dir = Path(data_dir)
        self.airfoils: dict[str, AirfoilData] = load_airfoil_library(
            self.data_dir / "airfoils",
            candidates=self.config.geometry.airfoil_candidates,
        )

    def optimize_for_wings(self, wings: tuple[WingCandidate, ...], top_k: int = 6) -> tuple[PropulsionCandidate, ...]:
        """Evaluate propulsion candidates for all provided wing designs."""

        if not wings:
            return tuple()

        motors = self._load_motor_catalog(self.data_dir / "motors" / "motors.csv")
        props = sorted((self.data_dir / "props").glob("*.csv"))
        battery_parallel_low = int(round(self.config.design_space.propulsion.battery_parallel.minimum))
        battery_parallel_high = int(round(self.config.design_space.propulsion.battery_parallel.maximum))

        results: list[PropulsionCandidate] = []
        for wing in wings:
            for motor in motors:
                for prop_path in props:
                    for parallel in range(battery_parallel_low, battery_parallel_high + 1):
                        candidate = self._evaluate_combination(
                            wing=wing,
                            motor=motor,
                            prop_path=prop_path,
                            battery_parallel=parallel,
                        )
                        if candidate is not None:
                            results.append(candidate)

        ranked = sorted(results, key=lambda item: item.score, reverse=True)
        return tuple(ranked[:top_k])

    def _evaluate_combination(
        self,
        wing: WingCandidate,
        motor: MotorConfig,
        prop_path: Path,
        battery_parallel: int,
    ) -> PropulsionCandidate | None:
        prop_name = prop_path.stem
        diameter_in, pitch_in = self._parse_prop_dimensions(prop_name)

        battery = BatteryConfig(
            chemistry=self.config.propulsion.battery.chemistry,
            cell_capacity_mah=self.config.propulsion.battery.cell_capacity_mah,
            cell_weight_g=self.config.propulsion.battery.cell_weight_g,
            cell_max_continuous_a=self.config.propulsion.battery.cell_max_continuous_a,
            series=self.config.propulsion.battery.series,
            parallel=battery_parallel,
            usable_fraction=self.config.propulsion.battery.usable_fraction,
            cell_internal_resistance_ohm=self.config.propulsion.battery.cell_internal_resistance_ohm,
        )

        prop = PropellerConfig(
            name=prop_name,
            diameter_in=diameter_in,
            pitch_in=pitch_in,
            data_file=prop_path.name,
        )

        try:
            prop_data = PropulsionModel.load_prop_data(prop_path, prop_name)
            propulsion = PropulsionModel(motor=motor, prop=prop, battery=battery, prop_data=prop_data)
        except Exception:
            return None

        # Rebuild aero/performance model for this wing candidate.
        geometry_cfg = GeometryConfig(
            wingspan_m=wing.wingspan_m,
            root_chord_m=wing.root_chord_m,
            tip_chord_m=wing.tip_chord_m,
            sweep_deg=wing.sweep_deg,
            dihedral_deg=wing.dihedral_deg,
            twist_deg=wing.twist_deg,
            airfoil=wing.airfoil,
            airfoil_candidates=self.config.geometry.airfoil_candidates,
            elevons=self.config.geometry.elevons,
        )

        try:
            geometry = compute_planform(geometry_cfg)
            airfoil = self.airfoils[wing.airfoil]
        except Exception:
            return None

        aero = AeroModel(geometry=geometry, airfoil=airfoil)
        evaluator = PerformanceEvaluator(aero=aero, propulsion=propulsion, mission=self.config.mission)

        non_prop_mass = self._non_propulsion_mass(wing)
        prop_mass = self._estimate_prop_mass_g(diameter_in)
        gross_mass = non_prop_mass + motor.weight_g + propulsion.battery_mass_g() + prop_mass

        if gross_mass > self.config.mass.auw_limit_g:
            feasible = False
        else:
            feasible = True

        try:
            aggregate: AggregatePerformance = evaluator.aggregate(
                scenarios=self.config.environment.resolved_scenarios(),
                gross_mass_g=gross_mass,
                cg_fraction_mac=wing.cg_fraction_mac,
            )
        except (ValueError, PropulsionError):
            return None

        first_scenario = aggregate.scenarios[0]
        cruise_current = first_scenario.current_a
        cruise_throttle = first_scenario.cruise_throttle

        tip_speed_ok = True
        try:
            op = propulsion.solve_operating_point(
                airspeed_ms=wing.envelope.cruise_speed_ms,
                density_kgm3=1.225,
            )
            tip_speed_ok = op.tip_speed_ms < 200.0
        except PropulsionError:
            feasible = False

        if cruise_throttle > 0.70:
            feasible = False
        if any(not scenario.feasible for scenario in aggregate.scenarios):
            feasible = False
        if not tip_speed_ok:
            feasible = False

        score = self._score(
            aggregate=aggregate,
            feasible=feasible,
            gross_mass_g=gross_mass,
            cruise_throttle=cruise_throttle,
        )

        return PropulsionCandidate(
            wing_airfoil=wing.airfoil,
            wing_signature=self._wing_signature(wing),
            wing_score=wing.score,
            motor_name=motor.name,
            prop_name=prop_name,
            battery_parallel=battery_parallel,
            gross_mass_g=gross_mass,
            cruise_current_a=cruise_current,
            cruise_throttle=cruise_throttle,
            weighted_range_km=aggregate.weighted_range_km,
            weighted_endurance_h=aggregate.weighted_endurance_h,
            worst_case_range_km=aggregate.worst_case_range_km,
            feasible=feasible,
            score=score,
        )

    @staticmethod
    def _wing_signature(wing: WingCandidate) -> str:
        return (
            f"{wing.airfoil}|{wing.wingspan_m:.4f}|{wing.root_chord_m:.4f}|{wing.tip_chord_m:.4f}|"
            f"{wing.sweep_deg:.3f}|{wing.dihedral_deg:.3f}|{wing.twist_deg:.3f}|{wing.cg_fraction_mac:.4f}"
        )

    def _non_propulsion_mass(self, wing: WingCandidate) -> float:
        base_battery_mass = (
            self.config.propulsion.battery.series
            * self.config.propulsion.battery.parallel
            * self.config.propulsion.battery.cell_weight_g
            * 1.1
        )
        base_prop_mass = self._estimate_prop_mass_g(self.config.propulsion.prop.diameter_in)
        base_propulsion = self.config.propulsion.motor.weight_g + base_battery_mass + base_prop_mass
        return max(wing.total_mass_g - base_propulsion, 0.0)

    @staticmethod
    def _estimate_prop_mass_g(diameter_in: float) -> float:
        return 0.25 * diameter_in * diameter_in + 2.0

    @staticmethod
    def _parse_prop_dimensions(name: str) -> tuple[float, float]:
        # expected format like apc_9x6 or 9x6
        token = name.split("_")[-1]
        if "x" not in token:
            return 9.0, 6.0
        d, p = token.split("x", maxsplit=1)
        return float(d), float(p)

    @staticmethod
    def _load_motor_catalog(path: Path) -> tuple[MotorConfig, ...]:
        motors: list[MotorConfig] = []
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                motors.append(
                    MotorConfig(
                        name=row["name"],
                        kv=float(row["kv"]),
                        rm_ohm=float(row["rm_ohm"]),
                        i0_amp=float(row["i0_amp"]),
                        max_current_amp=float(row["max_current_amp"]),
                        weight_g=float(row["weight_g"]),
                    )
                )
        return tuple(motors)

    @staticmethod
    def _score(
        aggregate: AggregatePerformance,
        feasible: bool,
        gross_mass_g: float,
        cruise_throttle: float,
    ) -> float:
        score = aggregate.weighted_range_km * 3.0 + aggregate.weighted_endurance_h * 12.0
        score += aggregate.worst_case_range_km * 1.2
        score -= gross_mass_g * 0.02
        score -= max(0.0, cruise_throttle - 0.6) * 150.0
        if not feasible:
            score -= 1000.0
        return score
