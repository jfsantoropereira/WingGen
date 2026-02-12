"""Propulsion system modeling and motor/prop matching."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from math import pi
from pathlib import Path

from wingopt.config.models import BatteryConfig, MotorConfig, PropellerConfig
from wingopt.utils.units import inch_to_m


@dataclass(frozen=True)
class PropDataPoint:
    """Single prop performance sample."""

    advance_ratio: float
    ct: float
    cp: float
    eta: float


@dataclass(frozen=True)
class PropData:
    """Interpolable propeller performance data."""

    name: str
    points: tuple[PropDataPoint, ...]

    def interpolate(self, advance_ratio: float) -> PropDataPoint:
        """Linearly interpolate CT/CP/eta for requested advance ratio J."""
        data = sorted(self.points, key=lambda p: p.advance_ratio)
        if advance_ratio <= data[0].advance_ratio:
            return data[0]
        if advance_ratio >= data[-1].advance_ratio:
            return data[-1]

        for p0, p1 in zip(data[:-1], data[1:]):
            if p0.advance_ratio <= advance_ratio <= p1.advance_ratio:
                if p1.advance_ratio == p0.advance_ratio:
                    return p0
                t = (advance_ratio - p0.advance_ratio) / (p1.advance_ratio - p0.advance_ratio)
                return PropDataPoint(
                    advance_ratio=advance_ratio,
                    ct=p0.ct + t * (p1.ct - p0.ct),
                    cp=p0.cp + t * (p1.cp - p0.cp),
                    eta=p0.eta + t * (p1.eta - p0.eta),
                )

        return data[-1]


@dataclass(frozen=True)
class OperatingPoint:
    """Solved propulsion operating point at one airspeed."""

    airspeed_ms: float
    thrust_n: float
    current_a: float
    voltage_v: float
    rpm: float
    mechanical_power_w: float
    electrical_power_w: float
    prop_efficiency: float
    motor_efficiency: float
    total_efficiency: float
    tip_speed_ms: float
    current_limit_exceeded: bool


@dataclass(frozen=True)
class EnduranceEstimate:
    """Battery endurance estimate at cruise point."""

    endurance_h: float
    range_km: float


class PropulsionError(ValueError):
    """Raised when propulsion matching cannot be solved."""


class PropulsionModel:
    """Motor-prop-battery matching model."""

    def __init__(self, motor: MotorConfig, prop: PropellerConfig, battery: BatteryConfig, prop_data: PropData) -> None:
        self.motor = motor
        self.prop = prop
        self.battery = battery
        self.prop_data = prop_data

    @staticmethod
    def load_prop_data(path: str | Path, name: str) -> PropData:
        """Load propeller CT/CP table from CSV."""
        csv_path = Path(path)
        if not csv_path.exists():
            raise PropulsionError(f"Missing prop data file: {csv_path}")

        points: list[PropDataPoint] = []
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                points.append(
                    PropDataPoint(
                        advance_ratio=float(row["J"]),
                        ct=float(row["CT"]),
                        cp=float(row["CP"]),
                        eta=float(row["eta"]),
                    )
                )

        if len(points) < 3:
            raise PropulsionError("Prop data must contain at least 3 samples")
        return PropData(name=name, points=tuple(points))

    @property
    def prop_diameter_m(self) -> float:
        return inch_to_m(self.prop.diameter_in)

    @property
    def pack_internal_resistance(self) -> float:
        return self.battery.cell_internal_resistance_ohm * self.battery.series / self.battery.parallel

    @property
    def max_continuous_current(self) -> float:
        return min(self.motor.max_current_amp, self.battery.cell_max_continuous_a * self.battery.parallel)

    def _open_circuit_voltage(self, soc: float) -> float:
        soc_clamped = min(max(soc, 0.0), 1.0)
        cell_v = 3.0 + 1.2 * soc_clamped
        return cell_v * self.battery.series

    def _pack_voltage(self, current_a: float, soc: float) -> float:
        return max(self._open_circuit_voltage(soc) - current_a * self.pack_internal_resistance, 0.1)

    def _motor_kt(self) -> float:
        kv_rads_per_volt = self.motor.kv * 2.0 * pi / 60.0
        return 1.0 / kv_rads_per_volt

    def _prop_thrust_torque(self, rpm: float, airspeed_ms: float, density_kgm3: float) -> tuple[float, float, float]:
        n_rev_s = max(rpm / 60.0, 1e-6)
        diameter = self.prop_diameter_m
        advance_ratio = airspeed_ms / max(n_rev_s * diameter, 1e-6)
        coeffs = self.prop_data.interpolate(advance_ratio)

        thrust = coeffs.ct * density_kgm3 * n_rev_s * n_rev_s * diameter**4
        power = coeffs.cp * density_kgm3 * n_rev_s**3 * diameter**5
        omega = 2.0 * pi * n_rev_s
        torque = power / max(omega, 1e-9)
        return thrust, torque, coeffs.eta

    def solve_operating_point(
        self,
        airspeed_ms: float,
        density_kgm3: float,
        soc: float = 0.8,
        max_iterations: int = 60,
    ) -> OperatingPoint:
        """Solve for current where motor torque equals prop torque."""

        if airspeed_ms < 0:
            raise PropulsionError("airspeed_ms must be >= 0")

        current = max(self.motor.i0_amp + 0.5, 0.5)
        kt = self._motor_kt()

        for _ in range(max_iterations):
            voltage = self._pack_voltage(current, soc)
            rpm = self.motor.kv * max(voltage - current * self.motor.rm_ohm, 0.0)
            rpm = max(rpm, 300.0)

            thrust, prop_torque, prop_eta = self._prop_thrust_torque(rpm, airspeed_ms, density_kgm3)
            current_required = prop_torque / max(kt, 1e-9) + self.motor.i0_amp
            current_required = max(current_required, self.motor.i0_amp)
            current_new = 0.55 * current + 0.45 * current_required

            if abs(current_new - current) < 1e-3:
                current = current_new
                break
            current = current_new

        current_limit_exceeded = current > self.max_continuous_current
        if current_limit_exceeded:
            current = self.max_continuous_current

        voltage = self._pack_voltage(current, soc)
        rpm = self.motor.kv * max(voltage - current * self.motor.rm_ohm, 0.0)
        rpm = max(rpm, 300.0)
        thrust, _, prop_eta = self._prop_thrust_torque(rpm, airspeed_ms, density_kgm3)

        omega = 2.0 * pi * rpm / 60.0
        motor_torque = max((current - self.motor.i0_amp) * kt, 0.0)
        mech_power = motor_torque * omega
        elec_power = voltage * current
        motor_eta = mech_power / elec_power if elec_power > 1e-8 else 0.0
        total_eta = max(0.0, motor_eta * prop_eta)

        tip_speed = omega * self.prop_diameter_m / 2.0

        return OperatingPoint(
            airspeed_ms=airspeed_ms,
            thrust_n=thrust,
            current_a=current,
            voltage_v=voltage,
            rpm=rpm,
            mechanical_power_w=mech_power,
            electrical_power_w=elec_power,
            prop_efficiency=prop_eta,
            motor_efficiency=motor_eta,
            total_efficiency=total_eta,
            tip_speed_ms=tip_speed,
            current_limit_exceeded=current_limit_exceeded,
        )

    def sweep(self, airspeeds_ms: list[float], density_kgm3: float, soc: float = 0.8) -> tuple[OperatingPoint, ...]:
        """Evaluate operating points across airspeed list."""
        return tuple(
            self.solve_operating_point(airspeed_ms=v, density_kgm3=density_kgm3, soc=soc) for v in airspeeds_ms
        )

    def estimate_endurance(self, cruise_current_a: float, cruise_speed_ms: float) -> EnduranceEstimate:
        """Estimate endurance/range from battery usable capacity."""
        if cruise_current_a <= 0:
            raise PropulsionError("cruise_current_a must be > 0")

        pack_capacity_ah = (self.battery.cell_capacity_mah / 1000.0) * self.battery.parallel
        usable_capacity_ah = pack_capacity_ah * self.battery.usable_fraction
        endurance_h = usable_capacity_ah / cruise_current_a
        range_km = cruise_speed_ms * 3.6 * endurance_h
        return EnduranceEstimate(endurance_h=endurance_h, range_km=range_km)

    def battery_mass_g(self) -> float:
        """Pack mass with 10% wiring overhead."""
        cells = self.battery.series * self.battery.parallel
        return cells * self.battery.cell_weight_g * 1.10
