from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil


_ACTIVE_LINE_RE = re.compile(r"^- (?P<task_id>O\d{5}): .* \[file: (?P<filename>[^\]]+)\]$")
_RECURRING_FILENAME_RE = re.compile(r"^(?P<task_id>R\d{5}).*\.md$")
_RECURRING_WEEKLY_SCHEDULE_RE = re.compile(r"^weekly:(mon|tue|wed|thu|fri|sat|sun)$")
_RECURRING_MONTHLY_SCHEDULE_RE = re.compile(r"^monthly:(\d{1,2})$")
_RECURRING_EVERY_SCHEDULE_RE = re.compile(r"^every:(\d+)d$")


@dataclass(frozen=True)
class LocalTaskEntry:
    task_id: str
    filename: str
    body: str


@dataclass(frozen=True)
class RecurringTaskEntry:
    task_id: str
    filename: str
    title: str
    repo_slug: str
    schedule: str
    task_type: str
    complexity: str
    max_time_minutes: int | None
    retry_on_failure: str
    only_if_last_succeeded: bool
    body: str


def load_local_task_entries(home: Path) -> list[LocalTaskEntry]:
    index_text = (home / "tasks.md").read_text(encoding="utf-8")
    active_section = _extract_active_section(index_text)
    entries: list[LocalTaskEntry] = []

    for line_number, raw_line in enumerate(active_section.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        match = _ACTIVE_LINE_RE.match(line)
        if match is None:
            raise ValueError(f"Malformed task entry in ## Active at line {line_number}: {line}")

        filename = match.group("filename")
        body = (home / "tasks" / filename).read_text(encoding="utf-8")
        entries.append(
            LocalTaskEntry(
                task_id=match.group("task_id"),
                filename=filename,
                body=body,
            )
        )

    return entries


def load_recurring_task_entries(home: Path) -> list[RecurringTaskEntry]:
    entries: list[RecurringTaskEntry] = []
    recurring_dir = home / "tasks" / "recurring"

    for path in sorted(recurring_dir.glob("R*.md")):
        match = _RECURRING_FILENAME_RE.match(path.name)
        if match is None:
            continue
        body = path.read_text(encoding="utf-8")
        metadata = _parse_frontmatter(body)
        max_time_minutes_raw = metadata.get("max_time_minutes")
        schedule = _require_frontmatter_value(metadata, "schedule", match.group("task_id"))
        entries.append(
            RecurringTaskEntry(
                task_id=match.group("task_id"),
                filename=path.name,
                title=_require_frontmatter_value(metadata, "title", match.group("task_id")),
                repo_slug=_require_frontmatter_value(metadata, "repo", match.group("task_id")),
                schedule=_validate_schedule(schedule),
                task_type=metadata.get("type", "implementation"),
                complexity=metadata.get("complexity", "small"),
                max_time_minutes=_parse_max_time_minutes(max_time_minutes_raw, task_id=match.group("task_id")),
                retry_on_failure=_parse_retry_policy(metadata.get("retry_on_failure", "next_run")),
                only_if_last_succeeded=_parse_boolean(metadata.get("only_if_last_succeeded", "false")),
                body=body,
            )
        )

    return entries


def render_local_tasks_section(entries: list[LocalTaskEntry]) -> str:
    if not entries:
        return "<none>"

    return "\n\n".join(
        f"--- Task ID: {entry.task_id} | File: {entry.filename} ---\n{entry.body}"
        for entry in entries
    )


def _extract_active_section(index_text: str) -> str:
    try:
        _, active_and_after = index_text.split("## Active", 1)
        active_section, _ = active_and_after.split("## Completed", 1)
    except ValueError as exc:
        raise ValueError("tasks.md must contain both '## Active' and '## Completed' sections") from exc
    return active_section


def archive_local_task_success(
    *,
    home: Path,
    task_id: str,
    filename: str,
    run_date: str,
) -> None:
    archived_completed_line = _build_completed_line(task_id=task_id, filename=filename, run_date=run_date, home=home)
    _relocate_local_task(
        home=home,
        task_id=task_id,
        filename=filename,
        destination_dir=home / "tasks" / "completed",
        completed_line=archived_completed_line,
    )


def move_local_task_to_pending_approval(
    *,
    home: Path,
    task_id: str,
    filename: str,
) -> None:
    _relocate_local_task(
        home=home,
        task_id=task_id,
        filename=filename,
        destination_dir=home / "tasks" / "pending_approval",
        completed_line=None,
    )


def _build_completed_line(*, task_id: str, filename: str, run_date: str, home: Path) -> str:
    index_path = home / "tasks.md"
    index_text = index_path.read_text(encoding="utf-8")
    try:
        _, active_and_after = index_text.split("## Active", 1)
        active_section, _ = active_and_after.split("## Completed", 1)
    except ValueError as exc:
        raise ValueError("tasks.md must contain both '## Active' and '## Completed' sections") from exc

    for raw_line in active_section.splitlines():
        stripped = raw_line.strip()
        if _matches_task_index_line(stripped, task_id=task_id, filename=filename):
            return f"- {run_date}: {stripped.removeprefix('- ')}"
    raise ValueError(f"Could not find active tasks.md entry for {task_id} / {filename}")


def _relocate_local_task(
    *,
    home: Path,
    task_id: str,
    filename: str,
    destination_dir: Path,
    completed_line: str | None,
) -> None:
    source_path = home / "tasks" / filename
    destination_path = destination_dir / filename
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    index_path = home / "tasks.md"
    index_text = index_path.read_text(encoding="utf-8")
    try:
        before_active, active_and_after = index_text.split("## Active", 1)
        active_section, after_completed = active_and_after.split("## Completed", 1)
    except ValueError as exc:
        raise ValueError("tasks.md must contain both '## Active' and '## Completed' sections") from exc

    active_lines = active_section.splitlines()
    remaining_active_lines: list[str] = []
    archived_line: str | None = None
    for raw_line in active_lines:
        stripped = raw_line.strip()
        if _matches_task_index_line(stripped, task_id=task_id, filename=filename):
            archived_line = stripped
            continue
        if stripped or raw_line == "":
            remaining_active_lines.append(raw_line)

    if archived_line is None:
        raise ValueError(f"Could not find active tasks.md entry for {task_id} / {filename}")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_path), str(destination_path))

    active_body = "\n".join(remaining_active_lines).strip("\n")
    completed_body = after_completed.rstrip("\n")
    if completed_line is not None:
        if completed_body.strip():
            completed_body = f"{completed_body}\n{completed_line}"
        else:
            completed_body = completed_line

    new_index_text = (
        f"{before_active.rstrip()}\n\n"
        f"## Active\n"
        f"{active_body}\n\n"
        f"## Completed\n"
        f"{completed_body}\n"
    )
    index_path.write_text(new_index_text, encoding="utf-8")


