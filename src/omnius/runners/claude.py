from __future__ import annotations

import os

from omnius.runners.base import (
    DayPrepInvocation,
    PlannerInvocation,
    RunnerAdapter,
    RunnerCapability,
    RunnerHealth,
    RunnerVersionProbe,
    WorkerRequest,
    load_worker_result_schema_text,
    normalize_runner_text_output,
    probe_runner_version,
    run_runner_text_command,
)


class ClaudeRunner(RunnerAdapter):
    def __init__(self, *, planner_dayprep_mode: str = "placeholder") -> None:
        self.planner_dayprep_mode = planner_dayprep_mode

    @property
    def name(self) -> str:
        return "claude"

    def version_probe(self) -> RunnerVersionProbe:
        return probe_runner_version(runner_name=self.name, executable=self._executable())

    def health_check(self) -> RunnerHealth:
        probe = self.version_probe()
        if not probe.available:
            return RunnerHealth(ok=False, summary=probe.detail)
        return RunnerHealth(ok=True, summary=f"claude runner available: {probe.version}")

    def discover_capabilities(self) -> dict[str, RunnerCapability]:
        probe = self.version_probe()
        detail = f"available via {probe.version}" if probe.available else probe.detail
        return {
            "brainstorm": RunnerCapability(
                name="brainstorm",
                available=probe.available,
                detail=detail,
            ),
            "review_diff": RunnerCapability(
                name="review_diff",
                available=probe.available,
                detail=detail,
            ),
            "autonomous_testing": RunnerCapability(
                name="autonomous_testing",
                available=probe.available,
                detail=detail,
            ),
            "second_opinion": RunnerCapability(
                name="second_opinion",
                available=probe.available,
                detail=detail,
            ),
        }

    def invoke_planner(self, *, task_id: str, prompt: str) -> PlannerInvocation:
        if self.planner_dayprep_mode == "real":
            command = self._build_text_command(prompt)
            output, returncode = run_runner_text_command(command)
            return PlannerInvocation(
                runner_name=self.name,
                task_id=task_id,
                prompt=prompt,
                plan_text=normalize_runner_text_output(output, preferred_keys=("plan_text", "text", "message", "content")),
                command=_redact_prompt_argument(command),
                returncode=returncode,
            )
        return PlannerInvocation(
            runner_name=self.name,
            task_id=task_id,
            prompt=prompt,
            plan_text=f"claude placeholder plan for {task_id}: {prompt}",
        )

    def invoke_dayprep(self, *, task_id: str, prompt: str) -> DayPrepInvocation:
        if self.planner_dayprep_mode == "real":
            command = self._build_text_command(prompt)
            output, returncode = run_runner_text_command(command)
            return DayPrepInvocation(
                runner_name=self.name,
                task_id=task_id,
                brief_markdown=normalize_runner_text_output(
                    output,
                    preferred_keys=("brief_markdown", "markdown", "text", "message", "content"),
                ),
                command=_redact_prompt_argument(command),
                returncode=returncode,
            )
        return DayPrepInvocation(
            runner_name=self.name,
            task_id=task_id,
            brief_markdown=f"# Omnius — Day Prep\n\nclaude placeholder brief for {task_id}\n",
        )

    def build_worker_command(self, request: WorkerRequest) -> list[str]:
        return [
            self._executable(),
            "--print",
            "--output-format",
            "json",
            "--json-schema",
            load_worker_result_schema_text(),
            "--permission-mode",
            "dontAsk",
            request.prompt,
        ]

    def _build_text_command(self, prompt: str) -> list[str]:
        return [
            self._executable(),
            "--print",
            "--output-format",
            "json",
            prompt,
        ]

    def _executable(self) -> str:
        return os.environ.get("OMNIUS_CLAUDE_BIN", "claude")


def _redact_prompt_argument(command: list[str]) -> list[str]:
    if not command:
        return []
    return [*command[:-1], "<prompt>"]
