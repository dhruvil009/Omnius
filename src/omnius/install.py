from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from omnius.config import (
    ConfigError,
    SUPPORTED_RUNNERS,
    load_config,
    render_default_config,
)
from omnius.scheduler import (
    build_omnius_run_command,
    default_backend_for_platform,
    inspect_cron_schedule,
    inspect_launchd_schedule,
    install_cron_schedule,
    install_launchd_schedule,
    uninstall_cron_schedule,
    uninstall_launchd_schedule,
)
from omnius.workspace import bootstrap_workspace


INSTALL_STATE_FILENAME = "install_state.json"


@dataclass(frozen=True)
class InstallRequest:
    backend: str | None
    runner: str | None
    repo_path: str | None
    repo_slug: str | None
    repo_branch: str | None
    non_interactive: bool


@dataclass(frozen=True)
class LifecycleRequest:
    backend: str | None


def run_install(*, request: InstallRequest, workspace_home: Path, cwd: Path) -> int:
    paths = bootstrap_workspace(workspace_home)
    config_path = workspace_home / "omnius.toml"
    if not config_path.exists():
        try:
            bootstrap = _resolve_bootstrap_answers(request=request, cwd=cwd)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        config_path.write_text(
            render_default_config(
                timezone=bootstrap["timezone"],
                runner_default=bootstrap["runner"],
                repo_slug=bootstrap["repo_slug"],
                repo_path=bootstrap["repo_path"],
                repo_branch=bootstrap["repo_branch"],
            ),
            encoding="utf-8",
        )

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        backend = _resolve_backend(request.backend)
        command = build_omnius_run_command()
        location = _install_backend(
            backend=backend,
            workspace_home=workspace_home,
            schedule=config.global_config.pipeline_cron,
            command=command,
        )
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    _write_install_state(
        paths.state_dir / INSTALL_STATE_FILENAME,
        backend=backend,
        command=command,
        location=location,
    )
    print(f"Installed Omnius scheduler via {backend}")
    print(f"workspace: {workspace_home}")
    print(f"config: {config_path}")
    print(f"schedule: {config.global_config.pipeline_cron}")
    print(f"command: {' '.join(command)}")
    print(f"location: {location}")
    return 0


def run_doctor(*, request: LifecycleRequest, workspace_home: Path) -> int:
    config_path = workspace_home / "omnius.toml"
    install_state = _read_install_state(workspace_home / "state" / INSTALL_STATE_FILENAME)
    backend = request.backend or install_state.get("backend") or _resolve_backend(None)
    command = install_state.get("command") or build_omnius_run_command()

    config = None
    if config_path.exists():
        try:
            config = load_config(config_path)
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    if config is None:
        installed = bool(install_state)
        location = str(install_state.get("location", "<unknown>"))
        print(f"workspace: {workspace_home}")
        print(f"config: {config_path} (missing)")
        print(f"backend: {backend}")
        print(f"installed: {'yes' if installed else 'no'}")
        print(f"location: {location}")
        return 0

    try:
        status = _inspect_backend(
            backend=backend,
            workspace_home=workspace_home,
            schedule=config.global_config.pipeline_cron,
            command=list(command),
        )
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"workspace: {workspace_home}")
    print(f"config: {config_path}")
    print(f"backend: {status.backend}")
    print(f"installed: {'yes' if status.installed else 'no'}")
    print(f"matches_config: {'yes' if status.matches_config else 'no'}")
    print(f"location: {status.location}")
    print(f"runner_default: {config.runner.default}")
    print(f"pipeline_cron: {config.global_config.pipeline_cron}")
    print(f"command: {' '.join(status.command)}")
    return 0


def run_uninstall(*, request: LifecycleRequest, workspace_home: Path) -> int:
    install_state_path = workspace_home / "state" / INSTALL_STATE_FILENAME
    install_state = _read_install_state(install_state_path)
    backend = request.backend or install_state.get("backend") or _resolve_backend(None)
    try:
        location = _uninstall_backend(backend=backend, workspace_home=workspace_home)
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if install_state_path.exists():
        install_state_path.unlink()
    print(f"Removed Omnius scheduler via {backend}")
    print(f"location: {location}")
    return 0


def _resolve_backend(requested_backend: str | None) -> str:
    backend = requested_backend or default_backend_for_platform()
    if backend not in {"cron", "launchd"}:
        raise ValueError(f"Unsupported scheduler backend: {backend}")
    if backend == "launchd" and sys.platform != "darwin":
        raise ValueError("launchd backend is supported only on macOS")
    return backend


