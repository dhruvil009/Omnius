from __future__ import annotations

import json
import shutil
import subprocess
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
class RunnerVersionProbe:
    runner_name: str
    executable: str
    command: list[str]
    available: bool
    version: str | None
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
    command: list[str] | None = None
    returncode: int | None = None


@dataclass(frozen=True)
class DayPrepInvocation:
    runner_name: str
    task_id: str
    brief_markdown: str
    usage: UsageStats | None = None
    command: list[str] | None = None
    returncode: int | None = None


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

    def version_probe(self) -> RunnerVersionProbe:
        return RunnerVersionProbe(
            runner_name=self.name,
            executable=self.name,
            command=[self.name, "--version"],
            available=False,
            version=None,
            detail=f"{self.name} runner does not implement version probing",
        )

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

    @abstractmethod
    def invoke_dayprep(self, *, task_id: str, prompt: str) -> DayPrepInvocation:
        raise NotImplementedError


def load_worker_result_schema() -> dict[str, object]:
    return json.loads(load_worker_result_schema_text())


def load_worker_result_schema_text() -> str:
    return load_worker_result_schema_path().read_text(encoding="utf-8")


def load_worker_result_schema_path() -> Path:
    return Path(__file__).resolve().parent.parent / "resources" / "schemas" / "worker_result.schema.json"


def probe_runner_version(*, runner_name: str, executable: str, timeout_seconds: float = 5.0) -> RunnerVersionProbe:
    resolved_executable = shutil.which(executable)
    if resolved_executable is None:
        return RunnerVersionProbe(
            runner_name=runner_name,
            executable=executable,
            command=[executable, "--version"],
            available=False,
            version=None,
            detail=f"{runner_name} executable not found: {executable}",
        )
    command = [resolved_executable, "--version"]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except OSError as exc:
        return RunnerVersionProbe(
            runner_name=runner_name,
            executable=resolved_executable,
            command=command,
            available=False,
            version=None,
            detail=f"{runner_name} version probe failed: {exc}",
        )
    except subprocess.TimeoutExpired:
        return RunnerVersionProbe(
            runner_name=runner_name,
            executable=resolved_executable,
            command=command,
            available=False,
            version=None,
            detail=f"{runner_name} version probe timed out",
        )
    version = _first_nonempty_line(completed.stdout) or _first_nonempty_line(completed.stderr)
    if completed.returncode != 0:
        detail = version or f"{runner_name} version probe exited {completed.returncode}"
        return RunnerVersionProbe(
            runner_name=runner_name,
            executable=resolved_executable,
            command=command,
            available=False,
            version=version,
            detail=detail,
        )
    return RunnerVersionProbe(
        runner_name=runner_name,
        executable=resolved_executable,
        command=command,
        available=True,
        version=version or "<unknown version>",
        detail=version or f"{runner_name} executable is available",
    )


def run_runner_text_command(command: list[str], *, timeout_seconds: float = 600.0) -> tuple[str, int]:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise RuntimeError(f"Runner command failed ({completed.returncode}): {detail}")
    return completed.stdout, completed.returncode


def normalize_runner_text_output(output: str, *, preferred_keys: tuple[str, ...] = ("text", "message", "content")) -> str:
    stripped = output.strip()
    if not stripped:
        return ""

    full_json = _try_load_json(stripped)
    if full_json is not None:
        extracted = _extract_text_from_payload(full_json, preferred_keys, fallback_json=True)
        if extracted:
            return extracted.strip()

    for line in reversed(stripped.splitlines()):
        line_payload = _try_load_json(line.strip())
        if line_payload is None:
            continue
        extracted = _extract_text_from_payload(line_payload, preferred_keys, fallback_json=False)
        if extracted:
            return extracted.strip()

    return stripped


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


def _first_nonempty_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _try_load_json(text: str) -> object | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _extract_text_from_payload(
    payload: object,
    preferred_keys: tuple[str, ...],
    *,
    fallback_json: bool,
) -> str | None:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        parts: list[str] = []
        for item in payload:
            extracted = _extract_text_from_payload(item, preferred_keys, fallback_json=fallback_json)
            if extracted is not None and extracted.strip():
                parts.append(extracted)
        return "\n".join(parts) if parts else None
    if isinstance(payload, dict):
        for key in preferred_keys:
            if key in payload:
                extracted = _extract_text_from_payload(payload[key], preferred_keys, fallback_json=fallback_json)
                if extracted is not None:
                    return extracted
        for key in ("text", "message", "response", "result", "output", "content"):
            if key in payload:
                extracted = _extract_text_from_payload(payload[key], preferred_keys, fallback_json=fallback_json)
                if extracted is not None:
                    return extracted
        if fallback_json:
            return json.dumps(payload, indent=2, sort_keys=True)
    return None
