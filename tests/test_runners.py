import os
import tempfile
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
from omnius.runners.base import normalize_runner_text_output


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

    def test_codex_health_uses_non_mutating_fake_binary_version_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_codex = self._write_fake_runner(Path(tmp), "fake-codex", version="codex 1.2.3", output="{}")
            with patch.dict(os.environ, {"OMNIUS_CODEX_BIN": str(fake_codex)}, clear=False):
                runner = CodexRunner()

                probe = runner.version_probe()
                health = runner.health_check()

        self.assertTrue(probe.available)
        self.assertEqual(probe.version, "codex 1.2.3")
        self.assertTrue(health.ok)
        self.assertIn("codex 1.2.3", health.summary)

    def test_codex_health_reports_missing_binary(self) -> None:
        with patch.dict(os.environ, {"OMNIUS_CODEX_BIN": "/tmp/omnius-missing-codex"}, clear=False):
            health = CodexRunner().health_check()

        self.assertFalse(health.ok)
        self.assertIn("not found", health.summary)

    def test_real_codex_planner_normalizes_json_and_records_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_codex = self._write_fake_runner(
                Path(tmp),
                "fake-codex",
                version="codex 1.2.3",
                output='{"plan_text":"Plan from fake codex"}',
            )
            with patch.dict(os.environ, {"OMNIUS_CODEX_BIN": str(fake_codex)}, clear=False):
                invocation = CodexRunner(planner_dayprep_mode="real").invoke_planner(
                    task_id="planner-run",
                    prompt="Plan this run",
                )

        self.assertEqual(invocation.plan_text, "Plan from fake codex")
        self.assertIsNotNone(invocation.command)
        self.assertEqual(invocation.command[0], str(fake_codex))
        self.assertIn("exec", invocation.command)

    def test_real_claude_dayprep_normalizes_content_fixture_and_records_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_claude = self._write_fake_runner(
                Path(tmp),
                "fake-claude",
                version="claude 4.0.0",
                output='{"content":[{"type":"text","text":"# Brief\\n\\nFrom fake claude"}]}',
            )
            with patch.dict(os.environ, {"OMNIUS_CLAUDE_BIN": str(fake_claude)}, clear=False):
                invocation = ClaudeRunner(planner_dayprep_mode="real").invoke_dayprep(
                    task_id="dayprep-run",
                    prompt="Compile brief",
                )

        self.assertEqual(invocation.brief_markdown, "# Brief\n\nFrom fake claude")
        self.assertIsNotNone(invocation.command)
        self.assertEqual(invocation.command[0], str(fake_claude))
        self.assertIn("--print", invocation.command)

    def test_placeholder_planner_does_not_execute_configured_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "called"
            fake_codex = Path(tmp) / "fake-codex"
            fake_codex.write_text(
                f"#!/bin/sh\nprintf called > {marker}\nexit 99\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)

            with patch.dict(os.environ, {"OMNIUS_CODEX_BIN": str(fake_codex)}, clear=False):
                invocation = CodexRunner().invoke_planner(task_id="planner-run", prompt="Plan this run")

        self.assertIn("placeholder", invocation.plan_text)
        self.assertIsNone(invocation.command)
        self.assertFalse(marker.exists())

    def test_normalize_runner_text_output_accepts_jsonl_fixture_output(self) -> None:
        output = '{"event":"started"}\n{"plan_text":"Final plan"}\n'

        self.assertEqual(normalize_runner_text_output(output, preferred_keys=("plan_text",)), "Final plan")

    def test_normalize_runner_text_output_skips_trailing_jsonl_events(self) -> None:
        output = '{"plan_text":"Final plan"}\n{"event":"done"}\n'

        self.assertEqual(normalize_runner_text_output(output, preferred_keys=("plan_text",)), "Final plan")

    def _write_fake_runner(self, directory: Path, name: str, *, version: str, output: str) -> Path:
        path = directory / name
        path.write_text(
            "\n".join(
                [
                    "#!/bin/sh",
                    'if [ "$1" = "--version" ]; then',
                    f"  printf '%s\\n' '{version}'",
                    "  exit 0",
                    "fi",
                    "cat <<'EOF'",
                    output,
                    "EOF",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path