def _resolve_bootstrap_answers(*, request: InstallRequest, cwd: Path) -> dict[str, str]:
    repo_path = request.repo_path or _infer_repo_path(cwd)
    runner = request.runner or _infer_runner()
    if request.non_interactive:
        if runner not in SUPPORTED_RUNNERS:
            raise ValueError("Non-interactive install requires --runner codex|claude")
        if repo_path is None:
            raise ValueError("Non-interactive install requires --repo-path or a detectable git repo")
        repo_root = Path(repo_path).expanduser().resolve()
        if not repo_root.exists():
            raise ValueError(f"Repo path does not exist: {repo_root}")
        if not _is_git_repo(repo_root):
            raise ValueError(f"Repo path is not a git repo: {repo_root}")
        return {
            "timezone": _detect_timezone(),
            "runner": runner,
            "repo_path": str(repo_root),
            "repo_slug": request.repo_slug or repo_root.name,
            "repo_branch": request.repo_branch or _detect_git_branch(repo_root),
        }

    chosen_runner = runner or _prompt_with_default("Default runner [codex]: ", "codex")
    if chosen_runner not in SUPPORTED_RUNNERS:
        raise ValueError(f"Unsupported runner: {chosen_runner}")

    default_repo = str(repo_path) if repo_path is not None else ""
    chosen_repo_text = _prompt_with_default("Repo path: ", default_repo)
    if not chosen_repo_text:
        raise ValueError("Repo path is required")
    repo_root = Path(chosen_repo_text).expanduser().resolve()
    if not _is_git_repo(repo_root):
        raise ValueError(f"Repo path is not a git repo: {repo_root}")
    chosen_branch = request.repo_branch or _prompt_with_default(
        f"Repo branch [{_detect_git_branch(repo_root)}]: ",
        _detect_git_branch(repo_root),
    )
    return {
        "timezone": _prompt_with_default(f"Timezone [{_detect_timezone()}]: ", _detect_timezone()),
        "runner": chosen_runner,
        "repo_path": str(repo_root),
        "repo_slug": request.repo_slug or _prompt_with_default(f"Repo slug [{repo_root.name}]: ", repo_root.name),
        "repo_branch": chosen_branch,
    }


def _prompt_with_default(prompt: str, default: str) -> str:
    response = input(prompt).strip()
    return response or default


def _detect_timezone() -> str:
    tz_env = os.environ.get("TZ")
    if tz_env:
        return tz_env
    tzinfo = datetime.now().astimezone().tzinfo
    if tzinfo is not None and hasattr(tzinfo, "key"):
        key = getattr(tzinfo, "key")
        if isinstance(key, str) and key:
            return key
    if tzinfo is not None:
        tz_name = tzinfo.tzname(datetime.now())
        if tz_name:
            return tz_name
    return "UTC"


def _infer_runner() -> str | None:
    if shutil.which("codex"):
        return "codex"
    if shutil.which("claude"):
        return "claude"
    return None


def _infer_repo_path(cwd: Path) -> str | None:
    repo_root = _git_repo_root(cwd)
    if repo_root is None:
        return None
    if _looks_like_omnius_repo(repo_root):
        return None
    return str(repo_root)


def _looks_like_omnius_repo(path: Path) -> bool:
    return (path / "src" / "omnius").exists() and (path / "pyproject.toml").exists()


def _is_git_repo(path: Path) -> bool:
    return _git_repo_root(path) is not None


def _git_repo_root(path: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(result.stdout.strip()).resolve()


def _detect_git_branch(path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "main"
    branch = result.stdout.strip()
    return branch or "main"


def _install_backend(*, backend: str, workspace_home: Path, schedule: str, command: list[str]) -> str:
    if backend == "cron":
        return install_cron_schedule(schedule=schedule, command=command)
    if backend == "launchd":
        return str(
            install_launchd_schedule(
                workspace_home=workspace_home,
                schedule=schedule,
                command=command,
            )
        )
    raise ValueError(f"Unsupported scheduler backend: {backend}")


def _inspect_backend(*, backend: str, workspace_home: Path, schedule: str, command: list[str]):
    if backend == "cron":
        return inspect_cron_schedule(schedule=schedule, command=command)
    if backend == "launchd":
        return inspect_launchd_schedule(
            workspace_home=workspace_home,
            schedule=schedule,
            command=command,
        )
    raise ValueError(f"Unsupported scheduler backend: {backend}")


def _uninstall_backend(*, backend: str, workspace_home: Path) -> str:
    if backend == "cron":
        return uninstall_cron_schedule()
    if backend == "launchd":
        return str(uninstall_launchd_schedule(workspace_home=workspace_home))
    raise ValueError(f"Unsupported scheduler backend: {backend}")


def _write_install_state(path: Path, *, backend: str, command: list[str], location: str) -> None:
    payload = {
        "backend": backend,
        "command": command,
        "location": location,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_install_state(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
