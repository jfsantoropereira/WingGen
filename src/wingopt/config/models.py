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
    root_incidence_deg: float
    tip_incidence_deg: float
    airfoil: str
    airfoil_candidates: tuple[str, ...]
    elevons: ElevonConfig

    def __post_init__(self) -> None:
        if self.wingspan_m <= 0 or self.root_chord_m <= 0 or self.tip_chord_m <= 0:
            raise ConfigError("Geometry dimensions must be > 0")
        if not (-10.0 <= self.dihedral_deg <= 20.0):
            raise ConfigError("dihedral_deg must be in [-10, 20]")
        if not (-15.0 <= self.root_incidence_deg <= 15.0):
            raise ConfigError("root_incidence_deg must be in [-15, 15]")
        if not (-15.0 <= self.tip_incidence_deg <= 15.0):
            raise ConfigError("tip_incidence_deg must be in [-15, 15]")
        if not self.airfoil:
            raise ConfigError("geometry.airfoil cannot be empty")
        if not self.airfoil_candidates:
            raise ConfigError("At least one airfoil candidate is required")

    @property
    def twist_deg(self) -> float:
        """Return root-to-tip incidence difference (washout positive)."""
        return self.root_incidence_deg - self.tip_incidence_deg


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
    root_incidence_deg: BoundRange
    tip_incidence_deg: BoundRange
    cg_fraction_mac: BoundRange


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
class OrganicDihedralProfileConfig:
    """Organic dihedral profile controls for pass-2 refinement."""

    eta_control_points: tuple[float, ...]
    angle_bounds_deg: BoundRange
    root_lock_deg: float
    smoothness_weight: float

    def __post_init__(self) -> None:
        if len(self.eta_control_points) < 3:
            raise ConfigError("organic_refinement.dihedral_profile.eta_control_points needs >= 3 values")
        if abs(self.eta_control_points[0]) > 1e-9 or abs(self.eta_control_points[-1] - 1.0) > 1e-9:
            raise ConfigError("eta_control_points must start at 0.0 and end at 1.0")
        if any(b <= a for a, b in zip(self.eta_control_points, self.eta_control_points[1:])):
            raise ConfigError("eta_control_points must be strictly increasing")
        if self.root_lock_deg < 0:
            raise ConfigError("root_lock_deg must be >= 0")
        if self.smoothness_weight < 0:
            raise ConfigError("smoothness_weight must be >= 0")


@dataclass(frozen=True)
class OrganicExportConfig:
    """Final artifact export settings for organic refinement."""

    output_stl: str
    span_sections: int
    profile_points: int

    def __post_init__(self) -> None:
        if not self.output_stl:
            raise ConfigError("organic_refinement.export.output_stl cannot be empty")
        if self.span_sections < 9:
            raise ConfigError("organic_refinement.export.span_sections must be >= 9")
        if self.profile_points < 41:
            raise ConfigError("organic_refinement.export.profile_points must be >= 41")


@dataclass(frozen=True)
class OrganicCfdConfig:
    """CFD backend adapter settings."""

    mesh_tool: str
    case_root: str
    external_runner: str
    result_file: str

    def __post_init__(self) -> None:
        if not self.mesh_tool:
            raise ConfigError("organic_refinement.cfd.mesh_tool cannot be empty")
        if not self.case_root:
            raise ConfigError("organic_refinement.cfd.case_root cannot be empty")
        if not self.result_file:
            raise ConfigError("organic_refinement.cfd.result_file cannot be empty")


@dataclass(frozen=True)
class OrganicRefinementConfig:
    """Configuration for pass-2 evolutionary organic refinement."""

    enabled: bool
    engine: str
    generations: int
    population_size: int
    elite_count: int
    mutation_rate: float
    crossover_rate: float
    seed: int
    dihedral_profile: OrganicDihedralProfileConfig
    export: OrganicExportConfig
    cfd: OrganicCfdConfig

    def __post_init__(self) -> None:
        if self.engine not in {"proxy", "lbm", "su2", "openfoam", "dafoam"}:
            raise ConfigError("organic_refinement.engine must be proxy, lbm, su2, openfoam, or dafoam")
        if self.generations <= 0 or self.population_size <= 0:
            raise ConfigError("organic_refinement generations and population_size must be > 0")
        if not (0 <= self.elite_count < self.population_size):
            raise ConfigError("organic_refinement.elite_count must be >= 0 and < population_size")
        if not (0.0 <= self.mutation_rate <= 1.0):
            raise ConfigError("organic_refinement.mutation_rate must be in [0, 1]")
        if not (0.0 <= self.crossover_rate <= 1.0):
            raise ConfigError("organic_refinement.crossover_rate must be in [0, 1]")


