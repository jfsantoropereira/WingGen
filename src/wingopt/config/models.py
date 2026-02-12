"""Configuration models for the WingGen simulator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ConfigError(ValueError):
    """Raised when configuration content is invalid."""


@dataclass(frozen=True)
class BoundRange:
    """Closed numeric range used by optimizers.

    Attributes:
        minimum: Lower bound (inclusive).
        maximum: Upper bound (inclusive).
    """

    minimum: float
    maximum: float

    def __post_init__(self) -> None:
        if self.minimum > self.maximum:
            msg = f"Invalid range [{self.minimum}, {self.maximum}]"
            raise ConfigError(msg)

    def contains(self, value: float) -> bool:
        """Return whether ``value`` is inside the closed range."""
        return self.minimum <= value <= self.maximum


@dataclass(frozen=True)
class MissionConfig:
    """Mission-level targets.

    Units:
        cruise_speed_kmh: km/h
        max_speed_kmh: km/h
        target_range_km: km
    """

    cruise_speed_kmh: float
    max_speed_kmh: float
    target_range_km: float

    def __post_init__(self) -> None:
        if self.cruise_speed_kmh <= 0 or self.max_speed_kmh <= 0:
            raise ConfigError("Mission speeds must be > 0")
        if self.cruise_speed_kmh >= self.max_speed_kmh:
            raise ConfigError("cruise_speed_kmh must be < max_speed_kmh")
        if self.target_range_km <= 0:
            raise ConfigError("target_range_km must be > 0")


@dataclass(frozen=True)
class EnvironmentScenario:
    """Atmosphere scenario definition.

    Units:
        temperature_c: deg C
        altitude_m: meters
        pressure_pa: Pascal (optional)
        relative_humidity: [0, 1]
        weight: non-negative scalar
    """

    name: str
    temperature_c: float
    altitude_m: float
    pressure_pa: float | None
    relative_humidity: float
    weight: float

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("Scenario name cannot be empty")
        if not (0.0 <= self.relative_humidity <= 1.0):
            raise ConfigError("relative_humidity must be in [0, 1]")
        if self.weight < 0:
            raise ConfigError("Scenario weight must be >= 0")


@dataclass(frozen=True)
class EnvironmentConfig:
    """Environment defaults and optional scenario list."""

    temperature_c: float
    altitude_m: float
    pressure_pa: float | None
    relative_humidity: float
    scenarios: tuple[EnvironmentScenario, ...]

    def __post_init__(self) -> None:
        if not (0.0 <= self.relative_humidity <= 1.0):
            raise ConfigError("environment.relative_humidity must be in [0, 1]")
        if self.scenarios:
            total_weight = sum(s.weight for s in self.scenarios)
            if total_weight <= 0:
                raise ConfigError("Sum of environment scenario weights must be > 0")

    def resolved_scenarios(self) -> tuple[EnvironmentScenario, ...]:
        """Return explicit scenarios, falling back to default singleton."""
        if self.scenarios:
            total = sum(s.weight for s in self.scenarios)
            return tuple(
                EnvironmentScenario(
                    name=s.name,
                    temperature_c=s.temperature_c,
                    altitude_m=s.altitude_m,
                    pressure_pa=s.pressure_pa,
                    relative_humidity=s.relative_humidity,
                    weight=s.weight / total,
                )
                for s in self.scenarios
            )
        return (
            EnvironmentScenario(
                name="default",
                temperature_c=self.temperature_c,
                altitude_m=self.altitude_m,
                pressure_pa=self.pressure_pa,
                relative_humidity=self.relative_humidity,
                weight=1.0,
            ),
        )


@dataclass(frozen=True)
class ElevonConfig:
    """Elevon geometry configuration."""

    span_fraction: float
    chord_fraction: float
    split_ratio: float
    num_surfaces: int

    def __post_init__(self) -> None:
        if not (0.0 < self.span_fraction <= 1.0):
            raise ConfigError("span_fraction must be in (0, 1]")
        if not (0.0 < self.chord_fraction <= 1.0):
            raise ConfigError("chord_fraction must be in (0, 1]")
        if not (0.0 < self.split_ratio < 1.0):
            raise ConfigError("split_ratio must be in (0, 1)")
        if self.num_surfaces != 4:
            raise ConfigError("num_surfaces must be 4 for split elevons")


@dataclass(frozen=True)
class GeometryConfig:
    """Wing geometry inputs."""

    wingspan_m: float
    root_chord_m: float
    tip_chord_m: float
    sweep_deg: float
    dihedral_deg: float
    twist_deg: float
    airfoil: str
    airfoil_candidates: tuple[str, ...]
    elevons: ElevonConfig

    def __post_init__(self) -> None:
        if self.wingspan_m <= 0 or self.root_chord_m <= 0 or self.tip_chord_m <= 0:
            raise ConfigError("Geometry dimensions must be > 0")
        if not (-10.0 <= self.dihedral_deg <= 20.0):
            raise ConfigError("dihedral_deg must be in [-10, 20]")
        if not self.airfoil:
            raise ConfigError("geometry.airfoil cannot be empty")
        if not self.airfoil_candidates:
            raise ConfigError("At least one airfoil candidate is required")


@dataclass(frozen=True)
class MotorConfig:
    """Motor model parameters."""

    name: str
    kv: float
    rm_ohm: float
    i0_amp: float
    max_current_amp: float
    weight_g: float

    def __post_init__(self) -> None:
        if self.kv <= 0 or self.rm_ohm <= 0 or self.max_current_amp <= 0 or self.weight_g <= 0:
            raise ConfigError("Invalid motor parameters")


@dataclass(frozen=True)
class PropellerConfig:
    """Propeller data selection."""

    name: str
    diameter_in: float
    pitch_in: float
    data_file: str

    def __post_init__(self) -> None:
        if self.diameter_in <= 0 or self.pitch_in <= 0:
            raise ConfigError("Propeller diameter and pitch must be > 0")
        if not self.data_file:
            raise ConfigError("propulsion.prop.data_file is required")


@dataclass(frozen=True)
class BatteryConfig:
    """Battery pack parameters."""

    chemistry: str
    cell_capacity_mah: float
    cell_weight_g: float
    cell_max_continuous_a: float
    series: int
    parallel: int
    usable_fraction: float
    cell_internal_resistance_ohm: float

    def __post_init__(self) -> None:
        if self.cell_capacity_mah <= 0 or self.cell_weight_g <= 0 or self.cell_max_continuous_a <= 0:
            raise ConfigError("Invalid battery cell parameters")
        if self.series <= 0 or self.parallel <= 0:
            raise ConfigError("Battery series/parallel must be > 0")
        if not (0.0 < self.usable_fraction <= 1.0):
            raise ConfigError("usable_fraction must be in (0, 1]")
        if self.cell_internal_resistance_ohm <= 0:
            raise ConfigError("cell_internal_resistance_ohm must be > 0")


@dataclass(frozen=True)
class PropulsionConfig:
    """Top-level propulsion configuration."""

    motor: MotorConfig
    prop: PropellerConfig
    battery: BatteryConfig


@dataclass(frozen=True)
class StructureConfig:
    """Structural model inputs."""

    foam_type: str
    foam_density_kgm3: float
    spar_type: str
    spar_od_mm: float
    spar_id_mm: float
    center_plate_thickness_mm: float
    skin: str
    skin_areal_weight_gm2: float
    elevon_material: str
    elevon_thickness_mm: float

    def __post_init__(self) -> None:
        if self.foam_density_kgm3 <= 0:
            raise ConfigError("foam_density_kgm3 must be > 0")
        if self.spar_od_mm <= 0 or self.spar_id_mm < 0 or self.spar_id_mm >= self.spar_od_mm:
            raise ConfigError("Invalid spar dimensions")
        if self.center_plate_thickness_mm <= 0 or self.elevon_thickness_mm <= 0:
            raise ConfigError("Thickness values must be > 0")


@dataclass(frozen=True)
class ComponentsConfig:
    """Discrete component mass model inputs."""

    fc_weight_g: float
    gps_weight_g: float
    rx_weight_g: float
    vtx_weight_g: float
    servo_weight_g: float
    servo_count: int
    esc_weight_g: float
    wiring_weight_g: float
    payload_weight_g: float

    def __post_init__(self) -> None:
        if self.servo_count <= 0:
            raise ConfigError("servo_count must be > 0")
        values = (
            self.fc_weight_g,
            self.gps_weight_g,
            self.rx_weight_g,
            self.vtx_weight_g,
            self.servo_weight_g,
            self.esc_weight_g,
            self.wiring_weight_g,
            self.payload_weight_g,
        )
        if any(v < 0 for v in values):
            raise ConfigError("Component masses must be >= 0")


@dataclass(frozen=True)
class MassConfig:
    """Global mass limits."""

    auw_limit_g: float

    def __post_init__(self) -> None:
        if self.auw_limit_g <= 0:
            raise ConfigError("auw_limit_g must be > 0")


@dataclass(frozen=True)
class StabilityConfig:
    """Static stability constraints."""

    min_static_margin: float
    target_static_margin: float
    max_cg_travel_fraction: float

    def __post_init__(self) -> None:
        if not (0.0 < self.min_static_margin < 1.0):
            raise ConfigError("min_static_margin must be in (0, 1)")
        if not (self.min_static_margin <= self.target_static_margin < 1.0):
            raise ConfigError("target_static_margin must be >= min_static_margin and < 1")
        if not (0.0 < self.max_cg_travel_fraction < 1.0):
            raise ConfigError("max_cg_travel_fraction must be in (0, 1)")


@dataclass(frozen=True)
class WingDesignSpace:
    """Design ranges for wing optimization."""

    wingspan_m: BoundRange
    root_chord_m: BoundRange
    tip_chord_m: BoundRange
    sweep_deg: BoundRange
    dihedral_deg: BoundRange
    twist_deg: BoundRange


@dataclass(frozen=True)
class PropulsionDesignSpace:
    """Design ranges for propulsion optimization."""

    prop_diameter_in: BoundRange
    prop_pitch_in: BoundRange
    battery_parallel: BoundRange


@dataclass(frozen=True)
class EnvironmentDesignSpace:
    """Design ranges for environment and payload sweeps."""

    temperature_c: BoundRange
    altitude_m: BoundRange
    payload_weight_g: BoundRange


@dataclass(frozen=True)
class DesignSpaceConfig:
    """Top-level design space configuration."""

    wing: WingDesignSpace
    propulsion: PropulsionDesignSpace
    environment: EnvironmentDesignSpace


@dataclass(frozen=True)
class OptimizerSettings:
    """Generic optimizer settings."""

    method: str
    max_evaluations: int
    population_size: int
    objective: str
    seed: int

    def __post_init__(self) -> None:
        if self.max_evaluations <= 0 or self.population_size <= 0:
            raise ConfigError("Optimizer max_evaluations and population_size must be > 0")
        if not self.method:
            raise ConfigError("Optimizer method is required")


@dataclass(frozen=True)
class CoordinatorSettings:
    """Coupling settings for wing/propulsion optimization."""

    max_coupling_iterations: int
    convergence_tolerance: float
    aggregation: str

    def __post_init__(self) -> None:
        if self.max_coupling_iterations <= 0:
            raise ConfigError("max_coupling_iterations must be > 0")
        if self.convergence_tolerance <= 0:
            raise ConfigError("convergence_tolerance must be > 0")
        if self.aggregation not in {"weighted_scenario_mean", "worst_case"}:
            raise ConfigError("aggregation must be weighted_scenario_mean or worst_case")


@dataclass(frozen=True)
class OptimizerConfig:
    """Optimizer section containing separated modules + coordinator."""

    wing: OptimizerSettings
    propulsion: OptimizerSettings
    coordinator: CoordinatorSettings


@dataclass(frozen=True)
class WingGenConfig:
    """Root simulator configuration."""

    mission: MissionConfig
    environment: EnvironmentConfig
    geometry: GeometryConfig
    propulsion: PropulsionConfig
    structure: StructureConfig
    components: ComponentsConfig
    mass: MassConfig
    stability: StabilityConfig
    design_space: DesignSpaceConfig
    optimizer: OptimizerConfig


def _bound_from(value: Any, path: str) -> BoundRange:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ConfigError(f"{path} must be [min, max]")
    return BoundRange(float(value[0]), float(value[1]))


def build_config(raw: dict[str, Any]) -> WingGenConfig:
    """Build and validate :class:`WingGenConfig` from parsed TOML data."""
    try:
        mission = MissionConfig(**raw["mission"])

        env_raw = raw["environment"]
        scenarios = tuple(
            EnvironmentScenario(
                name=item["name"],
                temperature_c=float(item.get("temperature_c", env_raw["temperature_c"])),
                altitude_m=float(item.get("altitude_m", env_raw["altitude_m"])),
                pressure_pa=(
                    float(item["pressure_pa"])
                    if item.get("pressure_pa") is not None
                    else (
                        float(env_raw["pressure_pa"])
                        if env_raw.get("pressure_pa") is not None
                        else None
                    )
                ),
                relative_humidity=float(item.get("relative_humidity", env_raw["relative_humidity"])),
                weight=float(item.get("weight", 1.0)),
            )
            for item in env_raw.get("scenarios", [])
        )
        environment = EnvironmentConfig(
            temperature_c=float(env_raw["temperature_c"]),
            altitude_m=float(env_raw["altitude_m"]),
            pressure_pa=(float(env_raw["pressure_pa"]) if env_raw.get("pressure_pa") is not None else None),
            relative_humidity=float(env_raw["relative_humidity"]),
            scenarios=scenarios,
        )

        geom_raw = raw["geometry"]
        geometry = GeometryConfig(
            wingspan_m=float(geom_raw["wingspan_m"]),
            root_chord_m=float(geom_raw["root_chord_m"]),
            tip_chord_m=float(geom_raw["tip_chord_m"]),
            sweep_deg=float(geom_raw["sweep_deg"]),
            dihedral_deg=float(geom_raw["dihedral_deg"]),
            twist_deg=float(geom_raw["twist_deg"]),
            airfoil=str(geom_raw["airfoil"]).lower(),
            airfoil_candidates=tuple(str(a).lower() for a in geom_raw.get("airfoil_candidates", [])),
            elevons=ElevonConfig(**geom_raw["elevons"]),
        )

        prop_raw = raw["propulsion"]
        propulsion = PropulsionConfig(
            motor=MotorConfig(**prop_raw["motor"]),
            prop=PropellerConfig(**prop_raw["prop"]),
            battery=BatteryConfig(**prop_raw["battery"]),
        )

        structure = StructureConfig(**raw["structure"])
        components = ComponentsConfig(**raw["components"])
        mass = MassConfig(**raw["mass"])
        stability = StabilityConfig(**raw["stability"])

        design_raw = raw["design_space"]
        design_space = DesignSpaceConfig(
            wing=WingDesignSpace(
                wingspan_m=_bound_from(design_raw["wing"]["wingspan_m"], "design_space.wing.wingspan_m"),
                root_chord_m=_bound_from(
                    design_raw["wing"]["root_chord_m"], "design_space.wing.root_chord_m"
                ),
                tip_chord_m=_bound_from(
                    design_raw["wing"]["tip_chord_m"], "design_space.wing.tip_chord_m"
                ),
                sweep_deg=_bound_from(design_raw["wing"]["sweep_deg"], "design_space.wing.sweep_deg"),
                dihedral_deg=_bound_from(
                    design_raw["wing"]["dihedral_deg"], "design_space.wing.dihedral_deg"
                ),
                twist_deg=_bound_from(design_raw["wing"]["twist_deg"], "design_space.wing.twist_deg"),
            ),
            propulsion=PropulsionDesignSpace(
                prop_diameter_in=_bound_from(
                    design_raw["propulsion"]["prop_diameter_in"], "design_space.propulsion.prop_diameter_in"
                ),
                prop_pitch_in=_bound_from(
                    design_raw["propulsion"]["prop_pitch_in"], "design_space.propulsion.prop_pitch_in"
                ),
                battery_parallel=_bound_from(
                    design_raw["propulsion"]["battery_parallel"], "design_space.propulsion.battery_parallel"
                ),
            ),
            environment=EnvironmentDesignSpace(
                temperature_c=_bound_from(
                    design_raw["environment"]["temperature_c"], "design_space.environment.temperature_c"
                ),
                altitude_m=_bound_from(
                    design_raw["environment"]["altitude_m"], "design_space.environment.altitude_m"
                ),
                payload_weight_g=_bound_from(
                    design_raw["environment"]["payload_weight_g"], "design_space.environment.payload_weight_g"
                ),
            ),
        )

        opt_raw = raw["optimizer"]
        optimizer = OptimizerConfig(
            wing=OptimizerSettings(**opt_raw["wing"]),
            propulsion=OptimizerSettings(**opt_raw["propulsion"]),
            coordinator=CoordinatorSettings(**opt_raw["coordinator"]),
        )
    except KeyError as exc:
        raise ConfigError(f"Missing required configuration section/key: {exc}") from exc
    except TypeError as exc:
        raise ConfigError(f"Invalid configuration schema: {exc}") from exc

    return WingGenConfig(
        mission=mission,
        environment=environment,
        geometry=geometry,
        propulsion=propulsion,
        structure=structure,
        components=components,
        mass=mass,
        stability=stability,
        design_space=design_space,
        optimizer=optimizer,
    )
