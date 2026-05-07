import json
import tempfile
import unittest
from pathlib import Path

from omnius.dayprep import run_dayprep
from omnius.dispatcher import initialize_dispatch_log
from omnius.runners.base import DayPrepInvocation, PlannerInvocation, RunnerAdapter, RunnerCapability, RunnerHealth, UsageStats, WorkerRequest


class SuccessfulDayPrepRunner(RunnerAdapter):
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
        raise AssertionError("worker path should not be used in dayprep tests")

    def invoke_dayprep(self, *, task_id: str, prompt: str) -> DayPrepInvocation:
        return DayPrepInvocation(
            runner_name=self.name,
            task_id=task_id,
            brief_markdown="# Omnius — 2026-05-06\n\nCompiled brief\n",
            usage=UsageStats(cost_usd=0.02, turns=5),
        )


class FailingDayPrepRunner(SuccessfulDayPrepRunner):
    def invoke_dayprep(self, *, task_id: str, prompt: str) -> DayPrepInvocation:
        raise RuntimeError("compiler unavailable")


class DayPrepTests(unittest.TestCase):
    def test_run_dayprep_writes_compiler_brief_and_latest_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            journal_dir, dispatch_log_path = self._seed_run(home)

            result = run_dayprep(
                runner=SuccessfulDayPrepRunner(),
                workspace_home=home,
                journal_dir=journal_dir,
                dispatch_log_path=dispatch_log_path,
            )

            brief_path = journal_dir / "daily_brief.md"
            self.assertTrue(brief_path.exists())
            self.assertEqual((home / "daily_brief.md").read_text(encoding="utf-8"), brief_path.read_text(encoding="utf-8"))
            self.assertTrue((journal_dir / "dayprep_prompt.md").exists())
            self.assertFalse(result.used_fallback)
            self.assertIsNone(result.warning_banner)
            self.assertTrue((home / "costs" / "2026-05-06_2100_dayprep.md").exists())

    def test_run_dayprep_falls_back_to_python_summary_when_runner_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            journal_dir, dispatch_log_path = self._seed_run(home)

            result = run_dayprep(
                runner=FailingDayPrepRunner(),
                workspace_home=home,
                journal_dir=journal_dir,
                dispatch_log_path=dispatch_log_path,
            )

            fallback_path = journal_dir / "daily_brief_fallback.md"
            self.assertTrue(fallback_path.exists())
            self.assertIn("Day prep failed; minimal brief only.", fallback_path.read_text(encoding="utf-8"))
            self.assertEqual((home / "daily_brief.md").read_text(encoding="utf-8"), fallback_path.read_text(encoding="utf-8"))
            self.assertTrue(result.used_fallback)
            self.assertEqual(result.warning_banner, "Day prep failed; minimal brief only.")

    def _seed_run(self, home: Path) -> tuple[Path, Path]:
        journal_dir = home / "journal" / "2026-05-06" / "2100"
        journal_dir.mkdir(parents=True, exist_ok=True)
        dispatch_log_path = journal_dir / "dispatch_log.json"
        initialize_dispatch_log(
            dispatch_log_path,
            pipeline_id="pipeline-20260506-210000",
            runner_name="fake",
            repo_slug="example",
            branch="main",
        )
        dispatch_log = json.loads(dispatch_log_path.read_text(encoding="utf-8"))
        dispatch_log["pipeline"].update(
            {
                "status": "completed",
                "run_date": "2026-05-06",
                "journal_dir": str(journal_dir),
                "started_at": "2026-05-06T21:00:00-07:00",
                "ended_at": "2026-05-06T21:05:00-07:00",
                "total_cost_usd": 0.18,
            }
        )
        dispatch_log["tasks"]["O00001"] = {
            "id": "O00001",
            "title": "Add sample",
            "status": "SUCCESS",
            "branch": "omnius/2026-05-06/O00001",
            "summary": "done",
            "cost_usd": 0.18,
        }
        dispatch_log_path.write_text(json.dumps(dispatch_log, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (journal_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "run_date": "2026-05-06",
                    "journal_dir": str(journal_dir),
                    "summary": "1 task(s) planned from local queue",
                    "tasks": [],
                    "skipped": [],
                    "notes": "stub",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return journal_dir, dispatch_log_path
