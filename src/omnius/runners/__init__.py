from __future__ import annotations

from omnius.runners.base import PlannerInvocation, RunnerAdapter, RunnerCapability, RunnerHealth
from omnius.runners.claude import ClaudeRunner
from omnius.runners.codex import CodexRunner


def get_runner(name: str) -> RunnerAdapter:
    if name == "codex":
        return CodexRunner()
    if name == "claude":
        return ClaudeRunner()
    raise ValueError(f"Unsupported runner: {name}")


__all__ = [
    "ClaudeRunner",
    "CodexRunner",
    "PlannerInvocation",
    "RunnerAdapter",
    "RunnerCapability",
    "RunnerHealth",
    "get_runner",
]
