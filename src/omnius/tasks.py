from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


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

    for raw_line in active_section.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _ACTIVE_LINE_RE.match(line)
        if match is None:
            continue

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
