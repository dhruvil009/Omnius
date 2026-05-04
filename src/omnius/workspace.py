from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


TASKS_TEMPLATE = (
    "## Format\n"
    "- <ID>: <Title> [file: <filename>.md]\n\n"
    "## Active\n\n"
    "## Completed\n"
)


@dataclass(frozen=True)
class WorkspacePaths:
    home: Path
    tasks_dir: Path
    tasks_recurring_dir: Path
    tasks_completed_dir: Path
    tasks_pending_approval_dir: Path
    journal_dir: Path
    state_dir: Path
    logs_dir: Path
    inbox_dir: Path
    prompts_dir: Path
    schemas_dir: Path


def bootstrap_workspace(home: Path) -> WorkspacePaths:
    paths = WorkspacePaths(
        home=home,
        tasks_dir=home / "tasks",
        tasks_recurring_dir=home / "tasks" / "recurring",
        tasks_completed_dir=home / "tasks" / "completed",
        tasks_pending_approval_dir=home / "tasks" / "pending_approval",
        journal_dir=home / "journal",
        state_dir=home / "state",
        logs_dir=home / "logs",
        inbox_dir=home / "inbox",
        prompts_dir=home / "prompts",
        schemas_dir=home / "schemas",
    )

    for directory in (
        paths.tasks_dir,
        paths.tasks_recurring_dir,
        paths.tasks_completed_dir,
        paths.tasks_pending_approval_dir,
        paths.journal_dir,
        paths.state_dir,
        paths.logs_dir,
        paths.inbox_dir,
        paths.prompts_dir,
        paths.schemas_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    tasks_index = home / "tasks.md"
    if not tasks_index.exists():
        tasks_index.write_text(TASKS_TEMPLATE)

    recurring_state = paths.state_dir / "recurring_state.json"
    if not recurring_state.exists():
        recurring_state.write_text(json.dumps({}))

    return paths
