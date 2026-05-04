import contextlib
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path

from omnius.cli import main


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class CliSmokeTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(SRC) if not existing_pythonpath else f"{SRC}{os.pathsep}{existing_pythonpath}"
        return subprocess.run(
            [sys.executable, "-m", "omnius", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            env=env,
        )

    def test_main_callable_prints_top_level_help(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = main([])
        self.assertEqual(result, 0)
        self.assertIn("run", stdout.getvalue())

    def test_top_level_help_lists_run_command(self) -> None:
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("run", result.stdout)

    def test_run_help_mentions_execute_one_pipeline_run(self) -> None:
        result = self.run_cli("run", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Execute one Omnius pipeline run", result.stdout)