@dataclass(frozen=True)
class VlmSettings:
    """Vortex-lattice solver discretization and backend settings.

    Attributes:
        spanwise_panels: Panels across the full span.
        chordwise_panels: Panels along the chord.
        backend: Linear-algebra backend: "auto" picks MLX (Metal GPU) when
            available, otherwise numpy.
    """

    spanwise_panels: int = 32
    chordwise_panels: int = 8
    backend: str = "auto"

    def __post_init__(self) -> None:
        if self.spanwise_panels < 4 or self.chordwise_panels < 1:
            raise ConfigError("aero.vlm panel counts too small")
        if self.backend not in {"auto", "mlx", "numpy"}:
            raise ConfigError("aero.vlm.backend must be auto, mlx, or numpy")


@dataclass(frozen=True)
class AeroConfig:
    """Aerodynamic fidelity-tier selection.

    Attributes:
        method: "polar_llt" (fast polar-based lifting-line, default) or
            "vlm" (vortex-lattice method, GPU-accelerated when available).
        vlm: VLM discretization settings, used when method == "vlm" and for
            on-demand high-fidelity re-evaluation of candidate designs.
    """

    method: str = "polar_llt"
    vlm: VlmSettings = VlmSettings()

    def __post_init__(self) -> None:
        if self.method not in {"polar_llt", "vlm"}:
            raise ConfigError("aero.method must be polar_llt or vlm")


@dataclass(frozen=True)
class StudioConfig:
    """Local web studio server settings.

    Attributes:
        host: Bind address for the studio server.
        port: TCP port for the studio server.
        runs_root: Directory (repo-relative or absolute) holding run records.
    """

    host: str = "127.0.0.1"
    port: int = 8151
    runs_root: str = "outputs/runs"

    def __post_init__(self) -> None:
        if not (0 < self.port < 65536):
            raise ConfigError("studio.port must be in (0, 65536)")
        if not self.runs_root:
            raise ConfigError("studio.runs_root cannot be empty")


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
    organic_refinement: OrganicRefinementConfig
    aero: AeroConfig = AeroConfig()
    studio: StudioConfig = StudioConfig()


def _bound_from(value: Any, path: str) -> BoundRange:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ConfigError(f"{path} must be [min, max]")
    return BoundRange(float(value[0]), float(value[1]))


