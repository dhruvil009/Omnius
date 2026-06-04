import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
import re
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
    def run_cli(self, *args: str, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if env_overrides is not None:
            env.update(env_overrides)
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
        self.assertIn("logs", stdout.getvalue())
        self.assertIn("stop", stdout.getvalue())
        self.assertIn("recover", stdout.getvalue())
        self.assertIn("task", stdout.getvalue())

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
        self.assertIn("logs", result.stdout)
        self.assertIn("stop", result.stdout)
        self.assertIn("recover", result.stdout)
        self.assertIn("task", result.stdout)

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

    def test_logs_help_lists_log_subcommands(self) -> None:
        result = self.run_cli("logs", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Show Omnius logs", result.stdout)
        self.assertIn("cron", result.stdout)
        self.assertIn("dispatch", result.stdout)
        self.assertIn("worker", result.stdout)
        self.assertIn("errors", result.stdout)
        self.assertIn("--json", result.stdout)

    def test_logs_json_summarizes_no_runs_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            result = self.run_cli("logs", "--json", env_overrides={"OMNIUS_HOME": str(home)})

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertIsNone(payload["latest_journal"])
        self.assertFalse(payload["scheduler_logs"]["cron"]["exists"])

    def test_logs_dispatch_json_reports_malformed_dispatch_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            journal_dir = home / "journal" / "2026-05-07" / "210000"
            journal_dir.mkdir(parents=True)
            (journal_dir / "dispatch_log.json").write_text("{broken", encoding="utf-8")

            result = self.run_cli("logs", "dispatch", "--json", env_overrides={"OMNIUS_HOME": str(home)})

        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "malformed_dispatch_log")

    def test_logs_dispatch_json_reports_invalid_byte_dispatch_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            journal_dir = home / "journal" / "2026-05-07" / "210000"
            journal_dir.mkdir(parents=True)
            (journal_dir / "dispatch_log.json").write_bytes(b'{"pipeline":{},"bad":"\xff"}')

            result = self.run_cli("logs", "dispatch", "--json", env_overrides={"OMNIUS_HOME": str(home)})

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "malformed_dispatch_log")

    def test_logs_worker_json_includes_stdout_and_stderr_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            journal_dir = home / "journal" / "2026-05-07" / "210000"
            journal_dir.mkdir(parents=True)
            (journal_dir / "dispatch_log.json").write_text(
                json.dumps({"pipeline": {"started_at": "2026-05-07T21:00:00-07:00"}, "tasks": {}}),
                encoding="utf-8",
            )
            (journal_dir / "O00001_stdout.json").write_text('{"status":"FAILURE","error":"boom"}\n', encoding="utf-8")
            (journal_dir / "O00001_stderr.log").write_text("stderr details\n", encoding="utf-8")

            result = self.run_cli("logs", "worker", "O00001", "--json", env_overrides={"OMNIUS_HOME": str(home)})

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["stdout"]["json"]["error"], "boom")
        self.assertEqual(payload["stderr"]["content"], "stderr details\n")

    def test_logs_worker_json_replaces_invalid_stdout_and_stderr_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            journal_dir = home / "journal" / "2026-05-07" / "210000"
            journal_dir.mkdir(parents=True)
            (journal_dir / "dispatch_log.json").write_text(
                json.dumps({"pipeline": {"started_at": "2026-05-07T21:00:00-07:00"}, "tasks": {}}),
                encoding="utf-8",
            )
            (journal_dir / "O00001_stdout.json").write_bytes(b'{"status":"FAILURE","error":\xff}\n')
            (journal_dir / "O00001_stderr.log").write_bytes(b"stderr \xff details\n")

            result = self.run_cli("logs", "worker", "O00001", "--json", env_overrides={"OMNIUS_HOME": str(home)})

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["stdout"]["content"], '{"status":"FAILURE","error":\ufffd}\n')
        self.assertEqual(payload["stdout"]["error"]["code"], "malformed_worker_stdout")
        self.assertEqual(payload["stderr"]["content"], "stderr \ufffd details\n")

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

    def test_task_help_lists_core_task_subcommands(self) -> None:
        result = self.run_cli("task", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("list", result.stdout)
        self.assertIn("show", result.stdout)
        self.assertIn("add", result.stdout)
        self.assertIn("complete", result.stdout)
        self.assertIn("pending", result.stdout)
        self.assertIn("recurring", result.stdout)

    def test_task_add_writes_task_and_task_list_json_reads_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            env = {"OMNIUS_HOME": str(home)}

            add_result = self.run_cli(
                "task",
                "add",
                "--title",
                "Add CLI commands",
                "--repo",
                "omnius",
                "--body",
                "Implement core task command coverage.",
                "--agent",
                "codex",
                "--type",
                "implementation",
                "--max-time",
                "60",
                "--json",
                env_overrides=env,
            )
            self.assertEqual(add_result.returncode, 0, add_result.stderr)
            list_result = self.run_cli("task", "list", "--json", env_overrides=env)
            self.assertEqual(list_result.returncode, 0, list_result.stderr)
            task_file_text = (home / "tasks" / "O00001_add_cli_commands.md").read_text(encoding="utf-8")

        added = json.loads(add_result.stdout)
        self.assertEqual(added["id"], "O00001")
        self.assertEqual(added["status"], "active")
        self.assertEqual(added["metadata"]["agent"], "codex")
        self.assertEqual(added["metadata"]["type"], "implementation")
        self.assertEqual(added["metadata"]["max_time_minutes"], "60")
        listed = json.loads(list_result.stdout)
        self.assertEqual([task["id"] for task in listed], ["O00001"])
        self.assertEqual(listed[0]["title"], "Add CLI commands")
        self.assertIn("max_time_minutes: 60", task_file_text)

    def test_task_show_and_complete_support_human_and_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            env = {"OMNIUS_HOME": str(home)}
            add_result = self.run_cli(
                "task",
                "add",
                "--title",
                "Finish archive path",
                "--repo",
                "omnius",
                "--body",
                "Move this task to completed.",
                env_overrides=env,
            )
            self.assertEqual(add_result.returncode, 0, add_result.stderr)
            show_result = self.run_cli("task", "show", "O00001", env_overrides=env)
            self.assertEqual(show_result.returncode, 0, show_result.stderr)
            complete_result = self.run_cli("task", "complete", "O00001", "--json", env_overrides=env)
            self.assertEqual(complete_result.returncode, 0, complete_result.stderr)
            index_text = (home / "tasks.md").read_text(encoding="utf-8")
            original_task_exists = (home / "tasks" / "O00001_finish_archive_path.md").exists()
            archived_task_exists = (home / "tasks" / "completed" / "O00001_finish_archive_path.md").exists()

        self.assertIn("Task O00001 (active)", show_result.stdout)
        self.assertIn("Move this task to completed.", show_result.stdout)
        completed = json.loads(complete_result.stdout)
        self.assertEqual(completed["status"], "completed")
        self.assertFalse(original_task_exists)
        self.assertTrue(archived_task_exists)
        self.assertRegex(
            index_text,
            re.compile(r"- \d{4}-\d{2}-\d{2}: O00001: Finish archive path \[file: O00001_finish_archive_path.md\]"),
        )

    def test_task_pending_and_recurring_json_list_non_active_queues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            pending_dir = home / "tasks" / "pending_approval"
            recurring_dir = home / "tasks" / "recurring"
            pending_dir.mkdir(parents=True, exist_ok=True)
            recurring_dir.mkdir(parents=True, exist_ok=True)
            (home / "tasks.md").write_text(
                "## Format\n"
                "- <ID>: <Title> [file: <filename>.md]\n\n"
                "## Active\n\n"
                "## Completed\n",
                encoding="utf-8",
            )
            (pending_dir / "O00002_pending_review.md").write_text(
                "---\ntitle: Pending review\nrepo: omnius\n---\nNeeds approval\n",
                encoding="utf-8",
            )
            (recurring_dir / "R00001_daily_cleanup.md").write_text(
                "---\ntitle: Daily cleanup\nrepo: omnius\nschedule: daily\n---\nClean up\n",
                encoding="utf-8",
            )

            pending_result = self.run_cli("task", "pending", "--json", env_overrides={"OMNIUS_HOME": str(home)})
            recurring_result = self.run_cli("task", "recurring", "--json", env_overrides={"OMNIUS_HOME": str(home)})

        self.assertEqual(pending_result.returncode, 0, pending_result.stderr)
        self.assertEqual(recurring_result.returncode, 0, recurring_result.stderr)
        self.assertEqual(json.loads(pending_result.stdout)[0]["status"], "pending")
        self.assertEqual(json.loads(recurring_result.stdout)[0]["status"], "recurring")

    def test_allocate_journal_dir_appends_suffix_when_base_path_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal_root = Path(tmp) / "journal"
            run_started_at = datetime(2026, 5, 7, 21, 0, 0, tzinfo=timezone.utc)

            first = cli._allocate_journal_dir(journal_root, run_started_at)
            second = cli._allocate_journal_dir(journal_root, run_started_at)

        self.assertEqual(first.name, "210000")
        self.assertEqual(second.name, "210000-01")
