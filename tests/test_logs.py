import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from omnius.logs import (
    collect_cron_logs,
    collect_error_summary,
    collect_worker_logs,
    load_latest_dispatch_log,
    summarize_logs,
)
from omnius.workspace import bootstrap_workspace


class LogsTests(unittest.TestCase):
    def test_summarize_logs_handles_no_runs_and_missing_scheduler_logs(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)

            summary = summarize_logs(home)
            cron = collect_cron_logs(home)

        self.assertTrue(summary["ok"])
        self.assertIsNone(summary["latest_journal"])
        self.assertIn("logs dispatch", summary["hint"])
        self.assertFalse(cron["logs"]["cron"]["exists"])
        self.assertFalse(cron["logs"]["launchd_stdout"]["exists"])
        self.assertFalse(cron["logs"]["launchd_stderr"]["exists"])

    def test_load_latest_dispatch_log_returns_error_envelope_for_malformed_log(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            journal_dir = home / "journal" / "2026-05-07" / "210000"
            journal_dir.mkdir(parents=True)
            (journal_dir / "dispatch_log.json").write_text("{not valid json", encoding="utf-8")

            result = load_latest_dispatch_log(home)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "malformed_dispatch_log")
        self.assertEqual(result["journal_dir"], str(journal_dir))
        self.assertEqual(result["path"], str(journal_dir / "dispatch_log.json"))

    def test_load_latest_dispatch_log_returns_error_envelope_for_invalid_bytes(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            older_dir = home / "journal" / "2026-05-07" / "210000"
            latest_dir = home / "journal" / "2026-05-07" / "220000"
            older_dir.mkdir(parents=True)
            latest_dir.mkdir(parents=True)
            (older_dir / "dispatch_log.json").write_text(
                json.dumps({"pipeline": {"started_at": "2026-05-07T20:00:00Z"}, "tasks": {}}),
                encoding="utf-8",
            )
            (latest_dir / "dispatch_log.json").write_bytes(b'{"pipeline":{"started_at":"2026-05-07T22:00:00-07:00"},"bad":"\xff"}')

            result = load_latest_dispatch_log(home)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "malformed_dispatch_log")
        self.assertEqual(result["journal_dir"], str(latest_dir))
        self.assertEqual(result["path"], str(latest_dir / "dispatch_log.json"))

    def test_collect_worker_logs_returns_stdout_and_stderr_artifacts_for_latest_run(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            journal_dir = home / "journal" / "2026-05-07" / "210000"
            journal_dir.mkdir(parents=True)
            self._write_dispatch(journal_dir, tasks={})
            (journal_dir / "O00001_stdout.json").write_text('{"status":"PARTIAL","notes":"needs review"}\n', encoding="utf-8")
            (journal_dir / "O00001_stderr.log").write_text("worker warning\n", encoding="utf-8")

            result = collect_worker_logs(home, "O00001")

        self.assertTrue(result["ok"])
        self.assertEqual(result["task_id"], "O00001")
        self.assertEqual(result["stdout"]["content"], '{"status":"PARTIAL","notes":"needs review"}\n')
        self.assertEqual(result["stdout"]["json"]["status"], "PARTIAL")
        self.assertEqual(result["stderr"]["content"], "worker warning\n")
        self.assertTrue(result["stderr"]["exists"])

    def test_collect_worker_logs_identifies_missing_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            journal_dir = home / "journal" / "2026-05-07" / "210000"
            journal_dir.mkdir(parents=True)
            self._write_dispatch(journal_dir, tasks={})

            result = collect_worker_logs(home, "O00099")

        self.assertFalse(result["ok"])
        self.assertFalse(result["stdout"]["exists"])
        self.assertFalse(result["stderr"]["exists"])
        self.assertEqual(result["error"]["code"], "missing_worker_artifacts")

    def test_collect_error_summary_from_dispatch_log_and_scheduler_stderr(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)
            (home / "logs" / "omnius-launchd.err").write_text("scheduler traceback\n", encoding="utf-8")
            journal_dir = home / "journal" / "2026-05-07" / "210000"
            journal_dir.mkdir(parents=True, exist_ok=True)
            self._write_dispatch(
                journal_dir,
                tasks={
                    "O00001": {"id": "O00001", "title": "Done", "status": "SUCCESS"},
                    "O00002": {"id": "O00002", "title": "Blocked", "status": "BLOCKED", "reason": "missing token"},
                    "O00003": {"id": "O00003", "title": "Crashed", "status": "CRASH", "error": "bad JSON"},
                },
            )

            result = collect_error_summary(home)

        self.assertTrue(result["ok"])
        self.assertEqual([task["id"] for task in result["tasks"]], ["O00002", "O00003"])
        self.assertEqual(result["tasks"][0]["detail"], "missing token")
        self.assertTrue(result["scheduler_logs"]["launchd_stderr"]["exists"])
        self.assertEqual(result["scheduler_logs"]["launchd_stderr"]["content"], "scheduler traceback\n")

    def test_collect_error_summary_replaces_invalid_scheduler_stderr_bytes(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)
            (home / "logs" / "omnius-launchd.err").write_bytes(b"scheduler \xff traceback\n")
            journal_dir = home / "journal" / "2026-05-07" / "210000"
            journal_dir.mkdir(parents=True, exist_ok=True)
            self._write_dispatch(journal_dir, tasks={})

            result = collect_error_summary(home)

        self.assertTrue(result["ok"])
        self.assertEqual(result["scheduler_logs"]["launchd_stderr"]["content"], "scheduler \ufffd traceback\n")

    def test_collect_worker_logs_replaces_invalid_artifact_bytes(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            journal_dir = home / "journal" / "2026-05-07" / "210000"
            journal_dir.mkdir(parents=True)
            self._write_dispatch(journal_dir, tasks={})
            (journal_dir / "O00001_stdout.json").write_bytes(b'{"status":"FAILURE","error":"boom \xff"}\n')
            (journal_dir / "O00001_stderr.log").write_bytes(b"stderr \xff details\n")

            result = collect_worker_logs(home, "O00001")

        self.assertTrue(result["ok"])
        self.assertEqual(result["stdout"]["content"], '{"status":"FAILURE","error":"boom \ufffd"}\n')
        self.assertEqual(result["stdout"]["json"]["error"], "boom \ufffd")
        self.assertEqual(result["stderr"]["content"], "stderr \ufffd details\n")

    def test_collect_worker_logs_returns_malformed_stdout_error_after_replacement(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            journal_dir = home / "journal" / "2026-05-07" / "210000"
            journal_dir.mkdir(parents=True)
            self._write_dispatch(journal_dir, tasks={})
            (journal_dir / "O00001_stdout.json").write_bytes(b'{"status":"FAILURE","error":\xff}\n')

            result = collect_worker_logs(home, "O00001")

        self.assertTrue(result["ok"])
        self.assertEqual(result["stdout"]["content"], '{"status":"FAILURE","error":\ufffd}\n')
        self.assertEqual(result["stdout"]["error"]["code"], "malformed_worker_stdout")

    def _write_dispatch(self, journal_dir: Path, *, tasks: dict[str, object]) -> None:
        payload = {
            "pipeline": {
                "status": "completed",
                "run_date": "2026-05-07",
                "started_at": "2026-05-07T21:00:00-07:00",
            },
            "tasks": tasks,
        }
        (journal_dir / "dispatch_log.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
