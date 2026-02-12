"""Runtime contract smoke test."""

import json
from pathlib import Path
import subprocess
import unittest


class RuntimeTests(unittest.TestCase):
    def test_simulate_outputs_contract_events(self) -> None:
        completed = subprocess.run(
            ["python3", "scripts/simulate.py", "--config", "configs/default_wing.toml"],
            check=False,
            capture_output=True,
            text=True,
            cwd=".",
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        self.assertTrue(any('"event": "result"' in line for line in lines))

        result_event = next(json.loads(line) for line in lines if '"event": "result"' in line)
        stl_file = Path(result_event["payload"]["artifacts"]["stl_file"])
        self.assertTrue(stl_file.exists())
        self.assertGreater(stl_file.stat().st_size, 0)
        self.assertIn("airfoil_comparison", result_event["payload"])
        self.assertGreaterEqual(len(result_event["payload"]["airfoil_comparison"]), 3)
        self.assertIn("organic_refinement", result_event["payload"])
        self.assertIsNotNone(result_event["payload"]["organic_refinement"])


if __name__ == "__main__":
    unittest.main()
