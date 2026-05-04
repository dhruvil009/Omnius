from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Optional

from omnius.runners.base import RunnerAdapter


@dataclass(frozen=True)
class CommandCheck:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class RepoCheck:
    path: Path
    exists: bool
    is_git_repo: bool


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    abort_reason: str
    runner_name: str
    payload: dict[str, object]


def _default_command_check(name: str) -> CommandCheck:
    return CommandCheck(name=name, ok=True, detail="milestone-1 stub")


def _default_python_check() -> CommandCheck:
    version_info = sys.version_info
    version_text = f"{version_info.major}.{version_info.minor}.{version_info.micro}"
    return CommandCheck(name="python", ok=version_info >= (3, 11), detail=version_text)


def _default_repo_check(repo_path: Path) -> RepoCheck:
    return RepoCheck(
        path=repo_path,
        exists=repo_path.exists(),
        is_git_repo=(repo_path / ".git").exists(),
    )


def run_preflight(
    *,
    runner: RunnerAdapter,
    repo_path: Path,
    required_capabilities: list[str],
    gh_check: Optional[CommandCheck] = None,
    git_check: Optional[CommandCheck] = None,
    python_check: Optional[CommandCheck] = None,
    repo_check: Optional[RepoCheck] = None,
) -> PreflightResult:
    runner_health = runner.health_check()
    capabilities = runner.discover_capabilities()
    gh_status = gh_check or _default_command_check("gh")
    git_status = git_check or _default_command_check("git")
    python_status = python_check or _default_python_check()
    repo_status = repo_check or _default_repo_check(repo_path)

    capability_payload: dict[str, dict[str, object]] = {}
    for capability_name in required_capabilities:
        capability = capabilities.get(capability_name)
        if capability is None:
            capability_payload[capability_name] = {
                "available": False,
                "detail": "runner did not report capability",
                "required": True,
            }
            continue
        capability_payload[capability_name] = {
            "available": capability.available,
            "detail": capability.detail,
            "required": True,
        }

    abort_reason = ""
    if not runner_health.ok:
        abort_reason = "runner"
    elif not gh_status.ok:
        abort_reason = "gh"
    elif not git_status.ok:
        abort_reason = "git"
    elif not python_status.ok:
        abort_reason = "python"

    payload = {
        "runner": {"name": runner.name, "ok": runner_health.ok, "summary": runner_health.summary},
        "gh": {"ok": gh_status.ok, "detail": gh_status.detail},
        "git": {"ok": git_status.ok, "detail": git_status.detail},
        "python": {"ok": python_status.ok, "detail": python_status.detail},
        "repo": {
            "path": str(repo_status.path),
            "exists": repo_status.exists,
            "is_git_repo": repo_status.is_git_repo,
        },
        "capabilities": capability_payload,
    }

    return PreflightResult(
        ok=abort_reason == "",
        abort_reason=abort_reason,
        runner_name=runner.name,
        payload=payload,
    )
