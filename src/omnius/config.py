from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class GlobalConfig:
    timezone: str
    pipeline_cron: str
    pipeline_budget_minutes: int
    default_task_budget_minutes: int
    max_consecutive_failures: int
    notification_backend: str


@dataclass(frozen=True)
class RunnerSelection:
    default: str


@dataclass(frozen=True)
class CapabilityConfig:
    brainstorm: str
    review_diff: str
    autonomous_testing: str
    second_opinion: str


@dataclass(frozen=True)
class RepoConfig:
    slug: str
    path: str
    branch: str
    role: str
    labels: list[str]


@dataclass(frozen=True)
class OmniusConfig:
    global_config: GlobalConfig
    runner: RunnerSelection
    capabilities: CapabilityConfig
    repos: list[RepoConfig]


def _require_str(value: object) -> None:
    if not isinstance(value, str):
        raise TypeError("expected str")


def _require_int(value: object) -> None:
    if type(value) is not int:
        raise TypeError("expected int")


def _require_str_list(value: object) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError("expected list[str]")


def _validate_config_types(
    global_config: GlobalConfig,
    runner_default: object,
    capabilities: CapabilityConfig,
    repos: list[RepoConfig],
) -> None:
    _require_str(global_config.timezone)
    _require_str(global_config.pipeline_cron)
    _require_int(global_config.pipeline_budget_minutes)
    _require_int(global_config.default_task_budget_minutes)
    _require_int(global_config.max_consecutive_failures)
    _require_str(global_config.notification_backend)
    _require_str(runner_default)
    _require_str(capabilities.brainstorm)
    _require_str(capabilities.review_diff)
    _require_str(capabilities.autonomous_testing)
    _require_str(capabilities.second_opinion)

    for repo in repos:
        _require_str(repo.slug)
        _require_str(repo.path)
        _require_str(repo.branch)
        _require_str(repo.role)
        _require_str_list(repo.labels)


def load_config(path: Path) -> OmniusConfig:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Failed to load config from {path}") from exc

    try:
        runner_default = data["runner"]["default"]
        global_config = GlobalConfig(**data["global"])
        capabilities = CapabilityConfig(**data["capabilities"])
        repos = [RepoConfig(**repo) for repo in data.get("repos", [])]
        _validate_config_types(global_config, runner_default, capabilities, repos)
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"Invalid config structure in {path}") from exc

    if runner_default not in {"codex", "claude"}:
        raise ConfigError(f"Unsupported runner: {runner_default}")

    return OmniusConfig(
        global_config=global_config,
        runner=RunnerSelection(default=runner_default),
        capabilities=capabilities,
        repos=repos,
    )
