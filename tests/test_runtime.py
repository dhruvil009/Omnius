import json
import os
import signal
import tempfile
import unittest
from pathlib import Path

from omnius.runtime import (
    PipelineAlreadyRunning,
    acquire_pipeline_lock,
    read_pipeline_pid,
    recover_pipeline_lock,
    stop_pipeline,
)


class RuntimeLockTests(unittest.TestCase):
    def test_acquire_pipeline_lock_writes_state_and_release_removes_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            journal_dir = Path(tmp) / "journal" / "2026-05-21" / "210000"

            lock = acquire_pipeline_lock(
                state_dir=state_dir,
                pipeline_id="pipeline-20260521-210000",
                journal_dir=journal_dir,
                runner_name="codex",
            )
            lock.update_worker(active_worker_pid=4321, active_worker_pgid=4321)
            payload = read_pipeline_pid(state_dir)

            self.assertIsNotNone(payload)
            self.assertEqual(payload["pid"], os.getpid())
            self.assertEqual(payload["pipeline_id"], "pipeline-20260521-210000")
            self.assertEqual(payload["journal_dir"], str(journal_dir))
            self.assertEqual(payload["runner"], "codex")
            self.assertEqual(payload["active_worker_pid"], 4321)
            self.assertEqual(payload["active_worker_pgid"], 4321)

            lock.release()

            self.assertIsNone(read_pipeline_pid(state_dir))

    def test_acquire_pipeline_lock_rejects_live_existing_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            state_dir.mkdir()
            (state_dir / "pipeline.pid").write_text(
                json.dumps({"pid": 1234, "pipeline_id": "existing"}) + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(PipelineAlreadyRunning):
                acquire_pipeline_lock(
                    state_dir=state_dir,
                    pipeline_id="new",
                    journal_dir=Path(tmp) / "journal",
                    runner_name="codex",
                    pid_checker=lambda _pid: True,
                )

    def test_acquire_pipeline_lock_replaces_stale_existing_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            state_dir.mkdir()
            (state_dir / "pipeline.pid").write_text(
                json.dumps({"pid": 1234, "pipeline_id": "stale"}) + "\n",
                encoding="utf-8",
            )

            lock = acquire_pipeline_lock(
                state_dir=state_dir,
                pipeline_id="fresh",
                journal_dir=Path(tmp) / "journal",
                runner_name="codex",
                pid_checker=lambda _pid: False,
            )
            payload = read_pipeline_pid(state_dir)

            self.assertEqual(payload["pipeline_id"], "fresh")
            self.assertEqual(payload["stale_replaced"]["pid"], 1234)
            lock.release()

    def test_stop_pipeline_dry_run_reports_without_signaling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            state_dir.mkdir()
            (state_dir / "pipeline.pid").write_text(
                json.dumps({"pid": 1234, "pipeline_id": "running"}) + "\n",
                encoding="utf-8",
            )
            signals: list[tuple[int, int]] = []

            result = stop_pipeline(
                state_dir=state_dir,
                dry_run=True,
                force=False,
                pid_checker=lambda _pid: True,
                kill_pid=lambda pid, sig: signals.append((pid, sig)),
            )

            self.assertEqual(result.status, "running")
            self.assertFalse(result.removed_lock)
            self.assertEqual(signals, [])
            self.assertIsNotNone(read_pipeline_pid(state_dir))

    def test_stop_pipeline_force_targets_worker_process_group_and_removes_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            state_dir.mkdir()
            (state_dir / "pipeline.pid").write_text(
                json.dumps(
                    {
                        "pid": 1234,
                        "pipeline_id": "running",
                        "active_worker_pid": 5678,
                        "active_worker_pgid": 5678,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            worker_group_signals: list[tuple[int, int]] = []
            pid_signals: list[tuple[int, int]] = []

            result = stop_pipeline(
                state_dir=state_dir,
                dry_run=False,
                force=True,
                pid_checker=lambda _pid: True,
                kill_pid=lambda pid, sig: pid_signals.append((pid, sig)),
                kill_pgid=lambda pgid, sig: worker_group_signals.append((pgid, sig)),
            )

            self.assertEqual(result.status, "signaled")
            self.assertTrue(result.removed_lock)
            self.assertEqual(worker_group_signals, [(5678, signal.SIGTERM)])
            self.assertEqual(pid_signals, [(1234, signal.SIGTERM)])
            self.assertIsNone(read_pipeline_pid(state_dir))

    def test_recover_pipeline_lock_removes_stale_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            state_dir.mkdir()
            (state_dir / "pipeline.pid").write_text(
                json.dumps({"pid": 1234, "pipeline_id": "stale"}) + "\n",
                encoding="utf-8",
            )

            result = recover_pipeline_lock(state_dir=state_dir, pid_checker=lambda _pid: False)

            self.assertEqual(result.status, "stale_removed")
            self.assertTrue(result.removed_lock)
            self.assertIsNone(read_pipeline_pid(state_dir))
