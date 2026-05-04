import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from omnius.cli import main


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
HAS_SETUPTOOLS = importlib.util.find_spec("setuptools") is not None


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

    @unittest.skipIf(sys.version_info < (3, 11), "package requires Python >= 3.11")
    @unittest.skipUnless(HAS_SETUPTOOLS, "setuptools is required for console-script install coverage")
    def test_installed_console_script_prints_help(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source"
            prefix = tmp_path / "prefix"
            source.mkdir()
            shutil.copy2(ROOT / "pyproject.toml", source / "pyproject.toml")
            shutil.copy2(ROOT / "README.md", source / "README.md")
            shutil.copytree(ROOT / "src", source / "src")

            install = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    ".",
                    "--prefix",
                    str(prefix),
                    "--no-build-isolation",
                ],
                cwd=source,
                text=True,
                capture_output=True,
            )
            self.assertEqual(install.returncode, 0, install.stderr)

            scripts_dir = prefix / ("Scripts" if os.name == "nt" else "bin")
            executable = scripts_dir / "omnius"
            self.assertTrue(executable.exists(), executable)
            env = os.environ.copy()
            site_packages = prefix / (
                "Lib/site-packages"
                if os.name == "nt"
                else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
            )
            existing_pythonpath = env.get("PYTHONPATH")
            env["PYTHONPATH"] = (
                str(site_packages)
                if not existing_pythonpath
                else f"{site_packages}{os.pathsep}{existing_pythonpath}"
            )
            result = subprocess.run(
                [str(executable), "--help"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("run", result.stdout)

    def test_top_level_help_lists_run_command(self) -> None:
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("run", result.stdout)

    def test_run_help_mentions_execute_one_pipeline_run(self) -> None:
        result = self.run_cli("run", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Execute one Omnius pipeline run", result.stdout)
