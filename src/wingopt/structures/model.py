"""Structural mass estimation and simplified validation checks."""

from __future__ import annotations

from dataclasses import dataclass
from math import pi

from wingopt.config.models import ComponentsConfig, StructureConfig
from wingopt.geometry.planform import WingGeometry


@dataclass(frozen=True)
class StructuralCheck:
    """Structural validation outputs."""

    root_bending_moment_nm: float
    bending_stress_pa: float
    allowable_stress_pa: float
    deflection_m: float
    deflection_limit_m: float
    torsional_frequency_hz: float
    min_required_torsional_frequency_hz: float
    stress_ok: bool
    deflection_ok: bool
    flutter_ok: bool


@dataclass(frozen=True)
class StructureResult:
    """Structure estimation outputs."""

    foam_mass_g: float
    spar_mass_g: float
    center_plate_mass_g: float
    skin_mass_g: float
    elevon_mass_g: float
    adhesive_mass_g: float
    structure_mass_g: float
    components_mass_g: float
    total_empty_mass_g: float
    checks: StructuralCheck


class StructuresModel:
    """Structure mass and load estimation for foam/carbon flying wings."""

    CARBON_DENSITY_KGM3 = 1600.0
    CARBON_E_PA = 120e9
    CARBON_G_PA = 4.5e9
    CARBON_ALLOWABLE_STRESS_PA = 800e6

    def __init__(self, geometry: WingGeometry, structure: StructureConfig, components: ComponentsConfig) -> None:
        self.geometry = geometry
        self.structure = structure
        self.components = components

    def estimate(self, gross_weight_n: float, load_factor: float = 3.5) -> StructureResult:
        """Estimate structure/empty masses and perform simplified load checks."""

        foam_mass = self._foam_mass_g()
        spar_mass = self._spar_mass_g()
        center_mass = self._center_plate_mass_g()
        skin_mass = self._skin_mass_g()
        elevon_mass = self._elevon_mass_g()

        subtotal = foam_mass + spar_mass + center_mass + skin_mass + elevon_mass
        adhesive_mass = 0.08 * subtotal
        structure_mass = subtotal + adhesive_mass

        components_mass = (
            self.components.fc_weight_g
            + self.components.gps_weight_g
            + self.components.rx_weight_g
            + self.components.vtx_weight_g
            + self.components.servo_weight_g * self.components.servo_count
            + self.components.esc_weight_g
            + self.components.wiring_weight_g
        )

        checks = self._structural_check(gross_weight_n=gross_weight_n, load_factor=load_factor)

        return StructureResult(
            foam_mass_g=foam_mass,
            spar_mass_g=spar_mass,
            center_plate_mass_g=center_mass,
            skin_mass_g=skin_mass,
            elevon_mass_g=elevon_mass,
            adhesive_mass_g=adhesive_mass,
            structure_mass_g=structure_mass,
            components_mass_g=components_mass,
            total_empty_mass_g=structure_mass + components_mass,
            checks=checks,
        )

    def _foam_mass_g(self) -> float:
        mean_thickness_m = 0.08 * self.geometry.mac_m
        form_factor = 0.58
        foam_volume = self.geometry.area_m2 * mean_thickness_m * form_factor
        return foam_volume * self.structure.foam_density_kgm3 * 1000.0

    def _spar_mass_g(self) -> float:
        od = self.structure.spar_od_mm / 1000.0
        id_ = self.structure.spar_id_mm / 1000.0
        area = pi * (od * od - id_ * id_) / 4.0
        spar_length = 0.92 * self.geometry.wingspan_m
        volume = area * spar_length
        return volume * self.CARBON_DENSITY_KGM3 * 1000.0

    def _center_plate_mass_g(self) -> float:
        area = 0.25 * self.geometry.root_chord_m * 0.35 * self.geometry.wingspan_m
        thickness = self.structure.center_plate_thickness_mm / 1000.0
        volume = area * thickness
        return volume * self.CARBON_DENSITY_KGM3 * 1000.0

    def _skin_mass_g(self) -> float:
        if self.structure.skin == "none":
            return 0.0
        skin_area = 2.0 * self.geometry.area_m2
        return skin_area * self.structure.skin_areal_weight_gm2

    def _elevon_mass_g(self) -> float:
        total_elevon_area = sum(surface.area_m2 for surface in self.geometry.elevons)
        thickness = self.structure.elevon_thickness_mm / 1000.0
        volume = total_elevon_area * thickness
        return volume * self.CARBON_DENSITY_KGM3 * 1000.0

    def _structural_check(self, gross_weight_n: float, load_factor: float) -> StructuralCheck:
        semi_span = self.geometry.wingspan_m / 2.0
        distributed_load = gross_weight_n * load_factor / self.geometry.wingspan_m
        root_bending_moment = distributed_load * semi_span * semi_span / 2.0

        od = self.structure.spar_od_mm / 1000.0
        id_ = self.structure.spar_id_mm / 1000.0
        inertia = pi * (od**4 - id_**4) / 64.0
        c = od / 2.0
        bending_stress = root_bending_moment * c / max(inertia, 1e-12)

        deflection = distributed_load * semi_span**4 / (8.0 * self.CARBON_E_PA * max(inertia, 1e-12))
        deflection_limit = 0.05 * semi_span

        torsion_constant = pi * (od**4 - id_**4) / 32.0
        torsional_stiffness = self.CARBON_G_PA * torsion_constant / max(semi_span, 1e-6)
        inertia_theta = max(0.03 * (gross_weight_n / 9.81) * semi_span * semi_span, 1e-6)
        torsional_frequency_hz = (1.0 / (2.0 * pi)) * (torsional_stiffness / inertia_theta) ** 0.5

        min_freq = 2.0

        allowable = self.CARBON_ALLOWABLE_STRESS_PA / 1.5
        return StructuralCheck(
            root_bending_moment_nm=root_bending_moment,
            bending_stress_pa=bending_stress,
            allowable_stress_pa=allowable,
            deflection_m=deflection,
            deflection_limit_m=deflection_limit,
            torsional_frequency_hz=torsional_frequency_hz,
            min_required_torsional_frequency_hz=min_freq,
            stress_ok=bending_stress <= allowable,
            deflection_ok=deflection <= deflection_limit,
            flutter_ok=torsional_frequency_hz >= min_freq,
        )
