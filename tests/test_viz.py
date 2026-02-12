"""STL export tests."""

from pathlib import Path
from collections import Counter
import tempfile
import unittest

from wingopt.config import load_config
from wingopt.geometry import compute_planform, load_airfoil_library
from wingopt.viz import export_wing_stl


class VizTests(unittest.TestCase):
    def test_export_stl(self) -> None:
        cfg = load_config(Path("configs/default_wing.toml"))
        geometry = compute_planform(cfg.geometry)
        airfoils = load_airfoil_library(Path("data/airfoils"), cfg.geometry.airfoil_candidates)
        airfoil = airfoils[cfg.geometry.airfoil]

        with tempfile.TemporaryDirectory() as tmp_dir:
            out = Path(tmp_dir) / "wing.stl"
            export_path = export_wing_stl(
                geometry=geometry,
                airfoil_coordinates=airfoil.coordinates,
                output_path=out,
            )
            self.assertEqual(export_path, out)
            self.assertTrue(out.exists())
            content = out.read_text(encoding="utf-8")
            self.assertIn("solid winggen", content)
            self.assertIn("facet normal", content)
            self.assertGreater(content.count("facet normal"), 1000)

            vertices: list[tuple[float, float, float]] = []
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("vertex "):
                    _, xs, ys, zs = stripped.split()
                    vertices.append((float(xs), float(ys), float(zs)))

            self.assertEqual(len(vertices) % 3, 0)
            edge_counts: Counter[tuple[tuple[float, float, float], tuple[float, float, float]]] = Counter()
            for i in range(0, len(vertices), 3):
                tri = [vertices[i], vertices[i + 1], vertices[i + 2]]
                for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
                    key = tuple(sorted((a, b)))
                    edge_counts[key] += 1

            boundary_edges = sum(1 for count in edge_counts.values() if count == 1)
            self.assertEqual(boundary_edges, 0, "STL mesh has open boundary edges")


if __name__ == "__main__":
    unittest.main()
