import os
import unittest
from pathlib import Path
from unittest.mock import patch

from omnius.runners import (
    ClaudeRunner,
    CodexRunner,
    WorkerRequest,
    WorkerResult,
    load_worker_result_schema,
    load_worker_result_schema_path,
    load_worker_result_schema_text,
)


class RunnerCommandTests(unittest.TestCase):
    def test_worker_result_schema_resource_loads(self) -> None:
        schema = load_worker_result_schema()
        path = load_worker_result_schema_path()
        schema_text = load_worker_result_schema_text()

        self.assertTrue(path.exists())
        self.assertEqual(schema["type"], "object")
        self.assertIn("status", schema["required"])
        self.assertIn('"status"', schema_text)

    def test_worker_request_and_result_are_normalized_dataclasses(self) -> None:
        request = WorkerRequest(
            task_id="O00001",
            prompt="Implement the task",
            prompt_path=Path("/tmp/prompt.md"),
            worktree_path=Path("/tmp/worktree"),
            journal_dir=Path("/tmp/journal"),
            branch="omnius/2026-05-05/O00001",
            base_ref="origin/main",
            max_time_minutes=120,
        )
        result = WorkerResult(status="SUCCESS", branch=request.branch, summary="done")

        self.assertEqual(request.task_id, "O00001")
        self.assertEqual(request.max_time_minutes, 120)
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.summary, "done")

    def test_codex_worker_command_uses_noninteractive_exec_with_schema(self) -> None:
        runner = CodexRunner()
        request = WorkerRequest(
            task_id="O00001",
            prompt="Implement the task",
            prompt_path=Path("/tmp/prompt.md"),
            worktree_path=Path("/tmp/worktree"),
            journal_dir=Path("/tmp/journal"),
            branch="omnius/2026-05-05/O00001",
            base_ref="origin/main",
            max_time_minutes=120,
        )

        command = runner.build_worker_command(request)

        self.assertEqual(command[:2], ["codex", "exec"])
        self.assertIn("--cd", command)
        self.assertIn("--output-schema", command)
        self.assertIn(str(request.worktree_path), command)
        self.assertEqual(command[-1], request.prompt)

    def test_claude_worker_command_uses_print_json_schema_mode(self) -> None:
        runner = ClaudeRunner()
        request = WorkerRequest(
            task_id="O00001",
            prompt="Implement the task",
            prompt_path=Path("/tmp/prompt.md"),
            worktree_path=Path("/tmp/worktree"),
            journal_dir=Path("/tmp/journal"),
            branch="omnius/2026-05-05/O00001",
            base_ref="origin/main",
            max_time_minutes=120,
        )

        command = runner.build_worker_command(request)

        self.assertEqual(command[0], "claude")
        self.assertIn("--print", command)
        self.assertIn("--output-format", command)
        self.assertIn("json", command)
        self.assertIn("--json-schema", command)
        self.assertIn("--permission-mode", command)
        self.assertEqual(command[-1], request.prompt)

    def test_runner_commands_honor_executable_env_overrides(self) -> None:
        request = WorkerRequest(
            task_id="O00001",
            prompt="Implement the task",
            prompt_path=Path("/tmp/prompt.md"),
            worktree_path=Path("/tmp/worktree"),
            journal_dir=Path("/tmp/journal"),
            branch="omnius/2026-05-05/O00001",
            base_ref="origin/main",
            max_time_minutes=120,
        )
        with patch.dict(
            os.environ,
            {
                "OMNIUS_CODEX_BIN": "/tmp/fake-codex",
                "OMNIUS_CLAUDE_BIN": "/tmp/fake-claude",
            },
            clear=False,
        ):
            codex_command = CodexRunner().build_worker_command(request)
            claude_command = ClaudeRunner().build_worker_command(request)

        self.assertEqual(codex_command[0], "/tmp/fake-codex")
        self.assertEqual(claude_command[0], "/tmp/fake-claude")
