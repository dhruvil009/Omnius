from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil


_ACTIVE_LINE_RE = re.compile(r"^- (?P<task_id>O\d{5}): .* \[file: (?P<filename>[^\]]+)\]$")


@dataclass(frozen=True)
class LocalTaskEntry:
    task_id: str
    filename: str
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
    source_path = home / "tasks" / filename
    destination_path = home / "tasks" / "completed" / filename
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

    completed_body = after_completed.rstrip("\n")
    archived_completed_line = f"- {run_date}: {archived_line.removeprefix('- ')}"
    if completed_body.strip():
        completed_body = f"{completed_body}\n{archived_completed_line}"
    else:
        completed_body = archived_completed_line

    active_body = "\n".join(remaining_active_lines).strip("\n")
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
