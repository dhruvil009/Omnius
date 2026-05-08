import contextlib
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class InstallCommandTests(unittest.TestCase):
    def run_cli(
        self,
        *args: str,
        cwd: Path | None = None,
        env_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(SRC) if not existing_pythonpath else f"{SRC}{os.pathsep}{existing_pythonpath}"
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            [sys.executable, "-m", "omnius", *args],
            cwd=cwd or ROOT,
            text=True,
            capture_output=True,
            env=env,
        )

    def test_install_creates_default_config_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = tmp_path / "repo"
            fake_bin = tmp_path / "bin"
            repo.mkdir()
            fake_bin.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)

            crontab_state = tmp_path / "crontab.txt"
            self._write_executable(
                fake_bin / "crontab",
                (
                    "#!/bin/sh\n"
                    "STATE_FILE=\"$OMNIUS_TEST_CRONTAB_FILE\"\n"
                    "if [ \"$1\" = \"-l\" ]; then\n"
                    "  if [ -f \"$STATE_FILE\" ]; then\n"
                    "    cat \"$STATE_FILE\"\n"
                    "    exit 0\n"
                    "  fi\n"
                    "  exit 1\n"
                    "fi\n"
                    "if [ \"$1\" = \"-\" ]; then\n"
                    "  cat > \"$STATE_FILE\"\n"
                    "  exit 0\n"
                    "fi\n"
                    "exit 2\n"
                ),
            )

            result = self.run_cli(
                "install",
                "--backend",
                "cron",
                "--non-interactive",
                "--runner",
                "codex",
                "--repo-path",
                str(repo),
                cwd=repo,
                env_overrides={
                    "OMNIUS_HOME": str(home),
                    "OMNIUS_TEST_CRONTAB_FILE": str(crontab_state),
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            config_text = (home / "omnius.toml").read_text(encoding="utf-8")
            self.assertIn('pipeline_cron = "0 21 * * 0-4"', config_text)
            self.assertIn('[runner]\ndefault = "codex"', config_text)
            self.assertIn('slug = "repo"', config_text)
            self.assertIn(str(repo), config_text)
            self.assertIn("BEGIN OMNIUS", crontab_state.read_text(encoding="utf-8"))

    def test_uninstall_removes_only_omnius_managed_cron_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = tmp_path / "repo"
            fake_bin = tmp_path / "bin"
            repo.mkdir()
            fake_bin.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            (home / "logs").mkdir(parents=True)
            (home / "omnius.toml").write_text(
                (
                    "[global]\n"
                    'timezone = "America/Los_Angeles"\n'
                    'pipeline_cron = "0 21 * * 0-4"\n'
                    "pipeline_budget_minutes = 540\n"
                    "default_task_budget_minutes = 120\n"
                    "max_consecutive_failures = 3\n"
                    'notification_backend = "none"\n\n'
                    "[runner]\n"
                    'default = "codex"\n\n'
                    "[capabilities]\n"
                    'brainstorm = "auto"\n'
                    'review_diff = "auto"\n'
                    'autonomous_testing = "auto"\n'
                    'second_opinion = "auto"\n\n'
                    "[[repos]]\n"
                    'slug = "repo"\n'
                    f'path = "{repo}"\n'
                    'branch = "main"\n'
                    'role = "primary"\n'
                    "labels = []\n"
                ),
                encoding="utf-8",
            )

            crontab_state = tmp_path / "crontab.txt"
            crontab_state.write_text(
                "# existing\nMAILTO=user@example.com\n# BEGIN OMNIUS\n0 21 * * 0-4 cd $HOME && /tmp/omnius run >> $HOME/.omnius/logs/omnius-cron.log 2>&1\n# END OMNIUS\n",
                encoding="utf-8",
            )
            self._write_executable(
                fake_bin / "crontab",
                (
                    "#!/bin/sh\n"
                    "STATE_FILE=\"$OMNIUS_TEST_CRONTAB_FILE\"\n"
                    "if [ \"$1\" = \"-l\" ]; then\n"
                    "  cat \"$STATE_FILE\"\n"
                    "  exit 0\n"
                    "fi\n"
                    "if [ \"$1\" = \"-\" ]; then\n"
                    "  cat > \"$STATE_FILE\"\n"
                    "  exit 0\n"
                    "fi\n"
                    "exit 2\n"
                ),
            )

            result = self.run_cli(
                "uninstall",
                "--backend",
                "cron",
                env_overrides={
                    "OMNIUS_HOME": str(home),
                    "OMNIUS_TEST_CRONTAB_FILE": str(crontab_state),
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            crontab_text = crontab_state.read_text(encoding="utf-8")
            self.assertIn("MAILTO=user@example.com", crontab_text)
            self.assertNotIn("BEGIN OMNIUS", crontab_text)

    def test_doctor_reports_installed_cron_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = tmp_path / "repo"
            fake_bin = tmp_path / "bin"
            repo.mkdir()
            fake_bin.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)

            crontab_state = tmp_path / "crontab.txt"
            self._write_executable(
                fake_bin / "crontab",
                (
                    "#!/bin/sh\n"
                    "STATE_FILE=\"$OMNIUS_TEST_CRONTAB_FILE\"\n"
                    "if [ \"$1\" = \"-l\" ]; then\n"
                    "  if [ -f \"$STATE_FILE\" ]; then\n"
                    "    cat \"$STATE_FILE\"\n"
                    "    exit 0\n"
                    "  fi\n"
                    "  exit 1\n"
                    "fi\n"
                    "if [ \"$1\" = \"-\" ]; then\n"
                    "  cat > \"$STATE_FILE\"\n"
                    "  exit 0\n"
                    "fi\n"
                    "exit 2\n"
                ),
            )

            install_result = self.run_cli(
                "install-cron",
                "--non-interactive",
                "--runner",
                "codex",
                "--repo-path",
                str(repo),
                cwd=repo,
                env_overrides={
                    "OMNIUS_HOME": str(home),
                    "OMNIUS_TEST_CRONTAB_FILE": str(crontab_state),
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                },
            )
            self.assertEqual(install_result.returncode, 0, install_result.stderr)

            doctor_result = self.run_cli(
                "doctor",
                "--backend",
                "cron",
                env_overrides={
                    "OMNIUS_HOME": str(home),
                    "OMNIUS_TEST_CRONTAB_FILE": str(crontab_state),
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                },
            )

            self.assertEqual(doctor_result.returncode, 0, doctor_result.stderr)
            self.assertIn("backend: cron", doctor_result.stdout)
            self.assertIn("installed: yes", doctor_result.stdout)
            self.assertIn("matches_config: yes", doctor_result.stdout)

    @unittest.skipUnless(sys.platform == "darwin", "launchd backend is macOS-only")
    def test_install_launchd_writes_plist_and_uninstall_removes_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = tmp_path / "repo"
            fake_bin = tmp_path / "bin"
            launchctl_log = tmp_path / "launchctl.log"
            repo.mkdir()
            fake_bin.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            self._write_executable(
                fake_bin / "launchctl",
                (
                    "#!/bin/sh\n"
                    "echo \"$@\" >> \"$OMNIUS_TEST_LAUNCHCTL_LOG\"\n"
                    "exit 0\n"
                ),
            )

            install_result = self.run_cli(
                "install-launchd",
                "--non-interactive",
                "--runner",
                "codex",
                "--repo-path",
                str(repo),
                cwd=repo,
                env_overrides={
                    "OMNIUS_HOME": str(home),
                    "OMNIUS_TEST_LAUNCHCTL_LOG": str(launchctl_log),
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                },
            )

            self.assertEqual(install_result.returncode, 0, install_result.stderr)
            plist_path = home.parent / "Library" / "LaunchAgents" / "dev.omnius.pipeline.plist"
            self.assertTrue(plist_path.exists())
            self.assertIn("bootstrap", launchctl_log.read_text(encoding="utf-8"))

            uninstall_result = self.run_cli(
                "uninstall",
                "--backend",
                "launchd",
                env_overrides={
                    "OMNIUS_HOME": str(home),
                    "OMNIUS_TEST_LAUNCHCTL_LOG": str(launchctl_log),
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                },
            )

            self.assertEqual(uninstall_result.returncode, 0, uninstall_result.stderr)
            self.assertFalse(plist_path.exists())

    def test_bootstrap_helper_installs_user_site_then_runs_omnius_install(self) -> None:
        from omnius.bootstrap import run_bootstrap_install

        calls: list[list[str]] = []

        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        with contextlib.redirect_stdout(io.StringIO()):
            result = run_bootstrap_install(
                repo_root=ROOT,
                python_bin="python3",
                argv=["--backend", "cron"],
                runner=fake_run,
            )

        self.assertEqual(result, 0)
        self.assertEqual(calls[0][:5], ["python3", "-m", "pip", "install", "--user"])
        self.assertEqual(calls[1], ["python3", "-m", "omnius", "install", "--backend", "cron"])

    def test_setup_script_invokes_python_bootstrap_module(self) -> None:
        script_path = ROOT / "omnius_setup.sh"
        self.assertTrue(script_path.exists(), script_path)
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("-m omnius.bootstrap", content)

    def _write_executable(self, path: Path, content: str) -> Path:
        path.write_text(content, encoding="utf-8")
        current_mode = path.stat().st_mode
        path.chmod(current_mode | stat.S_IXUSR)
        return path
