"""LBM CFD validation tests."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from wingopt.cfd.lbm import LbmSolver, _initialize_state, _step_numpy, solve_poiseuille_channel
from wingopt.cfd.voxelize import voxelize_wing
from wingopt.config import load_config
from wingopt.geometry import compute_planform, load_airfoil_library


class LbmValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_config(Path("configs/default_wing.toml"))
        self.geometry = compute_planform(self.cfg.geometry)
        self.airfoil = load_airfoil_library(
            Path("data/airfoils"), self.cfg.geometry.airfoil_candidates
        )[self.cfg.geometry.airfoil]

    def test_poiseuille_channel_profile_matches_parabola(self) -> None:
        z, ux = solve_poiseuille_channel(nx=48, nz=24, steps=500)
        interior = slice(2, -2)
        y = z[interior] - z[interior].min()
        h = max(float(y.max()), 1.0)
        expected = y * (h - y)
        expected *= float(np.max(ux[interior])) / max(float(np.max(expected)), 1e-12)
        error = np.linalg.norm(ux[interior] - expected) / max(np.linalg.norm(expected), 1e-12)
        self.assertLess(error, 0.05)

    def test_uniform_flow_no_obstacle_has_zero_force_and_conserves_density(self) -> None:
        resolution = (24, 12, 12)
        solid = np.zeros(resolution, dtype=bool)
        f = _initialize_state(resolution, 0.05, solid)
        initial_mass = float(np.sum(f))
        forces = []
        for _ in range(40):
            f, force = _step_numpy(f, solid, 0.05, 0.7)
            forces.append(force)
        mass = float(np.sum(f))
        self.assertLess(abs(mass - initial_mass) / initial_mass, 1e-6)
        self.assertLess(abs(np.asarray(forces)).max(), 1e-10)

    def test_flat_wing_lift_increases_with_alpha_and_drag_positive(self) -> None:
        solver = LbmSolver(resolution=(36, 24, 24), backend="numpy")
        result0 = solver.solve_wing(
            self.geometry,
            self.airfoil.coordinates,
            alpha_deg=0.0,
            v_ms=16.7,
            air_density=1.2,
            air_viscosity=1.8e-5,
            max_steps=80,
        )
        result8 = solver.solve_wing(
            self.geometry,
            self.airfoil.coordinates,
            alpha_deg=8.0,
            v_ms=16.7,
            air_density=1.2,
            air_viscosity=1.8e-5,
            max_steps=80,
        )
        self.assertGreater(result8.cl, result0.cl)
        self.assertGreater(result8.cd, 0.0)

    def test_voxelizer_nonempty_bounded_and_root_face_occupied(self) -> None:
        vox = voxelize_wing(
            self.geometry,
            self.airfoil.coordinates,
            resolution=(48, 32, 32),
            alpha_deg=4.0,
        )
        self.assertTrue(bool(np.any(vox.solid)))
        occupied = np.argwhere(vox.solid)
        self.assertGreaterEqual(int(occupied.min()), 0)
        self.assertLess(int(occupied[:, 0].max()), vox.solid.shape[0])
        self.assertTrue(bool(np.any(vox.solid[:, 0, :])))

    @unittest.skipUnless(LbmSolver.mlx_available(), "MLX Metal backend unavailable")
    def test_mlx_and_numpy_agree_on_tiny_case(self) -> None:
        kwargs = dict(
            geometry=self.geometry,
            airfoil_coordinates=self.airfoil.coordinates,
            alpha_deg=4.0,
            v_ms=16.7,
            air_density=1.2,
            air_viscosity=1.8e-5,
            max_steps=50,
        )
        numpy_result = LbmSolver(resolution=(24, 16, 16), backend="numpy").solve_wing(**kwargs)
        mlx_result = LbmSolver(resolution=(24, 16, 16), backend="mlx").solve_wing(**kwargs)
        self.assertLess(abs(mlx_result.cd - numpy_result.cd) / numpy_result.cd, 0.02)
        cl_rel_error = abs(mlx_result.cl - numpy_result.cl) / max(abs(numpy_result.cl), 1e-9)
        self.assertLess(cl_rel_error, 0.02)


if __name__ == "__main__":
    unittest.main()
