from __future__ import annotations

from omnius.runners.base import PlannerInvocation, RunnerAdapter, RunnerCapability, RunnerHealth


class ClaudeRunner(RunnerAdapter):
    @property
    def name(self) -> str:
        return "claude"

    def health_check(self) -> RunnerHealth:
        return RunnerHealth(ok=True, summary="claude runner stub ready")

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
            plan_text=f"claude placeholder plan for {task_id}: {prompt}",
        )
