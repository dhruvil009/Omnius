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
class UsageStats:
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_create_tokens: int | None = None
    turns: int | None = None
    model: str | None = None


@dataclass(frozen=True)
class PlannerInvocation:
    runner_name: str
    task_id: str
    prompt: str
    plan_text: str
    usage: UsageStats | None = None


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
    usage: UsageStats | None = None


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


def parse_usage_stats(payload: object) -> UsageStats | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError("Usage payload must be a JSON object")
    return UsageStats(
        cost_usd=_coerce_optional_number(payload.get("cost_usd"), "cost_usd"),
        input_tokens=_coerce_optional_int(payload.get("input_tokens"), "input_tokens"),
        output_tokens=_coerce_optional_int(payload.get("output_tokens"), "output_tokens"),
        cache_read_tokens=_coerce_optional_int(payload.get("cache_read_tokens"), "cache_read_tokens"),
        cache_create_tokens=_coerce_optional_int(payload.get("cache_create_tokens"), "cache_create_tokens"),
        turns=_coerce_optional_int(payload.get("turns"), "turns"),
        model=_coerce_optional_string(payload.get("model"), "model"),
    )


def usage_stats_to_dict(usage: UsageStats | None) -> dict[str, object] | None:
    if usage is None:
        return None
    payload: dict[str, object] = {}
    if usage.cost_usd is not None:
        payload["cost_usd"] = usage.cost_usd
    if usage.input_tokens is not None:
        payload["input_tokens"] = usage.input_tokens
    if usage.output_tokens is not None:
        payload["output_tokens"] = usage.output_tokens
    if usage.cache_read_tokens is not None:
        payload["cache_read_tokens"] = usage.cache_read_tokens
    if usage.cache_create_tokens is not None:
        payload["cache_create_tokens"] = usage.cache_create_tokens
    if usage.turns is not None:
        payload["turns"] = usage.turns
    if usage.model is not None:
        payload["model"] = usage.model
    return payload or None


def _coerce_optional_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError(f"Usage field '{field_name}' must be an integer")
    return value


def _coerce_optional_number(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Usage field '{field_name}' must be a number")
    return float(value)


def _coerce_optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Usage field '{field_name}' must be a string")
    return value
