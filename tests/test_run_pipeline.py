import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PYTHON = sys.executable


@unittest.skipIf(sys.version_info < (3, 11), "package requires Python >= 3.11")
class RunPipelineTests(unittest.TestCase):
    def test_run_command_executes_local_task_to_completion_and_archives_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_local_task(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={"OMNIUS_CODEX_BIN": str(fake_codex)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)

            journal_dir = journals[0].parent
            self.assertTrue((journal_dir / "preflight.json").exists())
            self.assertTrue((journal_dir / "planner_prompt.md").exists())
            self.assertTrue((journal_dir / "planner_response.json").exists())
            self.assertTrue((journal_dir / "manifest.json").exists())
            self.assertTrue((journal_dir / "dispatch_log.json").exists())
            self.assertTrue((journal_dir / "O00001_prompt.md").exists())
            self.assertTrue((journal_dir / "O00001_stdout.json").exists())
            self.assertTrue((journal_dir / "O00001_stderr.log").exists())

            manifest = json.loads((journal_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["summary"], "1 task(s) planned from local queue")
            self.assertEqual(manifest["skipped"], [])
            self.assertEqual(
                manifest["tasks"],
                [
                    {
                        "id": "O00001",
                        "title": "Add sample",
                        "type": "implementation",
                        "repo_slug": "example",
                        "source": "local_queue",
                        "source_ref": "tasks/O00001_add_sample.md",
                        "filename": "O00001_add_sample.md",
                        "priority": 3,
                        "project_context": "local task queue",
                        "file_paths": [],
                        "quality_phases": ["implement", "verify"],
                        "completion_contract": {
                            "artifact": "committed_branch_or_pr",
                            "archive_on": "SUCCESS_WITH_ARTIFACT",
                        },
                        "max_time_minutes": 120,
                        "complexity": "small",
                    }
                ],
            )

            dispatch_log = json.loads((journal_dir / "dispatch_log.json").read_text(encoding="utf-8"))
            self.assertEqual(dispatch_log["pipeline"]["status"], "completed")
            self.assertEqual(dispatch_log["tasks"]["O00001"]["status"], "SUCCESS")
            self.assertEqual(dispatch_log["tasks"]["O00001"]["summary"], "done")
            self.assertTrue((home / "tasks" / "completed" / "O00001_add_sample.md").exists())
            self.assertFalse((home / "tasks" / "O00001_add_sample.md").exists())
            self.assertFalse((home / "state" / "pipeline.pid").exists())
            self.assertFalse((repo / ".omnius" / "worktrees" / journal_dir.parent.name / "O00001").exists())

    def test_run_command_refuses_to_start_when_pipeline_lock_is_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_local_task(home)
            (home / "state").mkdir(parents=True, exist_ok=True)
            (home / "state" / "pipeline.pid").write_text(
                json.dumps({"pid": os.getpid(), "pipeline_id": "pipeline-existing"}) + "\n",
                encoding="utf-8",
            )
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={"OMNIUS_CODEX_BIN": str(fake_codex)},
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("already running", result.stderr)
            self.assertTrue((home / "tasks" / "O00001_add_sample.md").exists())
            self.assertFalse((home / "tasks" / "completed" / "O00001_add_sample.md").exists())

    def test_status_command_reports_latest_run_after_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_local_task(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={"OMNIUS_CODEX_BIN": str(fake_codex)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            journal_dir = journals[0].parent

            status_result = self._run_status_cli(home=home)

            self.assertEqual(status_result.returncode, 0, status_result.stderr)
            payload = json.loads(status_result.stdout)
            self.assertEqual(payload["date"], journal_dir.parent.name)
            self.assertEqual(payload["pipeline"]["status"], "completed")
            self.assertEqual(payload["tasks"][0]["id"], "O00001")
            self.assertEqual(payload["tasks"][0]["status"], "SUCCESS")
            self.assertEqual(payload["attention"], [])

    def test_run_command_executes_local_task_without_gh_on_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_local_task(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)
            (fake_bin / "gh").unlink()
            git_path = shutil.which("git")
            self.assertIsNotNone(git_path)
            self._write_executable(
                fake_bin / "git",
                f"#!/bin/sh\nexec {git_path} \"$@\"\n",
            )

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={
                    "OMNIUS_CODEX_BIN": str(fake_codex),
                    "PATH": str(fake_bin),
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            preflight = json.loads((journals[0].parent / "preflight.json").read_text(encoding="utf-8"))
            self.assertTrue(preflight["payload"]["gh"]["skipped"])

    def test_run_command_honors_local_task_agent_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_local_task(home, agent="claude")
            fake_bin, _fake_codex = self._write_fake_run_binaries(tmp_path)
            fake_claude = self._write_fake_claude_binary(tmp_path)
            failing_codex = self._write_executable(
                tmp_path / "bin" / "failing-codex",
                "#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo \"codex 1.2.3\"; exit 0; fi\nexit 99\n",
            )
            runner_log = tmp_path / "runner.log"

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={
                    "OMNIUS_CODEX_BIN": str(failing_codex),
                    "OMNIUS_CLAUDE_BIN": str(fake_claude),
                    "OMNIUS_FAKE_RUNNER_LOG": str(runner_log),
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            journal_dir = journals[0].parent
            manifest = json.loads((journal_dir / "manifest.json").read_text(encoding="utf-8"))
            dispatch_log = json.loads((journal_dir / "dispatch_log.json").read_text(encoding="utf-8"))
            status_result = self._run_status_cli(home=home)
            status_payload = json.loads(status_result.stdout)

            self.assertEqual(manifest["tasks"][0]["agent"], "claude")
            self.assertEqual(dispatch_log["tasks"]["O00001"]["agent"], "claude")
            self.assertEqual(status_payload["tasks"][0]["agent"], "claude")
            self.assertEqual(runner_log.read_text(encoding="utf-8").strip(), "claude")

    def test_run_command_populates_prompt_and_manifest_with_due_recurring_and_pending_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_local_task(home)
            self._write_recurring_task(home)
            self._write_pending_approval_task(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={"OMNIUS_CODEX_BIN": str(fake_codex)},
            )

            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)

            journal_dir = journals[0].parent
            planner_prompt = (journal_dir / "planner_prompt.md").read_text(encoding="utf-8")
            manifest = json.loads((journal_dir / "manifest.json").read_text(encoding="utf-8"))

            self.assertIn("RECURRING_TASKS\n", planner_prompt)
            self.assertIn("R00001", planner_prompt)
            self.assertIn("PENDING_APPROVAL\n", planner_prompt)
            self.assertIn("proposed_follow_up.md", planner_prompt)
            self.assertEqual(manifest["summary"], "2 task(s) planned from local and recurring queues")
            self.assertEqual(
                manifest["tasks"],
                [
                    {
                        "id": "O00001",
                        "title": "Add sample",
                        "type": "implementation",
                        "repo_slug": "example",
                        "source": "local_queue",
                        "source_ref": "tasks/O00001_add_sample.md",
                        "filename": "O00001_add_sample.md",
                        "priority": 3,
                        "project_context": "local task queue",
                        "file_paths": [],
                        "quality_phases": ["implement", "verify"],
                        "completion_contract": {
                            "artifact": "committed_branch_or_pr",
                            "archive_on": "SUCCESS_WITH_ARTIFACT",
                        },
                        "max_time_minutes": 120,
                        "complexity": "small",
                    },
                    {
                        "id": "R00001",
                        "title": "Daily cleanup",
                        "type": "research",
                        "repo_slug": "example",
                        "source": "recurring_queue",
                        "source_ref": "tasks/recurring/R00001_daily_cleanup.md",
                        "filename": "R00001_daily_cleanup.md",
                        "priority": 4,
                        "project_context": "recurring task queue",
                        "file_paths": [],
                        "quality_phases": ["implement", "verify"],
                        "completion_contract": {
                            "artifact": "committed_branch_or_pr",
                            "archive_on": "SUCCESS_WITH_ARTIFACT",
                        },
                        "max_time_minutes": 45,
                        "complexity": "medium",
                    },
                ],
            )
            self.assertTrue((journal_dir / "planner_response.json").exists())
            self.assertTrue((journal_dir / "daily_brief.md").exists())
            self.assertEqual(
                (home / "daily_brief.md").read_text(encoding="utf-8"),
                (journal_dir / "daily_brief.md").read_text(encoding="utf-8"),
            )

    def test_run_command_falls_back_to_local_manifest_when_planner_output_is_not_a_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_local_task(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={"OMNIUS_CODEX_BIN": str(fake_codex)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            journal_dir = journals[0].parent
            planner_response = json.loads((journal_dir / "planner_response.json").read_text(encoding="utf-8"))
            manifest = json.loads((journal_dir / "manifest.json").read_text(encoding="utf-8"))
            dispatch_log = json.loads((journal_dir / "dispatch_log.json").read_text(encoding="utf-8"))

            self.assertEqual(planner_response, manifest)
            self.assertEqual(manifest["summary"], "1 task(s) planned from local queue")
            self.assertEqual([task["id"] for task in manifest["tasks"]], ["O00001"])
            self.assertEqual(dispatch_log["pipeline"]["status"], "completed")
            self.assertFalse(dispatch_log["planner"]["used_runner_output"])
            self.assertEqual(dispatch_log["planner"]["fallback_reason"], "invalid_json")

    def test_run_command_persists_real_planner_dayprep_command_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo, planner_dayprep_mode="real")
            self._write_local_task(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={"OMNIUS_CODEX_BIN": str(fake_codex)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            dispatch_log = json.loads(journals[0].read_text(encoding="utf-8"))

            self.assertEqual(dispatch_log["planner"]["returncode"], 0)
            self.assertEqual(dispatch_log["planner"]["command"][0], str(fake_codex))
            self.assertEqual(dispatch_log["planner"]["command"][-1], "<prompt>")
            self.assertNotIn("RUN_DATE", " ".join(dispatch_log["planner"]["command"]))
            self.assertEqual(dispatch_log["dayprep"]["returncode"], 0)
            self.assertEqual(dispatch_log["dayprep"]["command"][0], str(fake_codex))
            self.assertEqual(dispatch_log["dayprep"]["command"][-1], "<prompt>")
            self.assertNotIn("DISPATCH_LOG_JSON", " ".join(dispatch_log["dayprep"]["command"]))

    def test_run_command_surfaces_quarantined_recurring_state_in_prompt_and_dispatch_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_local_task(home)
            self._write_recurring_task(home)
            self._write_corrupt_recurring_state(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={"OMNIUS_CODEX_BIN": str(fake_codex)},
            )

            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            journal_dir = journals[0].parent
            planner_prompt = (journal_dir / "planner_prompt.md").read_text(encoding="utf-8")
            dispatch_log = json.loads((journal_dir / "dispatch_log.json").read_text(encoding="utf-8"))

            self.assertIn("recurring_state.json.suspect.", planner_prompt)
            self.assertIn("recurring_state", dispatch_log["planner"])
            self.assertIn("suspect_path", dispatch_log["planner"]["recurring_state"])
            self.assertIn("recurring_state.json.suspect.", dispatch_log["planner"]["recurring_state"]["suspect_path"])

    def test_run_command_returns_nonzero_when_worker_result_is_non_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_local_task(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={
                    "OMNIUS_CODEX_BIN": str(fake_codex),
                    "OMNIUS_FAKE_CODEX_RESULT": "FAILURE",
                },
            )

            self.assertNotEqual(result.returncode, 0)
            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            dispatch_log = json.loads(journals[0].read_text(encoding="utf-8"))
            self.assertEqual(dispatch_log["pipeline"]["status"], "completed")
            self.assertEqual(dispatch_log["tasks"]["O00001"]["status"], "FAILURE")
            self.assertTrue((home / "tasks" / "O00001_add_sample.md").exists())
            self.assertFalse((home / "tasks" / "completed" / "O00001_add_sample.md").exists())

    def test_run_command_records_worker_usage_and_cost_ledgers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_local_task(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={
                    "OMNIUS_CODEX_BIN": str(fake_codex),
                    "OMNIUS_FAKE_CODEX_USAGE_JSON": '{"cost_usd":0.18,"turns":47,"input_tokens":142014,"output_tokens":4227,"cache_read_tokens":41022}',
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            journal_dir = journals[0].parent
            dispatch_log = json.loads(journals[0].read_text(encoding="utf-8"))

            self.assertEqual(dispatch_log["tasks"]["O00001"]["cost_usd"], 0.18)
            self.assertEqual(dispatch_log["tasks"]["O00001"]["turns"], 47)
            self.assertEqual(
                dispatch_log["tasks"]["O00001"]["tokens"],
                {"input": 142014, "output": 4227, "cache_read": 41022},
            )
            self.assertEqual(dispatch_log["pipeline"]["total_cost_usd"], 0.18)
            self.assertTrue((home / "costs" / f"{journal_dir.parent.name}_{journal_dir.name}_O00001.md").exists())
            self.assertIn(
                f"| {journal_dir.parent.name} |   1   |   1     | $0.18   |",
                (home / "costs" / "omnius_cost.md").read_text(encoding="utf-8"),
            )

    def test_run_command_moves_partial_local_task_to_pending_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_local_task(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={
                    "OMNIUS_CODEX_BIN": str(fake_codex),
                    "OMNIUS_FAKE_CODEX_RESULT": "PARTIAL",
                },
            )

            self.assertNotEqual(result.returncode, 0)
            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            dispatch_log = json.loads(journals[0].read_text(encoding="utf-8"))

            self.assertEqual(dispatch_log["tasks"]["O00001"]["status"], "PARTIAL")
            self.assertFalse((home / "tasks" / "O00001_add_sample.md").exists())
            self.assertTrue((home / "tasks" / "pending_approval" / "O00001_add_sample.md").exists())
            tasks_index = (home / "tasks.md").read_text(encoding="utf-8")
            self.assertNotIn("- O00001: Add sample [file: O00001_add_sample.md]", tasks_index)

    def test_run_command_updates_recurring_state_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_recurring_task(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={"OMNIUS_CODEX_BIN": str(fake_codex)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            run_date = journals[0].parent.parent.name
            recurring_state = json.loads((home / "state" / "recurring_state.json").read_text(encoding="utf-8"))
            self.assertEqual(
                recurring_state["R00001"],
                {
                    "consecutive_failures": 0,
                    "last_attempted": run_date,
                    "last_status": "SUCCESS",
                    "last_succeeded": run_date,
                },
            )

    def test_run_command_trips_circuit_breaker_and_skips_remaining_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo, max_consecutive_failures=1)
            self._write_local_task(home)
            self._write_second_local_task(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={
                    "OMNIUS_CODEX_BIN": str(fake_codex),
                    "OMNIUS_FAKE_CODEX_RESULTS": "FAILURE,SUCCESS",
                    "OMNIUS_FAKE_CODEX_SEQUENCE_FILE": str(tmp_path / "fake-codex-sequence.txt"),
                },
            )

            self.assertNotEqual(result.returncode, 0)
            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            dispatch_log = json.loads(journals[0].read_text(encoding="utf-8"))

            self.assertEqual(dispatch_log["tasks"]["O00001"]["status"], "FAILURE")
            self.assertEqual(dispatch_log["tasks"]["O00002"]["status"], "CIRCUIT_BREAKER_SKIPPED")
            self.assertEqual(dispatch_log["pipeline"]["circuit_breaker"]["state"], "open")
            self.assertEqual(dispatch_log["pipeline"]["circuit_breaker"]["consecutive_failures"], 1)

    def test_run_command_exits_nonzero_without_traceback_when_config_has_no_repos(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            self._write_config(home=home, repo=None)

            result = self._run_cli(home=home)

            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("Config must define at least one repo", result.stderr)

            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            dispatch_log = json.loads(journals[0].read_text(encoding="utf-8"))
            self.assertEqual(dispatch_log["pipeline"]["status"], "failed")
            self.assertIn("ended_at", dispatch_log["pipeline"])

    def test_run_command_finalizes_dispatch_log_when_late_pipeline_step_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_malformed_local_task(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={"OMNIUS_CODEX_BIN": str(fake_codex)},
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("Malformed task entry", result.stderr)

            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            journal_dir = journals[0].parent
            dispatch_log = json.loads(journals[0].read_text(encoding="utf-8"))

            self.assertTrue((journal_dir / "preflight.json").exists())
            self.assertEqual(dispatch_log["pipeline"]["status"], "failed")
            self.assertIn("ended_at", dispatch_log["pipeline"])

    def _run_cli(
        self,
        *,
        home: Path,
        fake_bin: Optional[Path] = None,
        extra_env: Optional[dict[str, str]] = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["OMNIUS_HOME"] = str(home)
        env["PYTHONPATH"] = str(SRC)
        if fake_bin is not None:
            env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
        if extra_env is not None:
            env.update(extra_env)
        return subprocess.run(
            [PYTHON, "-m", "omnius", "run"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            env=env,
        )

    def _run_status_cli(self, *, home: Path) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["OMNIUS_HOME"] = str(home)
        env["PYTHONPATH"] = str(SRC)
        return subprocess.run(
            [PYTHON, "-m", "omnius", "status", "--json"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            env=env,
        )

    def _write_config(
        self,
        *,
        home: Path,
        repo: Optional[Path],
        max_consecutive_failures: int = 3,
        planner_dayprep_mode: str = "placeholder",
    ) -> None:
        home.mkdir(parents=True, exist_ok=True)
        repos_block = ""
        if repo is not None:
            repos_block = textwrap.dedent(
                f"""

                [[repos]]
                slug = "example"
                path = "{repo}"
                branch = "main"
                role = "author"
                labels = ["omnius"]
                """
            )
        (home / "omnius.toml").write_text(
            textwrap.dedent(
                f"""
                [global]
                timezone = "America/Los_Angeles"
                pipeline_cron = "0 21 * * 0-4"
                pipeline_budget_minutes = 540
                default_task_budget_minutes = 120
                max_consecutive_failures = {max_consecutive_failures}
                notification_backend = "none"

                [runner]
                default = "codex"
                planner_dayprep_mode = "{planner_dayprep_mode}"

                [runners.codex]
                enabled = true

                [capabilities]
                brainstorm = "auto"
                review_diff = "auto"
                autonomous_testing = "auto"
                second_opinion = "auto"
                """
            ).strip()
            + repos_block
            + "\n",
            encoding="utf-8",
        )

    def _write_second_local_task(self, home: Path) -> None:
        (home / "tasks.md").write_text(
            "## Format\n"
            "- <ID>: <Title> [file: <filename>.md]\n\n"
            "## Active\n"
            "- O00001: Add sample [file: O00001_add_sample.md]\n"
            "- O00002: Follow up [file: O00002_follow_up.md]\n\n"
            "## Completed\n",
            encoding="utf-8",
        )
        self._write_task_file(home / "tasks" / "O00002_follow_up.md", title="Follow up")

    def _write_malformed_local_task(self, home: Path) -> None:
        tasks_dir = home / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        (home / "tasks.md").write_text(
            "## Format\n"
            "- <ID>: <Title> [file: <filename>.md]\n\n"
            "## Active\n"
            "- malformed entry\n\n"
            "## Completed\n",
            encoding="utf-8",
        )

    def _write_recurring_task(self, home: Path) -> None:
        (home / "tasks" / "recurring").mkdir(parents=True, exist_ok=True)
        (home / "tasks" / "recurring" / "R00001_daily_cleanup.md").write_text(
            textwrap.dedent(
                """
                ---
                title: Daily cleanup
                repo: example
                schedule: daily
                type: research
                complexity: medium
                max_time_minutes: 45
                ---
                Recurring body
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

    def _write_pending_approval_task(self, home: Path) -> None:
        (home / "tasks" / "pending_approval").mkdir(parents=True, exist_ok=True)
        (home / "tasks" / "pending_approval" / "proposed_follow_up.md").write_text(
            "Needs review\n",
            encoding="utf-8",
        )

    def _write_corrupt_recurring_state(self, home: Path) -> None:
        (home / "state").mkdir(parents=True, exist_ok=True)
        (home / "state" / "recurring_state.json").write_text("{not-json", encoding="utf-8")

    def _write_fake_run_binaries(self, tmp_path: Path) -> tuple[Path, Path]:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        self._write_executable(
            fake_bin / "gh",
            textwrap.dedent(
                """\
                #!/bin/sh
                if [ "$1" = "--version" ]; then
                    echo "gh version 2.61.0"
                    exit 0
                fi
                if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
                    echo "github.com"
                    exit 0
                fi
                echo "unexpected gh args: $@" >&2
                exit 1
                """
            ),
        )
        fake_codex = fake_bin / "omnius-fake-codex"
        self._write_executable(
            fake_codex,
            textwrap.dedent(
                """\
                #!/bin/sh
                set -eu
                if [ "$1" = "--version" ]; then
                    echo "codex 1.2.3"
                    exit 0
                fi
                [ "$1" = "exec" ] || {
                    echo "expected exec mode" >&2
                    exit 10
                }
                shift
                worktree=""
                sandbox=""
                saw_output_schema="0"
                prompt=""
                while [ "$#" -gt 0 ]; do
                    case "$1" in
                        --cd)
                            shift
                            worktree="$1"
                            ;;
                        --sandbox)
                            shift
                            sandbox="$1"
                            [ "$sandbox" = "workspace-write" ] || [ "$sandbox" = "read-only" ] || {
                                echo "unexpected sandbox: $1" >&2
                                exit 11
                            }
                            ;;
                        --ask-for-approval)
                            shift
                            [ "$1" = "never" ] || {
                                echo "unexpected approval mode: $1" >&2
                                exit 12
                            }
                            ;;
                        --output-schema)
                            shift
                            [ -f "$1" ] || {
                                echo "missing schema file: $1" >&2
                                exit 13
                            }
                            saw_output_schema="1"
                            ;;
                        *)
                            prompt="$1"
                            ;;
                    esac
                    shift
                done
                if [ "$sandbox" = "read-only" ]; then
                    [ -n "$prompt" ] || {
                        echo "missing prompt payload" >&2
                        exit 17
                    }
                    printf '{"text":"Fake real runner output"}\n'
                    exit 0
                fi
                [ "$saw_output_schema" = "1" ] || {
                    echo "missing --output-schema" >&2
                    exit 14
                }
                [ -n "$worktree" ] || {
                    echo "missing --cd path" >&2
                    exit 15
                }
                actual_pwd="$(pwd -P)"
                expected_pwd="$(cd "$worktree" && pwd -P)"
                [ "$actual_pwd" = "$expected_pwd" ] || {
                    echo "unexpected cwd: $actual_pwd != $expected_pwd" >&2
                    exit 16
                }
                [ -n "$prompt" ] || {
                    echo "missing prompt payload" >&2
                    exit 17
                }
                result="${OMNIUS_FAKE_CODEX_RESULT:-SUCCESS}"
                if [ "${OMNIUS_FAKE_CODEX_RESULTS:-}" != "" ]; then
                    sequence_file="${OMNIUS_FAKE_CODEX_SEQUENCE_FILE:-${TMPDIR:-/tmp}/omnius_fake_codex_sequence}"
                    index="0"
                    if [ -f "$sequence_file" ]; then
                        index="$(cat "$sequence_file")"
                    fi
                    next_result="$(printf '%s' "$OMNIUS_FAKE_CODEX_RESULTS" | cut -d, -f $((index + 1)))"
                    result="$next_result"
                    printf '%s' $((index + 1)) > "$sequence_file"
                fi
                usage_suffix=""
                if [ "${OMNIUS_FAKE_CODEX_USAGE_JSON:-}" != "" ]; then
                    usage_suffix=',"usage":'"${OMNIUS_FAKE_CODEX_USAGE_JSON}"
                fi
                case "$result" in
                    SUCCESS)
                        printf '%s\n' "artifact for ${OMNIUS_TASK_ID:-unknown}" > omnius_artifact.txt
                        git add omnius_artifact.txt
                        git commit -m "omnius artifact ${OMNIUS_TASK_ID:-unknown}" >/dev/null
                        printf '{"status":"SUCCESS","branch":"%s","summary":"done"%s}\n' "$OMNIUS_BRANCH" "$usage_suffix"
                        ;;
                    PARTIAL)
                        printf '{"status":"PARTIAL","branch":"%s","notes":"needs follow-up"%s}\n' "$OMNIUS_BRANCH" "$usage_suffix"
                        ;;
                    FAILURE)
                        printf '{"status":"FAILURE","error":"worker failed"%s}\n' "$usage_suffix"
                        ;;
                    *)
                        echo "unsupported fake result: $result" >&2
                        exit 18
                        ;;
                esac
                """
            ),
        )
        return fake_bin, fake_codex

    def _create_repo_with_origin(self, tmp_path: Path) -> Path:
        origin = tmp_path / "origin.git"
        repo = tmp_path / "repo"
        subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True, text=True)
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "omnius@example.com"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Omnius Test"],
            check=True,
            capture_output=True,
            text=True,
        )
        (repo / ".gitignore").write_text(".omnius/\n", encoding="utf-8")
        (repo / "README.md").write_text("example\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "initial"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "origin", str(origin)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "push", "-u", "origin", "main"],
            check=True,
            capture_output=True,
            text=True,
        )
        return repo

    def _write_executable(self, path: Path, content: str) -> None:
        path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def _write_task_file(self, path: Path, *, title: str, agent: Optional[str] = None) -> None:
        frontmatter = ["---", f"title: {title}", "repo: example"]
        if agent is not None:
            frontmatter.append(f"agent: {agent}")
        frontmatter.extend(["---", "Task body", ""])
        path.write_text("\n".join(frontmatter), encoding="utf-8")

    def _write_local_task(self, home: Path, agent: Optional[str] = None) -> None:
        tasks_dir = home / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        (home / "tasks.md").write_text(
            "## Format\n"
            "- <ID>: <Title> [file: <filename>.md]\n\n"
            "## Active\n"
            "- O00001: Add sample [file: O00001_add_sample.md]\n\n"
            "## Completed\n",
            encoding="utf-8",
        )
        self._write_task_file(tasks_dir / "O00001_add_sample.md", title="Add sample", agent=agent)

    def _write_fake_claude_binary(self, tmp_path: Path) -> Path:
        fake_claude = tmp_path / "bin" / "omnius-fake-claude"
        return self._write_executable(
            fake_claude,
            textwrap.dedent(
                """\
                #!/bin/sh
                set -eu
                if [ "$1" = "--version" ]; then
                    echo "claude 4.0.0"
                    exit 0
                fi
                if [ "${OMNIUS_FAKE_RUNNER_LOG:-}" != "" ]; then
                    printf 'claude\n' >> "$OMNIUS_FAKE_RUNNER_LOG"
                fi
                printf '%s\n' "artifact for ${OMNIUS_TASK_ID:-unknown}" > omnius_artifact.txt
                git add omnius_artifact.txt
                git commit -m "omnius artifact ${OMNIUS_TASK_ID:-unknown}" >/dev/null
                printf '{"status":"SUCCESS","branch":"%s","summary":"done"}\n' "$OMNIUS_BRANCH"
                """
            ),
        )
