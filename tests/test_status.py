import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from omnius.dispatcher import initialize_dispatch_log, update_dispatch_log
from omnius.status import build_status_payload, load_status_snapshot, render_status_table
from omnius.workspace import bootstrap_workspace


class StatusTests(unittest.TestCase):
    def test_load_latest_run_status_prefers_latest_journal_with_dispatch_log(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            older = self._seed_run(home=home, run_date="2026-05-05", run_time="2100", pipeline_status="completed")
            newer = self._seed_run(home=home, run_date="2026-05-06", run_time="0600", pipeline_status="completed")

            snapshot = load_status_snapshot(home)

            self.assertEqual(snapshot.journal_dir, newer)
            self.assertEqual(snapshot.run_date, "2026-05-06")
            self.assertEqual(snapshot.pipeline_status, "completed")
            self.assertNotEqual(snapshot.journal_dir, older)

    def test_build_status_payload_reports_attention_and_skipped_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)
            (home / "tasks" / "pending_approval" / "proposed_follow_up.md").write_text("Needs review\n", encoding="utf-8")
            journal_dir = self._seed_run(home=home, run_date="2026-05-06", run_time="2100", pipeline_status="completed")
            (journal_dir / "preflight.json").write_text(
                json.dumps({"ok": True, "abort_reason": None, "runner_name": "codex"}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (journal_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "run_date": "2026-05-06",
                        "journal_dir": str(journal_dir),
                        "summary": "2 task(s) planned from local queue",
                        "tasks": [],
                        "skipped": ["github issues"],
                        "notes": "Planner summary",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            update_dispatch_log(
                journal_dir / "dispatch_log.json",
                patch={
                    "dayprep": {
                        "brief_path": str(journal_dir / "daily_brief.md"),
                        "latest_brief_path": str(home / "daily_brief.md"),
                        "used_fallback": False,
                        "warning_banner": None,
                    },
                    "tasks": {
                        "O00001": {
                            "id": "O00001",
                            "title": "Ship feature",
                            "repo_slug": "example",
                            "status": "SUCCESS",
                            "agent": "codex",
                            "branch": "omnius/2026-05-06/O00001",
                            "summary": "done",
                            "duration_seconds": 12.3,
                        },
                        "O00039": {
                            "id": "O00039",
                            "title": "Investigate flaky test",
                            "repo_slug": "example",
                            "status": "PARTIAL",
                            "agent": "claude",
                            "branch": "omnius/2026-05-06/O00039",
                            "notes": "needs follow-up",
                            "duration_seconds": 25.1,
                        },
                        "O00040": {
                            "id": "O00040",
                            "title": "Skipped by breaker",
                            "repo_slug": "example",
                            "status": "CIRCUIT_BREAKER_SKIPPED",
                        },
                    },
                },
            )

            payload = build_status_payload(journal_dir)

            self.assertEqual(payload["attention"][0]["id"], "O00039")
            self.assertEqual(payload["attention"][0]["agent"], "claude")
            self.assertEqual(payload["skipped"]["pending_approval"], 1)
            self.assertEqual(payload["skipped"]["manifest"], 1)
            self.assertEqual(payload["skipped"]["circuit_breaker_skipped"], 1)
            self.assertEqual(payload["summary"], "2 task(s) planned from local queue")
            self.assertEqual(payload["notes"], "Planner summary")
            self.assertEqual(payload["preflight"]["ok"], True)

    def test_render_status_table_includes_summary_and_attention(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)
            (home / "tasks" / "pending_approval" / "proposed_follow_up.md").write_text("Needs review\n", encoding="utf-8")
            journal_dir = self._seed_run(home=home, run_date="2026-05-06", run_time="2100", pipeline_status="completed")
            (journal_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "run_date": "2026-05-06",
                        "journal_dir": str(journal_dir),
                        "summary": "1 task(s) planned from local queue",
                        "tasks": [],
                        "skipped": [],
                        "notes": "Planner summary",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            update_dispatch_log(
                journal_dir / "dispatch_log.json",
                patch={
                    "tasks": {
                        "O00039": {
                            "id": "O00039",
                            "title": "Investigate flaky test",
                            "repo_slug": "example",
                            "status": "PARTIAL",
                            "agent": "claude",
                            "branch": "omnius/2026-05-06/O00039",
                            "notes": "needs follow-up",
                            "duration_seconds": 25.1,
                        }
                    }
                },
            )

            rendered = render_status_table(build_status_payload(journal_dir))

            self.assertIn("Pipeline: completed", rendered)
            self.assertIn("Summary: 1 task(s) planned from local queue", rendered)
            self.assertIn("O00039 Investigate flaky test [PARTIAL via claude]", rendered)
            self.assertIn("pending_approval=1", rendered)

    def _seed_run(self, *, home: Path, run_date: str, run_time: str, pipeline_status: str) -> Path:
        journal_dir = home / "journal" / run_date / run_time
        journal_dir.mkdir(parents=True, exist_ok=True)
        dispatch_log_path = journal_dir / "dispatch_log.json"
        initialize_dispatch_log(
            dispatch_log_path,
            pipeline_id=f"pipeline-{run_date}-{run_time}",
            runner_name="codex",
            repo_slug="example",
            branch="main",
        )
        update_dispatch_log(
            dispatch_log_path,
            patch={
                "pipeline": {
                    "status": pipeline_status,
                    "run_date": run_date,
                    "journal_dir": str(journal_dir),
                    "started_at": f"{run_date}T{run_time[:2]}:{run_time[2:]}:00-07:00",
                    "ended_at": f"{run_date}T{run_time[:2]}:{run_time[2:]}:59-07:00",
                }
            },
        )
        return journal_dir
