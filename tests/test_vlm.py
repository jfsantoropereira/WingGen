"""Vortex-lattice solver validation tests."""

from __future__ import annotations

import sys
import unittest
from math import pi, radians
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wingopt.aero.vlm import VlmSolver  # noqa: E402
from wingopt.config.models import ElevonConfig, GeometryConfig, VlmSettings  # noqa: E402
from wingopt.geometry.planform import WingGeometry, compute_planform  # noqa: E402
from wingopt.utils.gpu import metal_available  # noqa: E402


class VlmTests(unittest.TestCase):
    """Analytical and behavioral checks for the VLM solver."""

    def _geometry(
        self,
        *,
        wingspan_m: float = 2.0,
        root_chord_m: float = 0.25,
        tip_chord_m: float = 0.25,
        sweep_deg: float = 0.0,
        dihedral_deg: float = 0.0,
        root_incidence_deg: float = 0.0,
        tip_incidence_deg: float = 0.0,
    ) -> WingGeometry:
        return compute_planform(
            GeometryConfig(
                wingspan_m=wingspan_m,
                root_chord_m=root_chord_m,
                tip_chord_m=tip_chord_m,
                sweep_deg=sweep_deg,
                dihedral_deg=dihedral_deg,
                root_incidence_deg=root_incidence_deg,
                tip_incidence_deg=tip_incidence_deg,
                airfoil="flat",
                airfoil_candidates=("flat",),
                elevons=ElevonConfig(
                    span_fraction=0.35, chord_fraction=0.25, split_ratio=0.5, num_surfaces=4
                ),
            )
        )

    def test_rectangular_ar8_matches_lifting_line_estimate(self) -> None:
        geometry = self._geometry()
        result = VlmSolver(
            VlmSettings(spanwise_panels=32, chordwise_panels=8, backend="numpy")
        ).solve(
            geometry,
            alpha_deg=5.0,
        )
        alpha_rad = radians(5.0)
        a0 = 2.0 * pi
        llt_cl = a0 * alpha_rad / (1.0 + a0 / (pi * geometry.aspect_ratio))
        self.assertAlmostEqual(result.cl, llt_cl, delta=0.10 * llt_cl)
        inferred_e = result.cl * result.cl / (pi * geometry.aspect_ratio * result.cdi)
        self.assertGreaterEqual(inferred_e, 0.85)
        self.assertLessEqual(inferred_e, 1.02)

    def test_zero_alpha_flat_symmetric_wing_has_zero_lift(self) -> None:
        result = VlmSolver(
            VlmSettings(spanwise_panels=24, chordwise_panels=6, backend="numpy")
        ).solve(
            self._geometry(),
            alpha_deg=0.0,
        )
        self.assertLess(abs(result.cl), 1.0e-6)

    def test_sweep_lowers_lift_curve_slope(self) -> None:
        settings = VlmSettings(spanwise_panels=24, chordwise_panels=8, backend="numpy")
        unswept = self._geometry(sweep_deg=0.0)
        swept = self._geometry(sweep_deg=30.0)
        solver = VlmSolver(settings)
        unswept_slope = solver.solve(unswept, 5.0).cl - solver.solve(unswept, 1.0).cl
        swept_slope = solver.solve(swept, 5.0).cl - solver.solve(swept, 1.0).cl
        self.assertLess(swept_slope, unswept_slope)

    def test_positive_elevon_increases_lift_and_nose_down_moment(self) -> None:
        geometry = self._geometry(root_chord_m=0.35, tip_chord_m=0.15, sweep_deg=25.0)
        solver = VlmSolver(VlmSettings(spanwise_panels=24, chordwise_panels=8, backend="numpy"))
        clean = solver.solve(geometry, alpha_deg=3.0, elevon_deg=0.0)
        deflected = solver.solve(geometry, alpha_deg=3.0, elevon_deg=10.0)
        self.assertGreater(deflected.cl, clean.cl)
        self.assertLess(deflected.cm, clean.cm)

    def test_dihedral_profile_runs_and_returns_span_loading(self) -> None:
        geometry = self._geometry(dihedral_deg=3.0)
        settings = VlmSettings(spanwise_panels=20, chordwise_panels=5, backend="numpy")
        result = VlmSolver(settings).solve(
            geometry,
            alpha_deg=4.0,
            dihedral_profile=((0.0, 0.0), (0.35, 4.0), (0.7, 7.0), (1.0, 3.0)),
        )
        self.assertTrue(all(value == value for value in (result.cl, result.cdi, result.cm)))
        self.assertEqual(len(result.span_loading), settings.spanwise_panels)

    def test_swept_tapered_neutral_point_is_aft_of_unswept_equivalent(self) -> None:
        settings = VlmSettings(spanwise_panels=24, chordwise_panels=8, backend="numpy")
        solver = VlmSolver(settings)
        unswept = solver.solve(
            self._geometry(root_chord_m=0.35, tip_chord_m=0.15, sweep_deg=0.0), 4.0
        )
        swept = solver.solve(
            self._geometry(root_chord_m=0.35, tip_chord_m=0.15, sweep_deg=30.0), 4.0
        )
        self.assertGreater(swept.neutral_point_x_m, unswept.neutral_point_x_m)

    @unittest.skipUnless(metal_available(), "MLX/Metal backend is not available")
    def test_mlx_backend_agrees_with_numpy(self) -> None:
        geometry = self._geometry(
            root_chord_m=0.35, tip_chord_m=0.18, sweep_deg=20.0, dihedral_deg=4.0
        )
        settings_np = VlmSettings(spanwise_panels=16, chordwise_panels=4, backend="numpy")
        settings_mlx = VlmSettings(spanwise_panels=16, chordwise_panels=4, backend="mlx")
        numpy_result = VlmSolver(settings_np).solve(geometry, alpha_deg=4.0, elevon_deg=3.0)
        mlx_result = VlmSolver(settings_mlx).solve(geometry, alpha_deg=4.0, elevon_deg=3.0)
        self.assertEqual(mlx_result.backend, "mlx")
        self.assertAlmostEqual(numpy_result.cl, mlx_result.cl, delta=1.0e-4)
        self.assertAlmostEqual(numpy_result.cdi, mlx_result.cdi, delta=1.0e-4)
        self.assertAlmostEqual(numpy_result.cm, mlx_result.cm, delta=1.0e-4)

    def test_numpy_micro_benchmark_32_by_8_under_one_second(self) -> None:
        geometry = self._geometry(
            root_chord_m=0.35, tip_chord_m=0.18, sweep_deg=20.0, dihedral_deg=4.0
        )
        solver = VlmSolver(VlmSettings(spanwise_panels=32, chordwise_panels=8, backend="numpy"))
        start = perf_counter()
        solver.solve(geometry, alpha_deg=4.0)
        elapsed = perf_counter() - start
        self.assertLess(elapsed, 1.0)


if __name__ == "__main__":
    unittest.main()
