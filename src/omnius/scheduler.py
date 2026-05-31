from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys


MANAGED_CRON_BEGIN = "# BEGIN OMNIUS"
MANAGED_CRON_END = "# END OMNIUS"
LAUNCHD_LABEL = "dev.omnius.pipeline"


@dataclass(frozen=True)
class SchedulerStatus:
    backend: str
    installed: bool
    matches_config: bool
    location: str
    command: list[str]
    environment: dict[str, str]


def build_scheduler_environment(
    *,
    workspace_home: Path,
    timezone: str,
    path_env: str | None = None,
) -> dict[str, str]:
    return {
        "OMNIUS_HOME": str(workspace_home.expanduser()),
        "OMNIUS_SCHEDULED": "1",
        "PATH": path_env or os.environ.get("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"),
        "TZ": timezone,
    }


def build_omnius_run_command(python_executable: str | None = None) -> list[str]:
    executable = python_executable or sys.executable
    return [str(Path(executable).resolve()), "-m", "omnius", "run"]


def default_backend_for_platform(platform: str | None = None) -> str:
    platform_name = platform or sys.platform
    if platform_name == "darwin":
        return "launchd"
    if platform_name.startswith("linux"):
        return "cron"
    raise ValueError(f"Unsupported platform for scheduler install: {platform_name}")


def render_cron_block(
    schedule: str,
    command: list[str],
    *,
    workspace_home: Path | None = None,
    timezone: str | None = None,
    path_env: str | None = None,
) -> str:
    _validate_cron_schedule(schedule)
    command_text = " ".join(shlex.quote(part) for part in command)
    if workspace_home is None or timezone is None:
        run_text = f"cd $HOME && {command_text} >> $HOME/.omnius/logs/omnius-cron.log 2>&1"
    else:
        home = workspace_home.expanduser()
        scheduler_env = build_scheduler_environment(
            workspace_home=home,
            timezone=timezone,
            path_env=path_env,
        )
        env_text = " ".join(f"{key}={shlex.quote(value)}" for key, value in sorted(scheduler_env.items()))
        run_text = (
            f"cd {shlex.quote(str(home))} && env {env_text} {command_text} "
            f">> {shlex.quote(str(home / 'logs' / 'omnius-cron.log'))} 2>&1"
        )
    return (
        f"{MANAGED_CRON_BEGIN}\n"
        f"{schedule} {run_text}\n"
        f"{MANAGED_CRON_END}\n"
    )


def replace_managed_cron_block(
    existing: str,
    schedule: str,
    command: list[str],
    *,
    workspace_home: Path | None = None,
    timezone: str | None = None,
    path_env: str | None = None,
) -> str:
    preserved = _strip_managed_cron_block(existing).rstrip("\n")
    block = render_cron_block(
        schedule,
        command,
        workspace_home=workspace_home,
        timezone=timezone,
        path_env=path_env,
    ).rstrip("\n")
    if not preserved:
        return block + "\n"
    return preserved + "\n" + block + "\n"


def install_cron_schedule(
    *,
    schedule: str,
    command: list[str],
    workspace_home: Path | None = None,
    timezone: str | None = None,
    path_env: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    existing = _read_crontab(env=env)
    updated = replace_managed_cron_block(
        existing,
        schedule,
        command,
        workspace_home=workspace_home,
        timezone=timezone,
        path_env=path_env,
    )
    _write_crontab(updated, env=env)
    return "user crontab"


def uninstall_cron_schedule(*, env: dict[str, str] | None = None) -> str:
    existing = _read_crontab(env=env)
    updated = _strip_managed_cron_block(existing).rstrip("\n")
    _write_crontab(updated + ("\n" if updated else ""), env=env)
    return "user crontab"


def inspect_cron_schedule(
    *,
    schedule: str,
    command: list[str],
    workspace_home: Path | None = None,
    timezone: str | None = None,
    path_env: str | None = None,
    env: dict[str, str] | None = None,
) -> SchedulerStatus:
    existing = _read_crontab(env=env)
    expected_block = render_cron_block(
        schedule,
        command,
        workspace_home=workspace_home,
        timezone=timezone,
        path_env=path_env,
    ).strip()
    installed_block = _extract_managed_cron_block(existing)
    scheduler_env = (
        build_scheduler_environment(
            workspace_home=workspace_home,
            timezone=timezone,
            path_env=path_env,
        )
        if workspace_home is not None and timezone is not None
        else {}
    )
    return SchedulerStatus(
        backend="cron",
        installed=installed_block is not None,
        matches_config=installed_block == expected_block,
        location="user crontab",
        command=command,
        environment=scheduler_env,
    )


def translate_cron_to_launchd(schedule: str) -> list[dict[str, int]]:
    minute, hour, day_of_month, month, weekdays = _validate_cron_schedule(schedule)
    if day_of_month != "*" or month != "*":
        raise ValueError(
            "launchd backend supports only wildcard day-of-month and month with fixed hour/minute"
        )
    if not minute.isdigit() or not hour.isdigit():
        raise ValueError("launchd backend supports only fixed numeric hour/minute values")
    return [
        {"Weekday": weekday, "Hour": int(hour), "Minute": int(minute)}
        for weekday in _expand_weekday_field(weekdays)
    ]


def render_launchd_plist(
    *,
    schedule: str,
    command: list[str],
    home: Path,
    timezone: str | None = None,
    path_env: str | None = None,
) -> bytes:
    scheduler_env = build_scheduler_environment(
        workspace_home=home.expanduser(),
        timezone=timezone or os.environ.get("TZ", "UTC"),
        path_env=path_env,
    )
    payload = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": command,
        "WorkingDirectory": str(home),
        "StartCalendarInterval": translate_cron_to_launchd(schedule),
        "StandardOutPath": str(home / "logs" / "omnius-launchd.log"),
        "StandardErrorPath": str(home / "logs" / "omnius-launchd.err"),
        "EnvironmentVariables": scheduler_env,
    }
    return plistlib.dumps(payload, sort_keys=False)


