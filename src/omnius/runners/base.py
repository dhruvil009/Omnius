from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


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
