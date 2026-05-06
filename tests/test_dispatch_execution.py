import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from omnius.config import CapabilityConfig, GlobalConfig, OmniusConfig, RepoConfig, RunnerSelection
from omnius.dispatcher import dispatch_manifest, initialize_dispatch_log
from omnius.runners.base import PlannerInvocation, RunnerAdapter, RunnerCapability, RunnerHealth, WorkerRequest


class FakeRunner(RunnerAdapter):
    def __init__(self, script_path: Path) -> None:
        self._script_path = script_path

    @property
    def name(self) -> str:
        return "fake"

    def health_check(self) -> RunnerHealth:
        return RunnerHealth(ok=True, summary="fake")

    def discover_capabilities(self) -> dict[str, RunnerCapability]:
        return {}

    def invoke_planner(self, *, task_id: str, prompt: str) -> PlannerInvocation:
        return PlannerInvocation(runner_name=self.name, task_id=task_id, prompt=prompt, plan_text="stub")

    def build_worker_command(self, request: WorkerRequest) -> list[str]:
        return [str(self._script_path), request.prompt]


class DispatchExecutionTests(unittest.TestCase):
    def test_dispatch_manifest_creates_worktree_invokes_worker_and_archives_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = self._create_repo_with_origin(tmp_path)
            home = self._create_workspace_home(tmp_path)
            self._write_local_task(home)

            journal_dir = home / "journal" / "2026-05-05" / "2100"
            journal_dir.mkdir(parents=True, exist_ok=True)
            dispatch_log_path = journal_dir / "dispatch_log.json"
            initialize_dispatch_log(
                dispatch_log_path,
                pipeline_id="pipeline-20260505-210000",
                runner_name="fake",
                repo_slug="example",
                branch="main",
            )

            branch = "omnius/2026-05-05/O00001"
            script_path = self._write_worker_script(
                tmp_path / "success.sh",
                f'printf \'{{"status":"SUCCESS","branch":"{branch}","summary":"done"}}\\n\'\n',
            )
            result = dispatch_manifest(
                manifest=self._manifest(),
                runner=FakeRunner(script_path),
                config=self._config(repo_path),
                workspace_home=home,
                journal_dir=journal_dir,
                dispatch_log_path=dispatch_log_path,
            )

            task_state = result["tasks"]["O00001"]
            self.assertEqual(task_state["status"], "SUCCESS")
            self.assertEqual(task_state["summary"], "done")
            self.assertTrue((journal_dir / "O00001_prompt.md").exists())
            self.assertTrue((journal_dir / "O00001_stdout.json").exists())
            self.assertTrue((journal_dir / "O00001_stderr.log").exists())
            prompt_text = (journal_dir / "O00001_prompt.md").read_text(encoding="utf-8")
            self.assertIn("Source task file: tasks/O00001_add_sample.md", prompt_text)
            self.assertIn("Task body", prompt_text)
            self.assertFalse((repo_path / ".omnius" / "worktrees" / "2026-05-05" / "O00001").exists())
            self.assertTrue((home / "tasks" / "completed" / "O00001_add_sample.md").exists())
            self.assertFalse((home / "tasks" / "O00001_add_sample.md").exists())
            tasks_index = (home / "tasks.md").read_text(encoding="utf-8")
            self.assertNotIn("- O00001: Add sample [file: O00001_add_sample.md]", tasks_index)
            self.assertIn("- 2026-05-05: O00001: Add sample [file: O00001_add_sample.md]", tasks_index)

            branch_list = subprocess.run(
                ["git", "-C", str(repo_path), "branch", "--list", branch],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn(branch, branch_list.stdout)

    def test_dispatch_manifest_marks_crash_when_worker_output_is_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = self._create_repo_with_origin(tmp_path)
            home = self._create_workspace_home(tmp_path)
            self._write_local_task(home)

            journal_dir = home / "journal" / "2026-05-05" / "2100"
            journal_dir.mkdir(parents=True, exist_ok=True)
            dispatch_log_path = journal_dir / "dispatch_log.json"
            initialize_dispatch_log(
                dispatch_log_path,
                pipeline_id="pipeline-20260505-210000",
                runner_name="fake",
                repo_slug="example",
                branch="main",
            )

            script_path = self._write_worker_script(tmp_path / "crash.sh", "printf 'not-json\\n'\n")
            result = dispatch_manifest(
                manifest=self._manifest(),
                runner=FakeRunner(script_path),
                config=self._config(repo_path),
                workspace_home=home,
                journal_dir=journal_dir,
                dispatch_log_path=dispatch_log_path,
            )

            task_state = result["tasks"]["O00001"]
            self.assertEqual(task_state["status"], "CRASH")
            self.assertTrue((home / "tasks" / "O00001_add_sample.md").exists())
            self.assertFalse((repo_path / ".omnius" / "worktrees" / "2026-05-05" / "O00001").exists())

    def test_dispatch_manifest_marks_timeout_and_cleans_up_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = self._create_repo_with_origin(tmp_path)
            home = self._create_workspace_home(tmp_path)
            self._write_local_task(home)

            journal_dir = home / "journal" / "2026-05-05" / "2100"
            journal_dir.mkdir(parents=True, exist_ok=True)
            dispatch_log_path = journal_dir / "dispatch_log.json"
            initialize_dispatch_log(
                dispatch_log_path,
                pipeline_id="pipeline-20260505-210000",
                runner_name="fake",
                repo_slug="example",
                branch="main",
            )

            script_path = self._write_worker_script(tmp_path / "sleep.sh", "sleep 2\n")
            manifest = self._manifest(max_time_minutes=0)
            result = dispatch_manifest(
                manifest=manifest,
                runner=FakeRunner(script_path),
                config=self._config(repo_path),
                workspace_home=home,
                journal_dir=journal_dir,
                dispatch_log_path=dispatch_log_path,
            )

            task_state = result["tasks"]["O00001"]
            self.assertEqual(task_state["status"], "TIMEOUT")
            self.assertTrue((home / "tasks" / "O00001_add_sample.md").exists())
            self.assertFalse((repo_path / ".omnius" / "worktrees" / "2026-05-05" / "O00001").exists())

    def test_dispatch_manifest_reuses_same_day_branch_and_keeps_partial_branch_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = self._create_repo_with_origin(tmp_path)
            home = self._create_workspace_home(tmp_path)
            self._write_local_task(home)

            first_journal_dir = home / "journal" / "2026-05-05" / "2100"
            first_journal_dir.mkdir(parents=True, exist_ok=True)
            first_dispatch_log_path = first_journal_dir / "dispatch_log.json"
            initialize_dispatch_log(
                first_dispatch_log_path,
                pipeline_id="pipeline-20260505-210000",
                runner_name="fake",
                repo_slug="example",
                branch="main",
            )

            partial_script = self._write_worker_script(
                tmp_path / "partial.sh",
                'printf \'{"status":"PARTIAL","notes":"needs follow-up"}\\n\'\n',
            )
            partial_result = dispatch_manifest(
                manifest=self._manifest(),
                runner=FakeRunner(partial_script),
                config=self._config(repo_path),
                workspace_home=home,
                journal_dir=first_journal_dir,
                dispatch_log_path=first_dispatch_log_path,
            )

            branch = "omnius/2026-05-05/O00001"
            partial_state = partial_result["tasks"]["O00001"]
            self.assertEqual(partial_state["status"], "PARTIAL")
            self.assertEqual(partial_state["branch"], branch)
            self.assertTrue((home / "tasks" / "O00001_add_sample.md").exists())

            second_journal_dir = home / "journal" / "2026-05-05" / "2200"
            second_journal_dir.mkdir(parents=True, exist_ok=True)
            second_dispatch_log_path = second_journal_dir / "dispatch_log.json"
            initialize_dispatch_log(
                second_dispatch_log_path,
                pipeline_id="pipeline-20260505-220000",
                runner_name="fake",
                repo_slug="example",
                branch="main",
            )

            success_script = self._write_worker_script(
                tmp_path / "success-after-partial.sh",
                f'printf \'{{"status":"SUCCESS","branch":"{branch}","summary":"done"}}\\n\'\n',
            )
            success_result = dispatch_manifest(
                manifest=self._manifest(),
                runner=FakeRunner(success_script),
                config=self._config(repo_path),
                workspace_home=home,
                journal_dir=second_journal_dir,
                dispatch_log_path=second_dispatch_log_path,
            )

            success_state = success_result["tasks"]["O00001"]
            self.assertEqual(success_state["status"], "SUCCESS")
            self.assertEqual(success_state["branch"], branch)
            self.assertFalse((home / "tasks" / "O00001_add_sample.md").exists())
            self.assertTrue((home / "tasks" / "completed" / "O00001_add_sample.md").exists())

    def _config(self, repo_path: Path) -> OmniusConfig:
        return OmniusConfig(
            global_config=GlobalConfig(
                timezone="America/Los_Angeles",
                pipeline_cron="0 21 * * 0-4",
                pipeline_budget_minutes=540,
                default_task_budget_minutes=120,
                max_consecutive_failures=3,
                notification_backend="none",
            ),
            runner=RunnerSelection(default="codex"),
            capabilities=CapabilityConfig(
                brainstorm="auto",
                review_diff="auto",
                autonomous_testing="auto",
                second_opinion="auto",
            ),
            repos=[
                RepoConfig(
                    slug="example",
                    path=str(repo_path),
                    branch="main",
                    role="author",
                    labels=["omnius"],
                )
            ],
        )

    def _manifest(self, *, max_time_minutes: int = 120) -> dict[str, object]:
        return {
            "run_date": "2026-05-05",
            "journal_dir": "/tmp/journal",
            "summary": "1 task",
            "tasks": [
                {
                    "id": "O00001",
                    "title": "Add sample",
                    "type": "implementation",
                    "repo_slug": "example",
                    "source_ref": "tasks/O00001_add_sample.md",
                    "filename": "O00001_add_sample.md",
                    "max_time_minutes": max_time_minutes,
                    "complexity": "small",
                }
            ],
            "skipped": [],
            "notes": "stub",
        }

    def _create_workspace_home(self, tmp_path: Path) -> Path:
        home = tmp_path / ".omnius"
        (home / "tasks" / "completed").mkdir(parents=True, exist_ok=True)
        return home

    def _write_local_task(self, home: Path) -> None:
        (home / "tasks.md").write_text(
            "## Format\n"
            "- <ID>: <Title> [file: <filename>.md]\n\n"
            "## Active\n"
            "- O00001: Add sample [file: O00001_add_sample.md]\n\n"
            "## Completed\n",
            encoding="utf-8",
        )
        (home / "tasks" / "O00001_add_sample.md").write_text(
            "---\n"
            "title: Add sample\n"
            "repo: example\n"
            "---\n"
            "Task body\n",
            encoding="utf-8",
        )

    def _create_repo_with_origin(self, tmp_path: Path) -> Path:
        origin_path = tmp_path / "origin.git"
        repo_path = tmp_path / "repo"
        subprocess.run(["git", "init", "--bare", str(origin_path)], check=True, capture_output=True, text=True)
        subprocess.run(["git", "init", "-b", "main", str(repo_path)], check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "-C", str(repo_path), "config", "user.email", "omnius@example.com"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_path), "config", "user.name", "Omnius Test"],
            check=True,
            capture_output=True,
            text=True,
        )
        (repo_path / ".gitignore").write_text(".omnius/\n", encoding="utf-8")
        (repo_path / "README.md").write_text("example\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo_path), "add", "."], check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "-C", str(repo_path), "commit", "-m", "initial"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_path), "remote", "add", "origin", str(origin_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_path), "push", "-u", "origin", "main"],
            check=True,
            capture_output=True,
            text=True,
        )
        return repo_path

    def _write_worker_script(self, path: Path, body: str) -> Path:
        path.write_text(f"#!/bin/sh\nset -eu\n{body}", encoding="utf-8")
        path.chmod(0o755)
        return path