def launchd_plist_path(home: Path) -> Path:
    return home.expanduser().parent / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def install_launchd_schedule(
    *,
    workspace_home: Path,
    schedule: str,
    command: list[str],
    timezone: str | None = None,
    path_env: str | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    plist_path = launchd_plist_path(workspace_home)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(
        render_launchd_plist(
            schedule=schedule,
            command=command,
            home=workspace_home.expanduser(),
            timezone=timezone,
            path_env=path_env,
        )
    )
    domain_target = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", domain_target, str(plist_path)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    subprocess.run(
        ["launchctl", "bootstrap", domain_target, str(plist_path)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return plist_path


def uninstall_launchd_schedule(*, workspace_home: Path, env: dict[str, str] | None = None) -> Path:
    plist_path = launchd_plist_path(workspace_home)
    domain_target = f"gui/{os.getuid()}"
    if plist_path.exists():
        subprocess.run(
            ["launchctl", "bootout", domain_target, str(plist_path)],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        plist_path.unlink()
    return plist_path


def inspect_launchd_schedule(
    *,
    workspace_home: Path,
    schedule: str,
    command: list[str],
    timezone: str | None = None,
    path_env: str | None = None,
) -> SchedulerStatus:
    plist_path = launchd_plist_path(workspace_home)
    scheduler_env = build_scheduler_environment(
        workspace_home=workspace_home.expanduser(),
        timezone=timezone or os.environ.get("TZ", "UTC"),
        path_env=path_env,
    )
    if not plist_path.exists():
        return SchedulerStatus(
            backend="launchd",
            installed=False,
            matches_config=False,
            location=str(plist_path),
            command=command,
            environment=scheduler_env,
        )
    payload = plistlib.loads(plist_path.read_bytes())
    expected_payload = plistlib.loads(
        render_launchd_plist(
            schedule=schedule,
            command=command,
            home=workspace_home.expanduser(),
            timezone=timezone,
            path_env=path_env,
        )
    )
    return SchedulerStatus(
        backend="launchd",
        installed=True,
        matches_config=payload == expected_payload,
        location=str(plist_path),
        command=command,
        environment=scheduler_env,
    )


def _read_crontab(*, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["crontab", "-l"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode == 0:
        return result.stdout
    if result.returncode == 1:
        return ""
    raise RuntimeError(result.stderr.strip() or "Failed to read crontab")


def _write_crontab(text: str, *, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["crontab", "-"],
        input=text,
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )


def _strip_managed_cron_block(existing: str) -> str:
    lines = existing.splitlines()
    kept: list[str] = []
    inside_block = False
    for line in lines:
        if line == MANAGED_CRON_BEGIN:
            inside_block = True
            continue
        if line == MANAGED_CRON_END:
            inside_block = False
            continue
        if not inside_block:
            kept.append(line)
    return "\n".join(kept).rstrip("\n")


def _extract_managed_cron_block(existing: str) -> str | None:
    lines = existing.splitlines()
    collected: list[str] = []
    inside_block = False
    for line in lines:
        if line == MANAGED_CRON_BEGIN:
            inside_block = True
        if inside_block:
            collected.append(line)
        if line == MANAGED_CRON_END and inside_block:
            return "\n".join(collected)
    return None


def _validate_cron_schedule(schedule: str) -> tuple[str, str, str, str, str]:
    fields = schedule.split()
    if len(fields) != 5:
        raise ValueError("Scheduler install requires a 5-field cron expression")
    return fields[0], fields[1], fields[2], fields[3], fields[4]


def _expand_weekday_field(field: str) -> list[int]:
    values: list[int] = []
    for chunk in field.split(","):
        chunk = chunk.strip()
        if not chunk:
            raise ValueError("launchd backend received an empty weekday value")
        if chunk == "*":
            values.extend(range(0, 7))
            continue
        if "-" in chunk:
            start_text, end_text = chunk.split("-", 1)
            if not start_text.isdigit() or not end_text.isdigit():
                raise ValueError("launchd backend requires numeric weekday ranges")
            start = int(start_text)
            end = int(end_text)
            values.extend(range(start, end + 1))
            continue
        if not chunk.isdigit():
            raise ValueError("launchd backend requires numeric weekdays")
        values.append(int(chunk))
    if not values:
        raise ValueError("launchd backend needs at least one weekday")
    unique = sorted(dict.fromkeys(values))
    for value in unique:
        if value < 0 or value > 6:
            raise ValueError("launchd backend weekday values must be between 0 and 6")
    return unique
