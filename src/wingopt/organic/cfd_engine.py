"""CFD engine adapters for organic pass-2 refinement."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wingopt.aero import AeroModel
from wingopt.config.models import WingGenConfig
from wingopt.geometry.airfoil import AirfoilData
from wingopt.geometry.planform import WingGeometry
from wingopt.stability import StabilityAnalyzer
from wingopt.utils.atmosphere import build_atmosphere
from wingopt.utils.units import g_to_kg, kmh_to_ms

G = 9.80665


@dataclass(frozen=True)
class CfdEvaluation:
    """Aggregated aerodynamic/stability outputs from one refinement candidate."""

    drag_coefficient: float
    lift_to_drag: float
    trim_elevon_deg: float
    static_margin: float
    lateral_stability_index: float
    feasible: bool
    source: str


class CfdEngine:
    """Base interface for organic refinement CFD evaluators."""

    def evaluate(
        self,
        geometry: WingGeometry,
        airfoil: AirfoilData,
        cg_fraction_mac: float,
        gross_mass_g: float,
        dihedral_profile: tuple[tuple[float, float], ...],
    ) -> CfdEvaluation:
        raise NotImplementedError


class ProxyCfdEngine(CfdEngine):
    """Fast in-process proxy evaluator based on current aero+stability stack."""

    def __init__(self, config: WingGenConfig) -> None:
        self.config = config

    def evaluate(
        self,
        geometry: WingGeometry,
        airfoil: AirfoilData,
        cg_fraction_mac: float,
        gross_mass_g: float,
        dihedral_profile: tuple[tuple[float, float], ...],
    ) -> CfdEvaluation:
        del dihedral_profile
        aero_model = AeroModel(geometry=geometry, airfoil=airfoil)
        stability = StabilityAnalyzer(aero_model=aero_model, stability=self.config.stability)

        cruise_speed = kmh_to_ms(self.config.mission.cruise_speed_kmh)
        max_speed = kmh_to_ms(self.config.mission.max_speed_kmh)
        weight_n = g_to_kg(gross_mass_g) * G

        weighted_cd = 0.0
        weighted_ld = 0.0
        weighted_trim = 0.0
        weighted_static_margin = 0.0
        weighted_lateral = 0.0
        feasible = True
        weight_sum = 0.0

        for scenario in self.config.environment.resolved_scenarios():
            atmosphere = build_atmosphere(
                temperature_c=scenario.temperature_c,
                altitude_m=scenario.altitude_m,
                pressure_pa=scenario.pressure_pa,
                relative_humidity=scenario.relative_humidity,
            )
            scenario_weight = scenario.weight
            weight_sum += scenario_weight

            try:
                trim = aero_model.trim_for_level_flight(
                    weight_n=weight_n,
                    speed_ms=cruise_speed,
                    atmosphere=atmosphere,
                    cg_x_fraction_mac=cg_fraction_mac,
                )
                stability_result = stability.analyze(
                    atmosphere=atmosphere,
                    weight_n=weight_n,
                    cruise_speed_ms=cruise_speed,
                    min_speed_ms=max(8.0, cruise_speed * 0.60),
                    max_speed_ms=max_speed,
                    cg_fraction_mac=cg_fraction_mac,
                )
            except Exception:
                return CfdEvaluation(
                    drag_coefficient=1e9,
                    lift_to_drag=0.0,
                    trim_elevon_deg=99.0,
                    static_margin=-1.0,
                    lateral_stability_index=0.0,
                    feasible=False,
                    source="proxy",
                )

            feasible = (
                feasible
                and stability.constraints_satisfied(stability_result)
                and trim.ld > 0.0
            )
            weighted_cd += trim.cd * scenario_weight
            weighted_ld += trim.ld * scenario_weight
            weighted_trim += abs(trim.trim_elevon_deg) * scenario_weight
            weighted_static_margin += stability_result.static_margin * scenario_weight
            weighted_lateral += stability_result.lateral_stability_index * scenario_weight

        denom = max(weight_sum, 1e-9)
        return CfdEvaluation(
            drag_coefficient=weighted_cd / denom,
            lift_to_drag=weighted_ld / denom,
            trim_elevon_deg=weighted_trim / denom,
            static_margin=weighted_static_margin / denom,
            lateral_stability_index=weighted_lateral / denom,
            feasible=feasible,
            source="proxy",
        )


class ExternalCommandCfdEngine(CfdEngine):
    """Adapter for externally managed CFD workflows (SU2/OpenFOAM/DAFoam)."""

    def __init__(self, config: WingGenConfig, engine_name: str) -> None:
        self.config = config
        self.engine_name = engine_name

    def evaluate(
        self,
        geometry: WingGeometry,
        airfoil: AirfoilData,
        cg_fraction_mac: float,
        gross_mass_g: float,
        dihedral_profile: tuple[tuple[float, float], ...],
    ) -> CfdEvaluation:
        runner_template = self.config.organic_refinement.cfd.external_runner.strip()
        if not runner_template:
            raise RuntimeError(
                f"organic_refinement engine '{self.engine_name}' requires cfd.external_runner"
            )

        case_root = Path(self.config.organic_refinement.cfd.case_root) / self.engine_name
        case_root.mkdir(parents=True, exist_ok=True)
        signature = abs(
            hash(
                (
                    round(geometry.wingspan_m, 6),
                    round(geometry.root_chord_m, 6),
                    round(geometry.tip_chord_m, 6),
                    tuple((round(eta, 5), round(angle, 5)) for eta, angle in dihedral_profile),
                )
            )
        )
        case_dir = case_root / f"case_{signature}"
        case_dir.mkdir(parents=True, exist_ok=True)

        input_path = case_dir / "input.json"
        result_path = case_dir / self.config.organic_refinement.cfd.result_file

        payload: dict[str, Any] = {
            "engine": self.engine_name,
            "geometry": {
                "wingspan_m": geometry.wingspan_m,
                "root_chord_m": geometry.root_chord_m,
                "tip_chord_m": geometry.tip_chord_m,
                "sweep_deg": geometry.sweep_deg,
                "dihedral_deg": geometry.dihedral_deg,
                "root_incidence_deg": geometry.root_incidence_deg,
                "tip_incidence_deg": geometry.tip_incidence_deg,
            },
            "airfoil": airfoil.name,
            "cg_fraction_mac": cg_fraction_mac,
            "gross_mass_g": gross_mass_g,
            "dihedral_profile": [
                {"eta": eta, "angle_deg": angle_deg}
                for eta, angle_deg in dihedral_profile
            ],
        }
        self._prepare_case_files(case_dir=case_dir, payload=payload)
        input_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        command = runner_template.format(
            engine=self.engine_name,
            case_dir=str(case_dir),
            input_json=str(input_path),
            output_json=str(result_path),
        )
        run_result = subprocess.run(
            shlex.split(command),
            check=False,
            capture_output=True,
            text=True,
        )
        if run_result.returncode != 0:
            return CfdEvaluation(
                drag_coefficient=1e9,
                lift_to_drag=0.0,
                trim_elevon_deg=99.0,
                static_margin=-1.0,
                lateral_stability_index=0.0,
                feasible=False,
                source=self.engine_name,
            )

        if not result_path.exists():
            raise RuntimeError(
                f"CFD runner finished without result file: {result_path}"
            )
        raw = json.loads(result_path.read_text(encoding="utf-8"))
        return CfdEvaluation(
            drag_coefficient=float(raw["drag_coefficient"]),
            lift_to_drag=float(raw["lift_to_drag"]),
            trim_elevon_deg=float(raw["trim_elevon_deg"]),
            static_margin=float(raw["static_margin"]),
            lateral_stability_index=float(raw["lateral_stability_index"]),
            feasible=bool(raw["feasible"]),
            source=self.engine_name,
        )

    def _prepare_case_files(self, case_dir: Path, payload: dict[str, Any]) -> None:
        (case_dir / "mesh").mkdir(parents=True, exist_ok=True)
        (case_dir / "results").mkdir(parents=True, exist_ok=True)
        if self.engine_name == "su2":
            self._write_su2_template(case_dir, payload)
        elif self.engine_name == "openfoam":
            self._write_openfoam_template(case_dir, payload)
        elif self.engine_name == "dafoam":
            self._write_dafoam_template(case_dir, payload)

    @staticmethod
    def _write_su2_template(case_dir: Path, payload: dict[str, Any]) -> None:
        geometry = payload["geometry"]
        su2_cfg = (
            "% WingGen SU2 template\n"
            "SOLVER= RANS\n"
            "MATH_PROBLEM= DIRECT\n"
            "KIND_TURB_MODEL= SA\n"
            "MESH_FILENAME= mesh/wing.su2\n"
            "RESTART_FILENAME= results/restart_flow.dat\n"
            "SURFACE_FLOW_FILENAME= results/surface_flow\n"
            "OUTPUT_FILES= (RESTART, PARAVIEW, SURFACE_PARAVIEW)\n"
            f"% sweep_deg={geometry['sweep_deg']:.4f}\n"
            f"% dihedral_deg={geometry['dihedral_deg']:.4f}\n"
        )
        (case_dir / "su2.cfg").write_text(su2_cfg, encoding="utf-8")

    @staticmethod
    def _write_openfoam_template(case_dir: Path, payload: dict[str, Any]) -> None:
        system_dir = case_dir / "system"
        constant_dir = case_dir / "constant"
        system_dir.mkdir(parents=True, exist_ok=True)
        constant_dir.mkdir(parents=True, exist_ok=True)

        control_dict = (
            "FoamFile\n{\n    version 2.0;\n    format ascii;\n"
            "    class dictionary;\n    object controlDict;\n}\n"
            "application simpleFoam;\n"
            "startFrom startTime;\nstartTime 0;\nstopAt endTime;\nendTime 500;\n"
            "deltaT 1;\nwriteControl timeStep;\nwriteInterval 200;\n"
        )
        transport = (
            "FoamFile\n{\n    version 2.0;\n    format ascii;\n"
            "    class dictionary;\n    object transportProperties;\n}\n"
            "transportModel Newtonian;\nnu 1.5e-05;\n"
        )
        geometry = payload["geometry"]
        note = (
            f"// sweep_deg={geometry['sweep_deg']:.4f}\n"
            f"// dihedral_deg={geometry['dihedral_deg']:.4f}\n"
        )
        (system_dir / "controlDict").write_text(control_dict + note, encoding="utf-8")
        (constant_dir / "transportProperties").write_text(transport, encoding="utf-8")

    @staticmethod
    def _write_dafoam_template(case_dir: Path, payload: dict[str, Any]) -> None:
        run_script = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "echo \"DAFoam template case generated.\"\n"
            "echo \"Provide solver setup and run command here.\"\n"
        )
        script_path = case_dir / "run_dafoam.sh"
        script_path.write_text(run_script, encoding="utf-8")
        script_path.chmod(0o755)
        (case_dir / "dafoam_case.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")



class LbmCfdEngine(CfdEngine):
    """In-process volumetric LBM CFD adapter for organic refinement.

    Stability, trim, and feasibility remain delegated to ``ProxyCfdEngine`` so
    the organic optimizer keeps the existing constraint semantics. The LBM solve
    replaces cruise drag and L/D at the configured cruise condition. Default
    resolution and step count are intentionally coarse for in-loop use; detailed
    verification should instantiate :class:`wingopt.cfd.lbm.LbmSolver` directly.
    """

    def __init__(
        self,
        config: WingGenConfig,
        resolution: tuple[int, int, int] = (96, 64, 48),
        max_steps: int = 1500,
        backend: str = "auto",
        alpha_deg: float = 4.0,
    ) -> None:
        self.config = config
        self.proxy = ProxyCfdEngine(config=config)
        self.resolution = resolution
        self.max_steps = min(max_steps, 1500)
        self.backend = backend
        self.alpha_deg = alpha_deg

    def evaluate(
        self,
        geometry: WingGeometry,
        airfoil: AirfoilData,
        cg_fraction_mac: float,
        gross_mass_g: float,
        dihedral_profile: tuple[tuple[float, float], ...],
    ) -> CfdEvaluation:
        """Evaluate a candidate using proxy constraints and LBM cruise drag."""

        proxy_eval = self.proxy.evaluate(
            geometry=geometry,
            airfoil=airfoil,
            cg_fraction_mac=cg_fraction_mac,
            gross_mass_g=gross_mass_g,
            dihedral_profile=dihedral_profile,
        )
        if not proxy_eval.feasible:
            return CfdEvaluation(
                drag_coefficient=proxy_eval.drag_coefficient,
                lift_to_drag=proxy_eval.lift_to_drag,
                trim_elevon_deg=proxy_eval.trim_elevon_deg,
                static_margin=proxy_eval.static_margin,
                lateral_stability_index=proxy_eval.lateral_stability_index,
                feasible=False,
                source="lbm",
            )

        from wingopt.cfd.lbm import LbmSolver

        cruise_speed = kmh_to_ms(self.config.mission.cruise_speed_kmh)
        scenario = self.config.environment.resolved_scenarios()[0]
        atmosphere = build_atmosphere(
            temperature_c=scenario.temperature_c,
            altitude_m=scenario.altitude_m,
            pressure_pa=scenario.pressure_pa,
            relative_humidity=scenario.relative_humidity,
        )
        solver = LbmSolver(resolution=self.resolution, backend=self.backend)
        result = solver.solve_wing(
            geometry=geometry,
            airfoil_coordinates=airfoil.coordinates,
            alpha_deg=self.alpha_deg,
            v_ms=cruise_speed,
            air_density=atmosphere.density_kgm3,
            air_viscosity=atmosphere.viscosity_pas,
            dihedral_profile=dihedral_profile,
            max_steps=self.max_steps,
        )
        target_cl = (g_to_kg(gross_mass_g) * G) / (
            0.5 * atmosphere.density_kgm3 * cruise_speed * cruise_speed * geometry.area_m2
        )
        lbm_ld = target_cl / max(result.cd, 1e-9)
        return CfdEvaluation(
            drag_coefficient=result.cd,
            lift_to_drag=lbm_ld,
            trim_elevon_deg=proxy_eval.trim_elevon_deg,
            static_margin=proxy_eval.static_margin,
            lateral_stability_index=proxy_eval.lateral_stability_index,
            feasible=proxy_eval.feasible and result.cd > 0.0,
            source="lbm",
        )

def build_cfd_engine(config: WingGenConfig) -> CfdEngine:
    """Build a configured CFD adapter for organic refinement."""

    engine = config.organic_refinement.engine
    if engine == "proxy":
        return ProxyCfdEngine(config=config)
    if engine == "lbm":
        return LbmCfdEngine(config=config)
    return ExternalCommandCfdEngine(config=config, engine_name=engine)
