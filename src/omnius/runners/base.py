from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass
from abc import ABC, abstractmethod


@dataclass(frozen=True)
class RunnerHealth:
    ok: bool
    summary: str


@dataclass(frozen=True)
class RunnerCapability:
    name: str
    available: bool
    detail: str


@dataclass(frozen=True)
class PlannerInvocation:
    runner_name: str
    task_id: str
    prompt: str
    plan_text: str


@dataclass(frozen=True)
class WorkerRequest:
    task_id: str
    prompt: str
    prompt_path: Path
    worktree_path: Path
    journal_dir: Path
    branch: str
    base_ref: str
    max_time_minutes: float


@dataclass(frozen=True)
class WorkerResult:
    status: str
    branch: str | None = None
    pr_url: str | None = None
    summary: str | None = None
    notes: str | None = None
    reason: str | None = None
    error: str | None = None


class RunnerAdapter(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> RunnerHealth:
        raise NotImplementedError

    @abstractmethod
    def discover_capabilities(self) -> dict[str, RunnerCapability]:
        raise NotImplementedError

    @abstractmethod
    def invoke_planner(self, *, task_id: str, prompt: str) -> PlannerInvocation:
        raise NotImplementedError

    @abstractmethod
    def build_worker_command(self, request: WorkerRequest) -> list[str]:
        raise NotImplementedError


def load_worker_result_schema() -> dict[str, object]:
    return json.loads(load_worker_result_schema_text())


def load_worker_result_schema_text() -> str:
    return load_worker_result_schema_path().read_text(encoding="utf-8")


def load_worker_result_schema_path() -> Path:
    return Path(__file__).resolve().parent.parent / "resources" / "schemas" / "worker_result.schema.json"
