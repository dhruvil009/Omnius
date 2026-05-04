import json
import tempfile
import unittest
from pathlib import Path

from omnius.dispatcher import initialize_dispatch_log, load_dispatch_log, update_dispatch_log


class DispatchLogTests(unittest.TestCase):
    def test_initialize_dispatch_log_writes_pipeline_metadata_and_empty_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "dispatch.json"

            initialize_dispatch_log(
                log_path,
                pipeline_id="nightly-2026-05-03",
                runner_name="codex",
                repo_slug="example",
                branch="main",
            )

            payload = json.loads(log_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["pipeline"]["pipeline_id"], "nightly-2026-05-03")
        self.assertEqual(payload["pipeline"]["runner"], "codex")
        self.assertEqual(payload["pipeline"]["repo_slug"], "example")
        self.assertEqual(payload["pipeline"]["branch"], "main")
        self.assertEqual(payload["tasks"], {})

    def test_update_dispatch_log_rewrites_atomically_and_preserves_existing_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "dispatch.json"
            initialize_dispatch_log(
                log_path,
                pipeline_id="nightly-2026-05-03",
                runner_name="claude",
                repo_slug="example",
                branch="main",
            )

            update_dispatch_log(
                log_path,
                task_id="task-001",
                task_payload={"status": "planned", "title": "Write tests"},
            )

            payload = load_dispatch_log(log_path)

        self.assertEqual(payload["pipeline"]["runner"], "claude")
        self.assertEqual(
            payload["tasks"]["task-001"],
            {"status": "planned", "title": "Write tests"},
        )
        self.assertFalse((log_path.parent / "dispatch.json.tmp").exists())
