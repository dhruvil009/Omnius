from __future__ import annotations

from datetime import date, datetime, timezone
import tempfile
import textwrap
import unittest
from pathlib import Path

from omnius.prefetch import collect_prefetch_snapshot
from omnius.workspace import bootstrap_workspace


class PrefetchSnapshotTests(unittest.TestCase):
    def test_collect_prefetch_snapshot_gathers_local_due_recurring_and_pending_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)
            (home / "tasks.md").write_text(
                "## Format\n"
                "- <ID>: <Title> [file: <filename>.md]\n\n"
                "## Active\n"
                "- O00001: Add sample [file: O00001_add_sample.md]\n\n"
                "## Completed\n",
                encoding="utf-8",
            )
            (home / "tasks" / "O00001_add_sample.md").write_text(
                "---\n"
                "title: Add sample\n"
                "repo: example\n"
                "---\n"
                "Local body\n",
                encoding="utf-8",
            )
            (home / "tasks" / "recurring" / "R00001_daily.md").write_text(
                textwrap.dedent(
                    """
                    ---
                    title: Daily cleanup
                    repo: example
                    schedule: daily
                    complexity: medium
                    max_time_minutes: 45
                    ---
                    Recurring body
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (home / "tasks" / "pending_approval" / "proposed_follow_up.md").write_text(
                "Needs review\n",
                encoding="utf-8",
            )

            snapshot = collect_prefetch_snapshot(home=home, today=date(2026, 5, 6))

        self.assertEqual([entry.task_id for entry in snapshot.local_task_entries], ["O00001"])
        self.assertEqual([entry.task_id for entry in snapshot.due_recurring_task_entries], ["R00001"])
        self.assertEqual(snapshot.pending_approval_filenames, ["proposed_follow_up.md"])
        self.assertIsNone(snapshot.recurring_state_suspect_path)
        self.assertIn("O00001", snapshot.local_tasks_section)
        self.assertIn("R00001", snapshot.recurring_tasks_section)
        self.assertIn("proposed_follow_up.md", snapshot.pending_approval_section)

    def test_collect_prefetch_snapshot_reports_quarantined_recurring_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)
            (home / "tasks" / "recurring" / "R00001_daily.md").write_text(
                textwrap.dedent(
                    """
                    ---
                    title: Daily cleanup
                    repo: example
                    schedule: daily
                    ---
                    Recurring body
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (home / "state" / "recurring_state.json").write_text("{not-json", encoding="utf-8")

            snapshot = collect_prefetch_snapshot(
                home=home,
                today=date(2026, 5, 6),
                recurring_state_quarantined_at=datetime(2026, 5, 6, 7, 8, 9, tzinfo=timezone.utc),
            )

        self.assertEqual([entry.task_id for entry in snapshot.due_recurring_task_entries], ["R00001"])
        self.assertEqual(
            snapshot.recurring_state_suspect_path.name if snapshot.recurring_state_suspect_path else None,
            "recurring_state.json.suspect.20260506T070809Z",
        )
        self.assertIn("R00001", snapshot.recurring_tasks_section)
        self.assertIn("recurring_state.json.suspect.20260506T070809Z", snapshot.recurring_tasks_section)
