import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from omnius import cli
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
        self.assertIn("install", stdout.getvalue())
        self.assertIn("doctor", stdout.getvalue())
        self.assertIn("uninstall", stdout.getvalue())
        self.assertIn("run", stdout.getvalue())
        self.assertIn("status", stdout.getvalue())
        self.assertIn("stop", stdout.getvalue())
        self.assertIn("recover", stdout.getvalue())

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
            self.assertIn("status", result.stdout)

    def test_top_level_help_lists_run_and_status_commands(self) -> None:
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("install", result.stdout)
        self.assertIn("doctor", result.stdout)
        self.assertIn("uninstall", result.stdout)
        self.assertIn("run", result.stdout)
        self.assertIn("status", result.stdout)
        self.assertIn("stop", result.stdout)
        self.assertIn("recover", result.stdout)

    def test_install_help_mentions_scheduler_setup(self) -> None:
        result = self.run_cli("install", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Install or update the Omnius scheduler setup", result.stdout)
        self.assertIn("--backend", result.stdout)
        self.assertIn("--non-interactive", result.stdout)

    def test_doctor_help_mentions_install_health(self) -> None:
        result = self.run_cli("doctor", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Show Omnius install and scheduler health", result.stdout)

    def test_uninstall_help_mentions_scheduler_removal(self) -> None:
        result = self.run_cli("uninstall", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Remove Omnius-managed scheduler setup", result.stdout)

    def test_install_cron_help_mentions_cron_backend(self) -> None:
        result = self.run_cli("install-cron", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Install or update the Omnius cron schedule", result.stdout)

    def test_install_launchd_help_mentions_launchd_backend(self) -> None:
        result = self.run_cli("install-launchd", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Install or update the Omnius launchd schedule", result.stdout)

    def test_run_help_mentions_execute_one_pipeline_run(self) -> None:
        result = self.run_cli("run", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Execute one Omnius pipeline run", result.stdout)

    def test_status_help_mentions_latest_run_summary(self) -> None:
        result = self.run_cli("status", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Show the latest Omnius run summary", result.stdout)
        self.assertIn("--json", result.stdout)

    def test_stop_help_mentions_runtime_lock_controls(self) -> None:
        result = self.run_cli("stop", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Stop a running Omnius pipeline", result.stdout)
        self.assertIn("--dry-run", result.stdout)
        self.assertIn("--force", result.stdout)

    def test_recover_help_mentions_stale_lock_cleanup(self) -> None:
        result = self.run_cli("recover", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Recover from a stale Omnius runtime lock", result.stdout)

    def test_allocate_journal_dir_appends_suffix_when_base_path_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal_root = Path(tmp) / "journal"
            run_started_at = datetime(2026, 5, 7, 21, 0, 0, tzinfo=timezone.utc)

            first = cli._allocate_journal_dir(journal_root, run_started_at)
            second = cli._allocate_journal_dir(journal_root, run_started_at)

        self.assertEqual(first.name, "210000")
        self.assertEqual(second.name, "210000-01")