def _default_organic_refinement() -> OrganicRefinementConfig:
    return OrganicRefinementConfig(
        enabled=False,
        engine="proxy",
        generations=8,
        population_size=16,
        elite_count=2,
        mutation_rate=0.25,
        crossover_rate=0.70,
        seed=314,
        dihedral_profile=OrganicDihedralProfileConfig(
            eta_control_points=(0.0, 0.35, 0.70, 1.0),
            angle_bounds_deg=BoundRange(-3.0, 14.0),
            root_lock_deg=1.5,
            smoothness_weight=0.25,
        ),
        export=OrganicExportConfig(
            output_stl="outputs/best_wing_organic_highres.stl",
            span_sections=161,
            profile_points=321,
        ),
        cfd=OrganicCfdConfig(
            mesh_tool="gmsh",
            case_root="outputs/cfd_cases",
            external_runner="",
            result_file="result.json",
        ),
    )


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
        if "root_incidence_deg" in geom_raw and "tip_incidence_deg" in geom_raw:
            root_incidence_deg = float(geom_raw["root_incidence_deg"])
            tip_incidence_deg = float(geom_raw["tip_incidence_deg"])
        else:
            # Backward-compatible fallback: previous schema used `twist_deg` only.
            twist_fallback = float(geom_raw.get("twist_deg", 0.0))
            root_incidence_deg = 0.0
            tip_incidence_deg = -twist_fallback
        geometry = GeometryConfig(
            wingspan_m=float(geom_raw["wingspan_m"]),
            root_chord_m=float(geom_raw["root_chord_m"]),
            tip_chord_m=float(geom_raw["tip_chord_m"]),
            sweep_deg=float(geom_raw["sweep_deg"]),
            dihedral_deg=float(geom_raw["dihedral_deg"]),
            root_incidence_deg=root_incidence_deg,
            tip_incidence_deg=tip_incidence_deg,
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
        wing_design_raw = design_raw["wing"]
        if "root_incidence_deg" in wing_design_raw and "tip_incidence_deg" in wing_design_raw:
            root_incidence_bounds = _bound_from(
                wing_design_raw["root_incidence_deg"],
                "design_space.wing.root_incidence_deg",
            )
            tip_incidence_bounds = _bound_from(
                wing_design_raw["tip_incidence_deg"],
                "design_space.wing.tip_incidence_deg",
            )
        else:
            # Backward-compatible fallback for legacy schema that exposed only twist_deg.
            twist_bounds = _bound_from(
                wing_design_raw["twist_deg"],
                "design_space.wing.twist_deg",
            )
            root_incidence_bounds = BoundRange(0.0, 0.0)
            tip_incidence_bounds = BoundRange(
                -twist_bounds.maximum,
                -twist_bounds.minimum,
            )
        if "cg_fraction_mac" in wing_design_raw:
            cg_bounds = _bound_from(
                wing_design_raw["cg_fraction_mac"],
                "design_space.wing.cg_fraction_mac",
            )
        else:
            cg_bounds = BoundRange(0.16, 0.32)
        design_space = DesignSpaceConfig(
            wing=WingDesignSpace(
                wingspan_m=_bound_from(wing_design_raw["wingspan_m"], "design_space.wing.wingspan_m"),
                root_chord_m=_bound_from(
                    wing_design_raw["root_chord_m"], "design_space.wing.root_chord_m"
                ),
                tip_chord_m=_bound_from(
                    wing_design_raw["tip_chord_m"], "design_space.wing.tip_chord_m"
                ),
                sweep_deg=_bound_from(wing_design_raw["sweep_deg"], "design_space.wing.sweep_deg"),
                dihedral_deg=_bound_from(
                    wing_design_raw["dihedral_deg"], "design_space.wing.dihedral_deg"
                ),
                root_incidence_deg=root_incidence_bounds,
                tip_incidence_deg=tip_incidence_bounds,
                cg_fraction_mac=cg_bounds,
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

        organic_raw = raw.get("organic_refinement")
        if organic_raw is None:
            organic_refinement = _default_organic_refinement()
        else:
            default_organic = _default_organic_refinement()

            profile_raw = organic_raw.get("dihedral_profile", {})
            bounds_raw = profile_raw.get(
                "angle_bounds_deg",
                [
                    default_organic.dihedral_profile.angle_bounds_deg.minimum,
                    default_organic.dihedral_profile.angle_bounds_deg.maximum,
                ],
            )
            dihedral_profile = OrganicDihedralProfileConfig(
                eta_control_points=tuple(
                    float(v)
                    for v in profile_raw.get(
                        "eta_control_points",
                        default_organic.dihedral_profile.eta_control_points,
                    )
                ),
                angle_bounds_deg=_bound_from(
                    bounds_raw,
                    "organic_refinement.dihedral_profile.angle_bounds_deg",
                ),
                root_lock_deg=float(
                    profile_raw.get(
                        "root_lock_deg",
                        default_organic.dihedral_profile.root_lock_deg,
                    )
                ),
                smoothness_weight=float(
                    profile_raw.get(
                        "smoothness_weight",
                        default_organic.dihedral_profile.smoothness_weight,
                    )
                ),
            )

            export_raw = organic_raw.get("export", {})
            export_cfg = OrganicExportConfig(
                output_stl=str(
                    export_raw.get("output_stl", default_organic.export.output_stl)
                ),
                span_sections=int(
                    export_raw.get("span_sections", default_organic.export.span_sections)
                ),
                profile_points=int(
                    export_raw.get("profile_points", default_organic.export.profile_points)
                ),
            )

            cfd_raw = organic_raw.get("cfd", {})
            cfd_cfg = OrganicCfdConfig(
                mesh_tool=str(cfd_raw.get("mesh_tool", default_organic.cfd.mesh_tool)),
                case_root=str(cfd_raw.get("case_root", default_organic.cfd.case_root)),
                external_runner=str(
                    cfd_raw.get("external_runner", default_organic.cfd.external_runner)
                ),
                result_file=str(cfd_raw.get("result_file", default_organic.cfd.result_file)),
            )

            organic_refinement = OrganicRefinementConfig(
                enabled=bool(organic_raw.get("enabled", default_organic.enabled)),
                engine=str(organic_raw.get("engine", default_organic.engine)).lower(),
                generations=int(
                    organic_raw.get("generations", default_organic.generations)
                ),
                population_size=int(
                    organic_raw.get(
                        "population_size",
                        default_organic.population_size,
                    )
                ),
                elite_count=int(
                    organic_raw.get("elite_count", default_organic.elite_count)
                ),
                mutation_rate=float(
                    organic_raw.get("mutation_rate", default_organic.mutation_rate)
                ),
                crossover_rate=float(
                    organic_raw.get("crossover_rate", default_organic.crossover_rate)
                ),
                seed=int(organic_raw.get("seed", default_organic.seed)),
                dihedral_profile=dihedral_profile,
                export=export_cfg,
                cfd=cfd_cfg,
            )
        aero_raw = raw.get("aero", {})
        vlm_raw = aero_raw.get("vlm", {})
        aero = AeroConfig(
            method=str(aero_raw.get("method", "polar_llt")).lower(),
            vlm=VlmSettings(
                spanwise_panels=int(vlm_raw.get("spanwise_panels", 32)),
                chordwise_panels=int(vlm_raw.get("chordwise_panels", 8)),
                backend=str(vlm_raw.get("backend", "auto")).lower(),
            ),
        )

        studio_raw = raw.get("studio", {})
        studio = StudioConfig(
            host=str(studio_raw.get("host", "127.0.0.1")),
            port=int(studio_raw.get("port", 8151)),
            runs_root=str(studio_raw.get("runs_root", "outputs/runs")),
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
        organic_refinement=organic_refinement,
        aero=aero,
        studio=studio,
    )
