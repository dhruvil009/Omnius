from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import shutil

from omnius.config import SUPPORTED_RUNNERS


SUPPORTED_TASK_TYPES = ("implementation", "design", "research", "comment_resolution")

_ACTIVE_LINE_RE = re.compile(r"^- (?P<task_id>O\d{5}): (?P<title>.*?) \[file: (?P<filename>[^\]]+)\]$")
_COMPLETED_LINE_RE = re.compile(
    r"^- (?:(?P<completed_on>\d{4}-\d{2}-\d{2}): )?"
    r"(?P<task_id>O\d{5}): (?P<title>.*?) \[file: (?P<filename>[^\]]+)\]$"
)
_LOCAL_TASK_ID_RE = re.compile(r"\bO(?P<number>\d{5})\b")
_LOCAL_TASK_FILENAME_RE = re.compile(r"^(?P<task_id>O\d{5}).*\.md$")
_RECURRING_FILENAME_RE = re.compile(r"^(?P<task_id>R\d{5}).*\.md$")
_RECURRING_WEEKLY_SCHEDULE_RE = re.compile(r"^weekly:(mon|tue|wed|thu|fri|sat|sun)$")
_RECURRING_MONTHLY_SCHEDULE_RE = re.compile(r"^monthly:(\d{1,2})$")
_RECURRING_EVERY_SCHEDULE_RE = re.compile(r"^every:(\d+)d$")


@dataclass(frozen=True)
class LocalTaskEntry:
    task_id: str
    filename: str
    body: str
    agent: str | None = None


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


@dataclass(frozen=True)
class TaskCommandEntry:
    task_id: str
    title: str
    filename: str
    path: Path
    status: str
    body: str
    metadata: dict[str, object]

    def as_payload(self) -> dict[str, object]:
        return {
            "id": self.task_id,
            "title": self.title,
            "filename": self.filename,
            "path": str(self.path),
            "status": self.status,
            "body": self.body,
            "metadata": dict(self.metadata),
        }


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
        metadata = _parse_frontmatter(body) if body.startswith("---") else {}
        entries.append(
            LocalTaskEntry(
                task_id=match.group("task_id"),
                filename=filename,
                body=body,
                agent=_parse_agent(metadata.get("agent"), task_id=match.group("task_id")),
            )
        )

    return entries


