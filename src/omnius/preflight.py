from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
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
class RepoStateCheck:
    clean: bool
    merge_in_progress: bool
    rebase_in_progress: bool
    detail: str


@dataclass(frozen=True)
class FilesystemCheck:
    ok: bool
    detail: str


@dataclass(frozen=True)
class DiskSpaceCheck:
    path: Path
    ok: bool
    free_bytes: int
    min_free_bytes: int
    detail: str


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    abort_reason: str
    runner_name: str
    payload: dict[str, object]


def _completed_process_detail(completed: subprocess.CompletedProcess) -> str:
    return completed.stdout.strip() or completed.stderr.strip() or "no command output"


def _run_command_check(name: str, argv: list[str]) -> CommandCheck:
    executable = shutil.which(name)
    if executable is None:
        return CommandCheck(name=name, ok=False, detail="command not found")

    try:
        completed = subprocess.run(
            [executable, *argv],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return CommandCheck(name=name, ok=False, detail=str(exc))

    detail = _completed_process_detail(completed)
    return CommandCheck(name=name, ok=completed.returncode == 0, detail=detail)


def _default_gh_payload() -> tuple[CommandCheck, dict[str, object]]:
    version_check = _run_command_check("gh", ["--version"])
    payload = {
        "probes": {
            "version": {"ok": version_check.ok, "detail": version_check.detail},
        }
    }
    if not version_check.ok:
        return version_check, payload

    auth_check = _run_command_check("gh", ["auth", "status"])
    payload["probes"]["auth"] = {"ok": auth_check.ok, "detail": auth_check.detail}
    return auth_check, payload


def _default_command_check(name: str) -> CommandCheck:
    return _run_command_check(name, ["--version"])


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


def _default_repo_state_check(repo_path: Path) -> RepoStateCheck:
    git_dir_result = _run_git(repo_path, ["rev-parse", "--git-dir"])
    if git_dir_result.returncode != 0:
        return RepoStateCheck(
            clean=False,
            merge_in_progress=False,
            rebase_in_progress=False,
            detail=_completed_process_detail(git_dir_result),
        )
    git_dir = Path(git_dir_result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo_path / git_dir
    status_result = _run_git(repo_path, ["status", "--porcelain"])
    if status_result.returncode != 0:
        return RepoStateCheck(
            clean=False,
            merge_in_progress=False,
            rebase_in_progress=False,
            detail=_completed_process_detail(status_result),
        )
    detail = status_result.stdout.strip() or "clean"
    return RepoStateCheck(
        clean=status_result.stdout.strip() == "",
        merge_in_progress=(git_dir / "MERGE_HEAD").exists(),
        rebase_in_progress=(git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists(),
        detail=detail,
    )


def _default_filesystem_check(*, workspace_home: Path | None, journal_dir: Path | None) -> FilesystemCheck:
    if workspace_home is None or journal_dir is None:
        return FilesystemCheck(ok=True, detail="runtime path checks skipped")
    for path in (journal_dir, workspace_home / "logs", workspace_home / "state"):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe_path = path / ".omnius-write-test"
            probe_path.write_text("ok\n", encoding="utf-8")
            probe_path.unlink()
        except OSError as exc:
            return FilesystemCheck(ok=False, detail=f"{path}: {exc}")
    return FilesystemCheck(ok=True, detail="runtime paths writable")


def _default_disk_space_check(*, path: Path, min_free_bytes: int) -> DiskSpaceCheck:
    usage = shutil.disk_usage(path)
    ok = usage.free >= min_free_bytes
    detail = f"{usage.free} bytes free; minimum {min_free_bytes}"
    return DiskSpaceCheck(path=path, ok=ok, free_bytes=usage.free, min_free_bytes=min_free_bytes, detail=detail)


def _run_git(repo_path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(["git", *args], 1, stdout="", stderr=str(exc))


def run_preflight(
    *,
    runner: RunnerAdapter,
    repo_path: Path,
    capability_policy: dict[str, str],
    check_github: bool = True,
    check_repo_state: bool = False,
    check_filesystem: bool = False,
    check_disk: bool = False,
    workspace_home: Path | None = None,
    journal_dir: Path | None = None,
    min_free_bytes: int = 536_870_912,
    gh_check: Optional[CommandCheck] = None,
    git_check: Optional[CommandCheck] = None,
    python_check: Optional[CommandCheck] = None,
    repo_check: Optional[RepoCheck] = None,
    repo_state_check: Optional[RepoStateCheck] = None,
    filesystem_check: Optional[FilesystemCheck] = None,
    disk_space_check: Optional[DiskSpaceCheck] = None,
) -> PreflightResult:
    runner_health = runner.health_check()
    repo_status = repo_check or _default_repo_check(repo_path)
    repo_state_status = repo_state_check or (
        _default_repo_state_check(repo_path)
        if check_repo_state and repo_status.exists and repo_status.is_git_repo
        else RepoStateCheck(clean=True, merge_in_progress=False, rebase_in_progress=False, detail="not checked")
    )
    filesystem_status = filesystem_check or (
        _default_filesystem_check(workspace_home=workspace_home, journal_dir=journal_dir)
        if check_filesystem
        else FilesystemCheck(ok=True, detail="not checked")
    )
    disk_status = disk_space_check or (
        _default_disk_space_check(path=journal_dir or workspace_home or repo_path, min_free_bytes=min_free_bytes)
        if check_disk
        else DiskSpaceCheck(path=repo_path, ok=True, free_bytes=0, min_free_bytes=min_free_bytes, detail="not checked")
    )
    capabilities: dict[str, object] | None
    if runner_health.ok:
        capabilities = runner.discover_capabilities()
        skipped_capability_detail = "runner did not report capability"
    else:
        capabilities = None
        skipped_capability_detail = "capability discovery skipped for unhealthy runner"
    gh_payload_extra: dict[str, object] = {}
    if not check_github:
        gh_status = CommandCheck(name="gh", ok=True, detail="GitHub checks skipped for this run")
        gh_payload_extra = {"skipped": True}
    elif gh_check is None:
        gh_status, gh_payload_extra = _default_gh_payload()
    else:
        gh_status = gh_check
    git_status = git_check or _default_command_check("git")
    python_status = python_check or _default_python_check()

    capability_payload: dict[str, dict[str, object]] = {}
    for capability_name, policy in capability_policy.items():
        capability = None if capabilities is None else capabilities.get(capability_name)
        runner_available = capability.available if capability is not None else False
        if policy == "disable":
            capability_payload[capability_name] = {
                "available": False,
                "detail": "disabled by user policy",
                "enabled": False,
                "policy": policy,
                "runner_available": runner_available,
            }
            continue
        if capability is None:
            capability_payload[capability_name] = {
                "available": False,
                "detail": skipped_capability_detail,
                "enabled": True,
                "policy": policy,
                "runner_available": False,
            }
            continue
        capability_payload[capability_name] = {
            "available": capability.available,
            "detail": capability.detail,
            "enabled": True,
            "policy": policy,
            "runner_available": capability.available,
        }

    repo_ok = repo_status.exists and repo_status.is_git_repo
    repo_state_ok = repo_state_status.clean and not repo_state_status.merge_in_progress and not repo_state_status.rebase_in_progress
    available_capabilities = sorted(
        capability_name
        for capability_name, capability_entry in capability_payload.items()
        if capability_entry.get("available") is True
    )
    forced_unavailable_capabilities = sorted(
        capability_name
        for capability_name, capability_entry in capability_payload.items()
        if capability_entry.get("policy") == "force" and capability_entry.get("available") is not True
    )

    abort_reason = ""
    if not runner_health.ok:
        abort_reason = "runner"
    elif not repo_ok:
        abort_reason = "repo"
    elif not repo_state_ok:
        abort_reason = "repo_state"
    elif not filesystem_status.ok:
        abort_reason = "filesystem"
    elif not disk_status.ok:
        abort_reason = "disk"
    elif not gh_status.ok:
        abort_reason = "gh"
    elif not git_status.ok:
        abort_reason = "git"
    elif not python_status.ok:
        abort_reason = "python"

    payload = {
        "runner": {"name": runner.name, "ok": runner_health.ok, "summary": runner_health.summary},
        "gh": {"ok": gh_status.ok, "detail": gh_status.detail, **gh_payload_extra},
        "git": {"ok": git_status.ok, "detail": git_status.detail},
        "python": {"ok": python_status.ok, "detail": python_status.detail},
        "repo": {
            "path": str(repo_status.path),
            "exists": repo_status.exists,
            "is_git_repo": repo_status.is_git_repo,
        },
        "repo_state": {
            "clean": repo_state_status.clean,
            "merge_in_progress": repo_state_status.merge_in_progress,
            "rebase_in_progress": repo_state_status.rebase_in_progress,
            "detail": repo_state_status.detail,
        },
        "filesystem": {"ok": filesystem_status.ok, "detail": filesystem_status.detail},
        "disk": {
            "path": str(disk_status.path),
            "ok": disk_status.ok,
            "free_bytes": disk_status.free_bytes,
            "min_free_bytes": disk_status.min_free_bytes,
            "detail": disk_status.detail,
        },
        "capabilities": capability_payload,
        "available_capabilities": available_capabilities,
        "forced_unavailable_capabilities": forced_unavailable_capabilities,
    }

    return PreflightResult(
        ok=abort_reason == "",
        abort_reason=abort_reason,
        runner_name=runner.name,
        payload=payload,
    )
