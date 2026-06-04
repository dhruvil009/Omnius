from __future__ import annotations

from omnius.runners.base import (
    PlannerInvocation,
    RunnerAdapter,
    RunnerCapability,
    RunnerHealth,
    RunnerVersionProbe,
    WorkerRequest,
    WorkerResult,
    load_worker_result_schema,
    load_worker_result_schema_path,
    load_worker_result_schema_text,
    normalize_runner_text_output,
)
from omnius.runners.claude import ClaudeRunner
from omnius.runners.codex import CodexRunner


def get_runner(name: str, *, planner_dayprep_mode: str = "placeholder") -> RunnerAdapter:
    if name == "codex":
        return CodexRunner(planner_dayprep_mode=planner_dayprep_mode)
    if name == "claude":
        return ClaudeRunner(planner_dayprep_mode=planner_dayprep_mode)
    raise ValueError(f"Unsupported runner: {name}")


__all__ = [
    "ClaudeRunner",
    "CodexRunner",
    "PlannerInvocation",
    "RunnerAdapter",
    "RunnerCapability",
    "RunnerHealth",
    "RunnerVersionProbe",
    "WorkerRequest",
    "WorkerResult",
    "get_runner",
    "load_worker_result_schema",
    "load_worker_result_schema_path",
    "load_worker_result_schema_text",
    "normalize_runner_text_output",
]
