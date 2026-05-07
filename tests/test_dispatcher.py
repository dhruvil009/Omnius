import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        self.assertEqual(payload["pipeline"]["circuit_breaker"], {"state": "closed", "consecutive_failures": 0})
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
                patch={
                    "tasks": {
                        "task-001": {"status": "planned", "title": "Write tests"},
                    },
                },
            )

            payload = load_dispatch_log(log_path)

        self.assertEqual(payload["pipeline"]["runner"], "claude")
        self.assertEqual(
            payload["tasks"]["task-001"],
            {"status": "planned", "title": "Write tests"},
        )
        self.assertFalse((log_path.parent / "dispatch.json.tmp").exists())

    def test_initialize_dispatch_log_fails_when_log_already_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "dispatch.json"
            initialize_dispatch_log(
                log_path,
                pipeline_id="nightly-2026-05-03",
                runner_name="codex",
                repo_slug="example",
                branch="main",
            )

            with self.assertRaises(FileExistsError):
                initialize_dispatch_log(
                    log_path,
                    pipeline_id="nightly-2026-05-04",
                    runner_name="claude",
                    repo_slug="example",
                    branch="main",
                )

            payload = load_dispatch_log(log_path)

        self.assertEqual(payload["pipeline"]["pipeline_id"], "nightly-2026-05-03")
        self.assertEqual(payload["pipeline"]["runner"], "codex")

    def test_update_dispatch_log_uses_unique_temp_path_instead_of_fixed_shared_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "dispatch.json"
            initialize_dispatch_log(
                log_path,
                pipeline_id="nightly-2026-05-03",
                runner_name="claude",
                repo_slug="example",
                branch="main",
            )

            original_replace = __import__("os").replace
            replace_calls: list[tuple[str, str]] = []

            def recording_replace(src: str, dst: str) -> None:
                replace_calls.append((src, dst))
                original_replace(src, dst)

            with patch("omnius.dispatcher.os.replace", side_effect=recording_replace):
                update_dispatch_log(
                    log_path,
                    patch={"tasks": {"task-001": {"status": "planned"}}},
                )

        self.assertEqual(len(replace_calls), 1)
        replaced_src = str(replace_calls[0][0])
        replaced_dst = str(replace_calls[0][1])
        self.assertEqual(replaced_dst, str(log_path))
        self.assertNotEqual(replaced_src, str(log_path.parent / "dispatch.json.tmp"))
        self.assertTrue(replaced_src.startswith(str(log_path.parent / "dispatch.json")))

    def test_initialize_dispatch_log_does_not_use_exists_precheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "dispatch.json"

            with patch("omnius.dispatcher.Path.exists", side_effect=AssertionError("exists precheck used")):
                initialize_dispatch_log(
                    log_path,
                    pipeline_id="nightly-2026-05-03",
                    runner_name="codex",
                    repo_slug="example",
                    branch="main",
                )

            payload = load_dispatch_log(log_path)

        self.assertEqual(payload["pipeline"]["pipeline_id"], "nightly-2026-05-03")

    def test_update_dispatch_log_merges_generic_nested_patch_and_preserves_existing_content(self) -> None:
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
                patch={
                    "tasks": {
                        "task-001": {"status": "planned", "title": "Write tests"},
                    },
                    "meta": {"attempts": 1},
                },
            )
            update_dispatch_log(
                log_path,
                patch={
                    "tasks": {
                        "task-001": {"status": "running"},
                        "task-002": {"status": "queued"},
                    },
                    "meta": {"last_runner": "claude"},
                },
            )

            payload = load_dispatch_log(log_path)

        self.assertEqual(payload["pipeline"]["runner"], "claude")
        self.assertEqual(payload["tasks"]["task-001"]["status"], "running")
        self.assertEqual(payload["tasks"]["task-001"]["title"], "Write tests")
        self.assertEqual(payload["tasks"]["task-002"], {"status": "queued"})
        self.assertEqual(payload["meta"], {"attempts": 1, "last_runner": "claude"})
