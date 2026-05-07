from __future__ import annotations

import os

from omnius.runners.base import (
    DayPrepInvocation,
    PlannerInvocation,
    RunnerAdapter,
    RunnerCapability,
    RunnerHealth,
    WorkerRequest,
    load_worker_result_schema_path,
)


class CodexRunner(RunnerAdapter):
    @property
    def name(self) -> str:
        return "codex"

    def health_check(self) -> RunnerHealth:
        return RunnerHealth(ok=True, summary="codex runner stub ready")

    def discover_capabilities(self) -> dict[str, RunnerCapability]:
        return {
            "brainstorm": RunnerCapability(
                name="brainstorm",
                available=True,
                detail="milestone-1 stub",
            ),
            "review_diff": RunnerCapability(
                name="review_diff",
                available=True,
                detail="milestone-1 stub",
            ),
            "autonomous_testing": RunnerCapability(
                name="autonomous_testing",
                available=True,
                detail="milestone-1 stub",
            ),
            "second_opinion": RunnerCapability(
                name="second_opinion",
                available=True,
                detail="milestone-1 stub",
            ),
        }

    def invoke_planner(self, *, task_id: str, prompt: str) -> PlannerInvocation:
        return PlannerInvocation(
            runner_name=self.name,
            task_id=task_id,
            prompt=prompt,
            plan_text=f"codex placeholder plan for {task_id}: {prompt}",
        )

    def invoke_dayprep(self, *, task_id: str, prompt: str) -> DayPrepInvocation:
        return DayPrepInvocation(
            runner_name=self.name,
            task_id=task_id,
            brief_markdown=f"# Omnius — Day Prep\n\ncodex placeholder brief for {task_id}\n",
        )

    def build_worker_command(self, request: WorkerRequest) -> list[str]:
        executable = os.environ.get("OMNIUS_CODEX_BIN", "codex")
        return [
            executable,
            "exec",
            "--cd",
            str(request.worktree_path),
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "never",
            "--output-schema",
            str(load_worker_result_schema_path()),
            request.prompt,
        ]
