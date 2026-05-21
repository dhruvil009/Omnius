import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from omnius.config import CapabilityConfig, GlobalConfig, OmniusConfig, RepoConfig, RunnerSelection
from omnius.dispatcher import dispatch_manifest, initialize_dispatch_log
from omnius.runners.base import DayPrepInvocation, PlannerInvocation, RunnerAdapter, RunnerCapability, RunnerHealth, WorkerRequest


class FakeRunner(RunnerAdapter):
    def __init__(self, script_path: Path, *, name: str = "fake") -> None:
        self._script_path = script_path
        self._name = name
        self.requests: list[WorkerRequest] = []

    @property
    def name(self) -> str:
        return self._name

    def health_check(self) -> RunnerHealth:
        return RunnerHealth(ok=True, summary="fake")

    def discover_capabilities(self) -> dict[str, RunnerCapability]:
        return {}

    def invoke_planner(self, *, task_id: str, prompt: str) -> PlannerInvocation:
        return PlannerInvocation(runner_name=self.name, task_id=task_id, prompt=prompt, plan_text="stub")

    def build_worker_command(self, request: WorkerRequest) -> list[str]:
        self.requests.append(request)
        return [str(self._script_path), request.prompt]

    def invoke_dayprep(self, *, task_id: str, prompt: str) -> DayPrepInvocation:
        return DayPrepInvocation(runner_name=self.name, task_id=task_id, brief_markdown="stub brief")


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

            script_path = self._write_worker_script(
                tmp_path / "success.sh",
                self._commit_success_body(),
            )
            runner = FakeRunner(script_path)
            result = dispatch_manifest(
                manifest=self._manifest(tasks=[self._local_manifest_task()]),
                runner=runner,
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
            self.assertEqual(result["pipeline"]["circuit_breaker"]["state"], "closed")
            self.assertEqual(result["pipeline"]["circuit_breaker"]["consecutive_failures"], 0)

            branch = "omnius/2026-05-05/O00001"
            branch_list = subprocess.run(
                ["git", "-C", str(repo_path), "branch", "--list", branch],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn(branch, branch_list.stdout)

    def test_dispatch_manifest_downgrades_success_without_artifact_and_keeps_task_active(self) -> None:
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

            script_path = self._write_worker_script(
                tmp_path / "success-no-artifact.sh",
                'printf \'{"status":"SUCCESS","branch":"%s","summary":"done"}\\n\' "$OMNIUS_BRANCH"\n',
            )
            result = dispatch_manifest(
                manifest=self._manifest(tasks=[self._local_manifest_task()]),
                runner=FakeRunner(script_path),
                config=self._config(repo_path),
                workspace_home=home,
                journal_dir=journal_dir,
                dispatch_log_path=dispatch_log_path,
            )

            task_state = result["tasks"]["O00001"]
            self.assertEqual(task_state["status"], "NO_ARTIFACT")
            self.assertEqual(task_state["summary"], "done")
            self.assertIn("durable artifact", task_state["reason"])
            self.assertTrue((home / "tasks" / "O00001_add_sample.md").exists())
            self.assertFalse((home / "tasks" / "completed" / "O00001_add_sample.md").exists())

    def test_dispatch_manifest_records_worker_usage_and_pipeline_total_cost(self) -> None:
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

            script_path = self._write_worker_script(
                tmp_path / "usage-success.sh",
                textwrap.dedent(
                    """\
                    printf '%s\n' 'usage artifact' > omnius_artifact.txt
                    git add omnius_artifact.txt
                    git commit -m "omnius artifact $OMNIUS_TASK_ID" >/dev/null
                    printf '{"status":"SUCCESS","branch":"%s","summary":"done","usage":{"cost_usd":0.18,"turns":47,"input_tokens":142014,"output_tokens":4227,"cache_read_tokens":41022}}\\n' "$OMNIUS_BRANCH"
                    """
                ),
            )
            result = dispatch_manifest(
                manifest=self._manifest(tasks=[self._local_manifest_task()]),
                runner=FakeRunner(script_path),
                config=self._config(repo_path),
                workspace_home=home,
                journal_dir=journal_dir,
                dispatch_log_path=dispatch_log_path,
            )

            task_state = result["tasks"]["O00001"]
            self.assertEqual(task_state["cost_usd"], 0.18)
            self.assertEqual(task_state["turns"], 47)
            self.assertEqual(
                task_state["tokens"],
                {"input": 142014, "output": 4227, "cache_read": 41022},
            )
            self.assertEqual(result["pipeline"]["total_cost_usd"], 0.18)
            self.assertTrue((home / "costs" / "2026-05-05_2100_O00001.md").exists())
            self.assertTrue((home / "costs" / "omnius_cost.md").exists())

    def test_dispatch_manifest_moves_partial_local_task_to_pending_approval(self) -> None:
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

            script_path = self._write_worker_script(
                tmp_path / "partial.sh",
                'printf \'{"status":"PARTIAL","notes":"needs follow-up"}\\n\'\n',
            )
            result = dispatch_manifest(
                manifest=self._manifest(tasks=[self._local_manifest_task()]),
                runner=FakeRunner(script_path),
                config=self._config(repo_path),
                workspace_home=home,
                journal_dir=journal_dir,
                dispatch_log_path=dispatch_log_path,
            )

            partial_state = result["tasks"]["O00001"]
            self.assertEqual(partial_state["status"], "PARTIAL")
            self.assertEqual(partial_state["notes"], "needs follow-up")
            self.assertFalse((home / "tasks" / "O00001_add_sample.md").exists())
            self.assertTrue((home / "tasks" / "pending_approval" / "O00001_add_sample.md").exists())
            tasks_index = (home / "tasks.md").read_text(encoding="utf-8")
            self.assertNotIn("- O00001: Add sample [file: O00001_add_sample.md]", tasks_index)
            self.assertNotIn("2026-05-05", tasks_index)

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
                manifest=self._manifest(tasks=[self._local_manifest_task()]),
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
            result = dispatch_manifest(
                manifest=self._manifest(tasks=[self._local_manifest_task(max_time_minutes=0)]),
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

    def test_dispatch_manifest_updates_recurring_state_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = self._create_repo_with_origin(tmp_path)
            home = self._create_workspace_home(tmp_path)
            self._write_recurring_task(home)
            (home / "state" / "recurring_state.json").write_text(
                json.dumps(
                    {
                        "R00001": {
                            "last_attempted": "2026-05-04",
                            "last_status": "FAILURE",
                            "consecutive_failures": 2,
                            "quarantined_until": "2026-05-11",
                        }
                    }
                ),
                encoding="utf-8",
            )

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

            script_path = self._write_worker_script(
                tmp_path / "recurring-success.sh",
                self._commit_success_body(),
            )
            dispatch_manifest(
                manifest=self._manifest(tasks=[self._recurring_manifest_task()]),
                runner=FakeRunner(script_path),
                config=self._config(repo_path),
                workspace_home=home,
                journal_dir=journal_dir,
                dispatch_log_path=dispatch_log_path,
            )

            recurring_state = json.loads((home / "state" / "recurring_state.json").read_text(encoding="utf-8"))
            self.assertEqual(
                recurring_state["R00001"],
                {
                    "consecutive_failures": 0,
                    "last_attempted": "2026-05-05",
                    "last_status": "SUCCESS",
                    "last_succeeded": "2026-05-05",
                },
            )

    def test_dispatch_manifest_updates_recurring_state_and_quarantines_after_threshold_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = self._create_repo_with_origin(tmp_path)
            home = self._create_workspace_home(tmp_path)
            self._write_recurring_task(home)
            (home / "state" / "recurring_state.json").write_text(
                json.dumps({"R00001": {"consecutive_failures": 1, "last_attempted": "2026-05-04"}}),
                encoding="utf-8",
            )

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

            script_path = self._write_worker_script(
                tmp_path / "recurring-failure.sh",
                'printf \'{"status":"FAILURE","error":"worker failed"}\\n\'\n',
            )
            dispatch_manifest(
                manifest=self._manifest(tasks=[self._recurring_manifest_task()]),
                runner=FakeRunner(script_path),
                config=self._config(repo_path, max_consecutive_failures=2),
                workspace_home=home,
                journal_dir=journal_dir,
                dispatch_log_path=dispatch_log_path,
            )

            recurring_state = json.loads((home / "state" / "recurring_state.json").read_text(encoding="utf-8"))
            self.assertEqual(
                recurring_state["R00001"],
                {
                    "consecutive_failures": 2,
                    "last_attempted": "2026-05-05",
                    "last_status": "FAILURE",
                    "quarantined_until": "2026-05-12",
                },
            )

    def test_dispatch_manifest_trips_circuit_breaker_and_skips_remaining_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = self._create_repo_with_origin(tmp_path)
            home = self._create_workspace_home(tmp_path)
            self._write_local_task(home, task_id="O00001", filename="O00001_first.md", title="First task")
            self._write_local_task(home, task_id="O00002", filename="O00002_second.md", title="Second task", append=True)
            self._write_local_task(home, task_id="O00003", filename="O00003_third.md", title="Third task", append=True)

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

            script_path = self._write_worker_script(
                tmp_path / "failure.sh",
                'printf \'{"status":"FAILURE","error":"worker failed"}\\n\'\n',
            )
            result = dispatch_manifest(
                manifest=self._manifest(
                    tasks=[
                        self._local_manifest_task(task_id="O00001", filename="O00001_first.md", title="First task"),
                        self._local_manifest_task(task_id="O00002", filename="O00002_second.md", title="Second task"),
                        self._local_manifest_task(task_id="O00003", filename="O00003_third.md", title="Third task"),
                    ]
                ),
                runner=FakeRunner(script_path),
                config=self._config(repo_path, max_consecutive_failures=2),
                workspace_home=home,
                journal_dir=journal_dir,
                dispatch_log_path=dispatch_log_path,
            )

            self.assertEqual(result["tasks"]["O00001"]["status"], "FAILURE")
            self.assertEqual(result["tasks"]["O00002"]["status"], "FAILURE")
            self.assertEqual(result["tasks"]["O00003"]["status"], "CIRCUIT_BREAKER_SKIPPED")
            self.assertEqual(result["pipeline"]["circuit_breaker"]["state"], "open")
            self.assertEqual(result["pipeline"]["circuit_breaker"]["consecutive_failures"], 2)

    def test_dispatch_manifest_uses_smaller_of_task_budget_and_remaining_pipeline_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = self._create_repo_with_origin(tmp_path)
            home = self._create_workspace_home(tmp_path)
            self._write_local_task(home, task_id="O00001", filename="O00001_first.md", title="First task")
            self._write_local_task(home, task_id="O00002", filename="O00002_second.md", title="Second task", append=True)

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

            script_path = self._write_worker_script(
                tmp_path / "success.sh",
                self._commit_success_body(),
            )
            runner = FakeRunner(script_path)
            with patch("omnius.dispatcher.time.monotonic", side_effect=[0.0, 240.0, 240.0, 300.0]):
                dispatch_manifest(
                    manifest=self._manifest(
                        tasks=[
                            self._local_manifest_task(task_id="O00001", filename="O00001_first.md", title="First task"),
                            self._local_manifest_task(task_id="O00002", filename="O00002_second.md", title="Second task"),
                        ]
                    ),
                    runner=runner,
                    config=self._config(repo_path, pipeline_budget_minutes=5),
                    workspace_home=home,
                    journal_dir=journal_dir,
                    dispatch_log_path=dispatch_log_path,
                )

            self.assertEqual(len(runner.requests), 2)
            self.assertEqual(runner.requests[0].max_time_minutes, 5)
            self.assertEqual(runner.requests[1].max_time_minutes, 1)

    def test_dispatch_manifest_honors_task_agent_override_and_records_effective_agent(self) -> None:
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
                runner_name="codex",
                repo_slug="example",
                branch="main",
            )

            script_path = self._write_worker_script(
                tmp_path / "success.sh",
                self._commit_success_body(),
            )
            default_runner = FakeRunner(script_path, name="codex")
            override_runner = FakeRunner(script_path, name="claude")

            result = dispatch_manifest(
                manifest=self._manifest(tasks=[self._local_manifest_task(agent="claude")]),
                runner=default_runner,
                config=self._config(repo_path),
                workspace_home=home,
                journal_dir=journal_dir,
                dispatch_log_path=dispatch_log_path,
                runner_resolver=lambda name: override_runner if name == "claude" else default_runner,
            )

            self.assertEqual(len(default_runner.requests), 0)
            self.assertEqual(len(override_runner.requests), 1)
            self.assertEqual(result["tasks"]["O00001"]["agent"], "claude")
            self.assertEqual(result["tasks"]["O00001"]["status"], "SUCCESS")

    def test_dispatch_manifest_marks_remaining_tasks_budget_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = self._create_repo_with_origin(tmp_path)
            home = self._create_workspace_home(tmp_path)
            self._write_local_task(home, task_id="O00001", filename="O00001_first.md", title="First task")
            self._write_local_task(home, task_id="O00002", filename="O00002_second.md", title="Second task", append=True)

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

            script_path = self._write_worker_script(
                tmp_path / "success.sh",
                self._commit_success_body(),
            )
            with patch("omnius.dispatcher.time.monotonic", side_effect=[0.0, 120.0]):
                result = dispatch_manifest(
                    manifest=self._manifest(
                        tasks=[
                            self._local_manifest_task(task_id="O00001", filename="O00001_first.md", title="First task"),
                            self._local_manifest_task(task_id="O00002", filename="O00002_second.md", title="Second task"),
                        ]
                    ),
                    runner=FakeRunner(script_path),
                    config=self._config(repo_path, pipeline_budget_minutes=1),
                    workspace_home=home,
                    journal_dir=journal_dir,
                    dispatch_log_path=dispatch_log_path,
                )

            self.assertEqual(result["tasks"]["O00001"]["status"], "SUCCESS")
            self.assertEqual(result["tasks"]["O00002"]["status"], "BUDGET_EXHAUSTED")

    def _config(
        self,
        repo_path: Path,
        *,
        pipeline_budget_minutes: int = 540,
        max_consecutive_failures: int = 3,
    ) -> OmniusConfig:
        return OmniusConfig(
            global_config=GlobalConfig(
                timezone="America/Los_Angeles",
                pipeline_cron="0 21 * * 0-4",
                pipeline_budget_minutes=pipeline_budget_minutes,
                default_task_budget_minutes=120,
                max_consecutive_failures=max_consecutive_failures,
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

    def _manifest(self, *, tasks: list[dict[str, object]], run_date: str = "2026-05-05") -> dict[str, object]:
        return {
            "run_date": run_date,
            "journal_dir": "/tmp/journal",
            "summary": f"{len(tasks)} task(s)",
            "tasks": tasks,
            "skipped": [],
            "notes": "stub",
        }

    def _local_manifest_task(
        self,
        *,
        task_id: str = "O00001",
        filename: str = "O00001_add_sample.md",
        title: str = "Add sample",
        max_time_minutes: int = 120,
        agent: str | None = None,
    ) -> dict[str, object]:
        payload = {
            "id": task_id,
            "title": title,
            "type": "implementation",
            "repo_slug": "example",
            "source_ref": f"tasks/{filename}",
            "filename": filename,
            "max_time_minutes": max_time_minutes,
            "complexity": "small",
        }
        if agent is not None:
            payload["agent"] = agent
        return payload

    def _recurring_manifest_task(self) -> dict[str, object]:
        return {
            "id": "R00001",
            "title": "Daily cleanup",
            "type": "maintenance",
            "repo_slug": "example",
            "source_ref": "tasks/recurring/R00001_daily_cleanup.md",
            "filename": "R00001_daily_cleanup.md",
            "max_time_minutes": 45,
            "complexity": "medium",
        }

    def _create_workspace_home(self, tmp_path: Path) -> Path:
        home = tmp_path / ".omnius"
        (home / "tasks" / "completed").mkdir(parents=True, exist_ok=True)
        (home / "tasks" / "pending_approval").mkdir(parents=True, exist_ok=True)
        (home / "state").mkdir(parents=True, exist_ok=True)
        (home / "state" / "recurring_state.json").write_text("{}\n", encoding="utf-8")
        return home

    def _write_local_task(
        self,
        home: Path,
        *,
        task_id: str = "O00001",
        filename: str = "O00001_add_sample.md",
        title: str = "Add sample",
        append: bool = False,
    ) -> None:
        tasks_md_path = home / "tasks.md"
        active_line = f"- {task_id}: {title} [file: {filename}]\n"
        if append and tasks_md_path.exists():
            current = tasks_md_path.read_text(encoding="utf-8")
            updated = current.replace("## Completed\n", f"{active_line}\n## Completed\n")
            tasks_md_path.write_text(updated, encoding="utf-8")
        else:
            tasks_md_path.write_text(
                "## Format\n"
                "- <ID>: <Title> [file: <filename>.md]\n\n"
                "## Active\n"
                f"{active_line}\n"
                "## Completed\n",
                encoding="utf-8",
            )
        (home / "tasks" / filename).write_text(
            "---\n"
            f"title: {title}\n"
            "repo: example\n"
            "---\n"
            "Task body\n",
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
                type: maintenance
                complexity: medium
                max_time_minutes: 45
                ---
                Recurring body
                """
            ).strip()
            + "\n",
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

    def _commit_success_body(self) -> str:
        return (
            "printf '%s\\n' 'artifact' > omnius_artifact.txt\n"
            "git add omnius_artifact.txt\n"
            'git commit -m "omnius artifact $OMNIUS_TASK_ID" >/dev/null\n'
            'printf \'{"status":"SUCCESS","branch":"%s","summary":"done"}\\n\' "$OMNIUS_BRANCH"\n'
        )
