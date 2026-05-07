from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from omnius.recurring import filter_due_recurring_task_entries, load_recurring_state
from omnius.tasks import (
    LocalTaskEntry,
    RecurringTaskEntry,
    load_local_task_entries,
    load_recurring_task_entries,
    render_local_tasks_section,
)


@dataclass(frozen=True)
class PrefetchSnapshot:
    local_task_entries: list[LocalTaskEntry]
    due_recurring_task_entries: list[RecurringTaskEntry]
    pending_approval_filenames: list[str]
    recurring_state_suspect_path: Path | None
    local_tasks_section: str
    recurring_tasks_section: str
    pending_approval_section: str


def collect_prefetch_snapshot(
    home: Path,
    *,
    today: date,
    recurring_state_quarantined_at: datetime | None = None,
) -> PrefetchSnapshot:
    local_task_entries = load_local_task_entries(home)
    recurring_entries = load_recurring_task_entries(home)
    recurring_state_suspect_path, recurring_state = _load_recurring_state_with_suspect_tracking(
        home,
        quarantined_at=recurring_state_quarantined_at,
    )
    due_recurring_task_entries = filter_due_recurring_task_entries(
        recurring_entries,
        recurring_state,
        today=today,
    )
    pending_approval_filenames = sorted(path.name for path in (home / "tasks" / "pending_approval").glob("*.md"))
    return PrefetchSnapshot(
        local_task_entries=local_task_entries,
        due_recurring_task_entries=due_recurring_task_entries,
        pending_approval_filenames=pending_approval_filenames,
        recurring_state_suspect_path=recurring_state_suspect_path,
        local_tasks_section=render_local_tasks_section(local_task_entries),
        recurring_tasks_section=_render_recurring_tasks_section(
            due_recurring_task_entries,
            suspect_path=recurring_state_suspect_path,
        ),
        pending_approval_section=_render_pending_approval_section(pending_approval_filenames),
    )


def _load_recurring_state_with_suspect_tracking(
    home: Path,
    *,
    quarantined_at: datetime | None,
) -> tuple[Path | None, dict[str, dict[str, object]]]:
    state_dir = home / "state"
    before = {path.name: path for path in state_dir.glob("recurring_state.json.suspect.*")}
    state = load_recurring_state(
        home,
        quarantine_corrupt=True,
        quarantined_at=quarantined_at,
    )
    after = {path.name: path for path in state_dir.glob("recurring_state.json.suspect.*")}
    new_names = sorted(set(after) - set(before))
    suspect_path = after[new_names[-1]] if new_names else None
    return suspect_path, state


def _render_recurring_tasks_section(
    entries: list[RecurringTaskEntry],
    *,
    suspect_path: Path | None,
) -> str:
    sections: list[str] = []
    if suspect_path is not None:
        sections.append(
            "WARNING: recurring state was quarantined due to corrupt JSON; using empty state.\n"
            f"Suspect file: {suspect_path.name}"
        )
    if not entries:
        sections.append("<none>")
        return "\n\n".join(sections)
    sections.append(
        "\n\n".join(
        f"--- Task ID: {entry.task_id} | File: {entry.filename} | Schedule: {entry.schedule} ---\n{entry.body}"
        for entry in entries
        )
    )
    return "\n\n".join(sections)


def _render_pending_approval_section(filenames: list[str]) -> str:
    if not filenames:
        return "<none>"
    count_label = "file" if len(filenames) == 1 else "files"
    listing = "\n".join(f"- {filename}" for filename in filenames)
    return f"{len(filenames)} pending approval {count_label}\n{listing}"
