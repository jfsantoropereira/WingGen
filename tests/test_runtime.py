"""Runtime contract smoke test."""

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


if __name__ == "__main__":
    unittest.main()
