import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliSmokeTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "omnius", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

    def test_top_level_help_lists_run_command(self) -> None:
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("run", result.stdout)

    def test_run_help_mentions_execute_one_pipeline_run(self) -> None:
        result = self.run_cli("run", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Execute one Omnius pipeline run", result.stdout)