def list_active_task_entries(home: Path) -> list[TaskCommandEntry]:
    index_text = (home / "tasks.md").read_text(encoding="utf-8")
    active_section = _extract_active_section(index_text)
    entries: list[TaskCommandEntry] = []

    for line_number, raw_line in enumerate(active_section.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        match = _ACTIVE_LINE_RE.match(line)
        if match is None:
            raise ValueError(f"Malformed task entry in ## Active at line {line_number}: {line}")
        filename = match.group("filename")
        path = home / "tasks" / filename
        body = path.read_text(encoding="utf-8")
        metadata = _parse_frontmatter(body) if body.startswith("---") else {}
        parsed_agent = _parse_agent(metadata.get("agent"), task_id=match.group("task_id"))
        if parsed_agent is not None:
            metadata = {**metadata, "agent": parsed_agent}
        title = metadata.get("title") or match.group("title")
        entries.append(
            TaskCommandEntry(
                task_id=match.group("task_id"),
                title=title,
                filename=filename,
                path=path,
                status="active",
                body=body,
                metadata=dict(metadata),
            )
        )

    return entries


def list_pending_task_entries(home: Path) -> list[TaskCommandEntry]:
    pending_dir = home / "tasks" / "pending_approval"
    entries: list[TaskCommandEntry] = []

    for path in sorted(pending_dir.glob("*.md")):
        body = path.read_text(encoding="utf-8")
        metadata = _parse_frontmatter(body) if body.startswith("---") else {}
        task_id = _task_id_from_filename(path.name)
        title = metadata.get("title") or path.stem
        entries.append(
            TaskCommandEntry(
                task_id=task_id,
                title=title,
                filename=path.name,
                path=path,
                status="pending",
                body=body,
                metadata=dict(metadata),
            )
        )

    return entries


def list_recurring_command_entries(home: Path) -> list[TaskCommandEntry]:
    entries: list[TaskCommandEntry] = []

    for entry in load_recurring_task_entries(home):
        metadata: dict[str, object] = {
            "title": entry.title,
            "repo": entry.repo_slug,
            "schedule": entry.schedule,
            "type": entry.task_type,
            "complexity": entry.complexity,
            "retry_on_failure": entry.retry_on_failure,
            "only_if_last_succeeded": entry.only_if_last_succeeded,
        }
        if entry.max_time_minutes is not None:
            metadata["max_time_minutes"] = entry.max_time_minutes
        entries.append(
            TaskCommandEntry(
                task_id=entry.task_id,
                title=entry.title,
                filename=entry.filename,
                path=home / "tasks" / "recurring" / entry.filename,
                status="recurring",
                body=entry.body,
                metadata=metadata,
            )
        )

    return entries


def list_completed_task_entries(home: Path) -> list[TaskCommandEntry]:
    index_text = (home / "tasks.md").read_text(encoding="utf-8")
    completed_section = _extract_completed_section(index_text)
    entries: list[TaskCommandEntry] = []

    for line_number, raw_line in enumerate(completed_section.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        match = _COMPLETED_LINE_RE.match(line)
        if match is None:
            raise ValueError(f"Malformed task entry in ## Completed at line {line_number}: {line}")
        filename = match.group("filename")
        path = home / "tasks" / "completed" / filename
        body = path.read_text(encoding="utf-8") if path.exists() else ""
        metadata = _parse_frontmatter(body) if body.startswith("---") else {}
        completed_on = match.group("completed_on")
        if completed_on is not None:
            metadata = {**metadata, "completed_on": completed_on}
        title = metadata.get("title") or match.group("title")
        entries.append(
            TaskCommandEntry(
                task_id=match.group("task_id"),
                title=title,
                filename=filename,
                path=path,
                status="completed",
                body=body,
                metadata=dict(metadata),
            )
        )

    return entries


def show_task_entry(home: Path, task_id: str) -> TaskCommandEntry:
    for loader in (
        list_active_task_entries,
        list_pending_task_entries,
        list_recurring_command_entries,
        list_completed_task_entries,
    ):
        for entry in loader(home):
            if entry.task_id == task_id:
                return entry
    raise ValueError(f"Task not found: {task_id}")


def allocate_next_local_task_id(home: Path) -> str:
    used_numbers: set[int] = set()
    index_path = home / "tasks.md"
    if index_path.exists():
        for match in _LOCAL_TASK_ID_RE.finditer(index_path.read_text(encoding="utf-8")):
            used_numbers.add(int(match.group("number")))

    for directory in (home / "tasks", home / "tasks" / "pending_approval", home / "tasks" / "completed"):
        if not directory.exists():
            continue
        for path in directory.glob("O*.md"):
            match = _LOCAL_TASK_FILENAME_RE.match(path.name)
            if match is not None:
                used_numbers.add(int(match.group("task_id")[1:]))

    next_number = max(used_numbers, default=0) + 1
    if next_number > 99999:
        raise ValueError("No local task IDs remain")
    return f"O{next_number:05d}"


def add_local_task(
    *,
    home: Path,
    title: str,
    repo_slug: str,
    body: str,
    agent: str | None = None,
    task_type: str = "implementation",
    max_time_minutes: int | None = None,
) -> TaskCommandEntry:
    title = title.strip()
    repo_slug = repo_slug.strip()
    if not title:
        raise ValueError("Task title is required")
    if not repo_slug:
        raise ValueError("Task repo is required")
    if task_type not in SUPPORTED_TASK_TYPES:
        raise ValueError(f"Invalid task type: {task_type}")
    if max_time_minutes is not None and max_time_minutes <= 0:
        raise ValueError("Task max_time_minutes must be positive")
    normalized_agent = _parse_agent(agent, task_id="new task")

    task_id = allocate_next_local_task_id(home)
    filename = f"{task_id}_{_slugify_title(title)}.md"
    path = home / "tasks" / filename
    if path.exists():
        raise FileExistsError(path)

    metadata: dict[str, object] = {
        "title": title,
        "repo": repo_slug,
    }
    if normalized_agent is not None:
        metadata["agent"] = normalized_agent
    metadata["type"] = task_type
    if max_time_minutes is not None:
        metadata["max_time_minutes"] = str(max_time_minutes)

    task_body = _build_local_task_body(
        title=title,
        repo_slug=repo_slug,
        agent=normalized_agent,
        task_type=task_type,
        max_time_minutes=max_time_minutes,
        body=body,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(task_body, encoding="utf-8")
    _append_active_index_line(
        home=home,
        line=f"- {task_id}: {title} [file: {filename}]",
    )

    return TaskCommandEntry(
        task_id=task_id,
        title=title,
        filename=filename,
        path=path,
        status="active",
        body=task_body,
        metadata=metadata,
    )


def complete_local_task(*, home: Path, task_id: str, run_date: str | None = None) -> TaskCommandEntry:
    active_entry = show_task_entry(home, task_id)
    if active_entry.status != "active":
        raise ValueError(f"Task is not active: {task_id}")
    archive_local_task_success(
        home=home,
        task_id=active_entry.task_id,
        filename=active_entry.filename,
        run_date=run_date or date.today().isoformat(),
    )
    return show_task_entry(home, task_id)


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


def _extract_completed_section(index_text: str) -> str:
    try:
        _, completed_section = index_text.split("## Completed", 1)
    except ValueError as exc:
        raise ValueError("tasks.md must contain a '## Completed' section") from exc
    return completed_section


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


def _append_active_index_line(*, home: Path, line: str) -> None:
    index_path = home / "tasks.md"
    index_text = index_path.read_text(encoding="utf-8")
    try:
        before_active, active_and_after = index_text.split("## Active", 1)
        active_section, after_completed = active_and_after.split("## Completed", 1)
    except ValueError as exc:
        raise ValueError("tasks.md must contain both '## Active' and '## Completed' sections") from exc

    active_body = active_section.strip("\n")
    if active_body.strip():
        active_body = f"{active_body}\n{line}"
    else:
        active_body = line
    completed_body = after_completed.strip("\n")
    new_index_text = (
        f"{before_active.rstrip()}\n\n"
        f"## Active\n"
        f"{active_body}\n\n"
        f"## Completed\n"
    )
    if completed_body:
        new_index_text = f"{new_index_text}{completed_body}\n"
    index_path.write_text(new_index_text, encoding="utf-8")


def _task_id_from_filename(filename: str) -> str:
    local_match = _LOCAL_TASK_FILENAME_RE.match(filename)
    if local_match is not None:
        return local_match.group("task_id")
    recurring_match = _RECURRING_FILENAME_RE.match(filename)
    if recurring_match is not None:
        return recurring_match.group("task_id")
    return Path(filename).stem


def _slugify_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    if not slug:
        return "task"
    return slug[:48].rstrip("_") or "task"


def _build_local_task_body(
    *,
    title: str,
    repo_slug: str,
    agent: str | None,
    task_type: str,
    max_time_minutes: int | None,
    body: str,
) -> str:
    lines = [
        "---",
        f"title: {title}",
        f"repo: {repo_slug}",
    ]
    if agent is not None:
        lines.append(f"agent: {agent}")
    lines.append(f"type: {task_type}")
    if max_time_minutes is not None:
        lines.append(f"max_time_minutes: {max_time_minutes}")
    lines.extend(["---", body.rstrip("\n"), ""])
    return "\n".join(lines)


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


def _parse_agent(raw_value: str | None, *, task_id: str) -> str | None:
    if raw_value is None or raw_value == "":
        return None
    normalized = raw_value.strip().lower()
    if normalized in SUPPORTED_RUNNERS:
        return normalized
    raise ValueError(f"Task {task_id} frontmatter has invalid 'agent': {raw_value}")


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