def _matches_task_index_line(line: str, *, task_id: str, filename: str) -> bool:
    match = _ACTIVE_LINE_RE.match(line)
    if match is None:
        return False
    return match.group("task_id") == task_id and match.group("filename") == filename


def _parse_frontmatter(body: str) -> dict[str, str]:
    lines = body.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        raise ValueError("Task body must start with YAML frontmatter")

    metadata: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return metadata
        key, separator, value = line.partition(":")
        if separator == "":
            raise ValueError(f"Malformed task frontmatter line: {line}")
        metadata[key.strip()] = value.strip()

    raise ValueError("Task body frontmatter must be closed with '---'")


def _require_frontmatter_value(metadata: dict[str, str], key: str, task_id: str) -> str:
    value = metadata.get(key)
    if not value:
        raise ValueError(f"Task {task_id} frontmatter must define '{key}'")
    return value


def _parse_boolean(raw_value: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"Expected boolean frontmatter value, got: {raw_value}")


def _parse_retry_policy(raw_value: str) -> str:
    normalized = raw_value.strip().lower()
    if normalized in {"next_run", "immediate"}:
        return normalized
    raise ValueError(f"Invalid retry_on_failure policy: {raw_value}")


def _validate_schedule(schedule: str) -> str:
    normalized = schedule.strip().lower()
    if normalized in {"daily", "daily:weekdays"}:
        return normalized
    if _RECURRING_WEEKLY_SCHEDULE_RE.fullmatch(normalized) is not None:
        return normalized
    monthly_match = _RECURRING_MONTHLY_SCHEDULE_RE.fullmatch(normalized)
    if monthly_match is not None:
        day_of_month = int(monthly_match.group(1))
        if 1 <= day_of_month <= 28:
            return normalized
    every_match = _RECURRING_EVERY_SCHEDULE_RE.fullmatch(normalized)
    if every_match is not None and int(every_match.group(1)) > 0:
        return normalized
    raise ValueError(f"Invalid recurring schedule: {schedule}")


def _parse_max_time_minutes(raw_value: str | None, *, task_id: str) -> int | None:
    if raw_value is None or raw_value == "":
        return None
    try:
        parsed_value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"Task {task_id} frontmatter has invalid 'max_time_minutes': {raw_value}") from exc
    if parsed_value <= 0:
        raise ValueError(f"Task {task_id} frontmatter has invalid 'max_time_minutes': {raw_value}")
    return parsed_value
