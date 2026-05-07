from __future__ import annotations

from datetime import datetime, timezone, date
import tempfile
import unittest
from pathlib import Path

from omnius.recurring import (
    filter_due_recurring_task_entries,
    load_recurring_state,
    save_recurring_state,
)
from omnius.tasks import RecurringTaskEntry
from omnius.workspace import bootstrap_workspace


class RecurringStateTests(unittest.TestCase):
    def test_save_and_load_recurring_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)

            save_recurring_state(
                home,
                {
                    "R00001": {
                        "last_attempted": "2026-05-05",
                        "last_succeeded": "2026-05-05",
                        "last_status": "SUCCESS",
                        "consecutive_failures": 0,
                        "quarantined_until": None,
                    }
                },
            )

            state = load_recurring_state(home)

        self.assertEqual(
            state,
            {
                "R00001": {
                    "last_attempted": "2026-05-05",
                    "last_succeeded": "2026-05-05",
                    "last_status": "SUCCESS",
                    "consecutive_failures": 0,
                    "quarantined_until": None,
                }
            },
        )

    def test_load_recurring_state_quarantines_corrupt_state_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)
            state_path = home / "state" / "recurring_state.json"
            state_path.write_text("{not-json", encoding="utf-8")

            state = load_recurring_state(
                home,
                quarantine_corrupt=True,
                quarantined_at=datetime(2026, 5, 6, 7, 8, 9, tzinfo=timezone.utc),
            )

            suspect_files = sorted((home / "state").glob("recurring_state.json.suspect.*"))
            replacement_state = state_path.read_text(encoding="utf-8").strip()
            suspect_name = suspect_files[0].name
            suspect_contents = suspect_files[0].read_text(encoding="utf-8")

        self.assertEqual(state, {})
        self.assertEqual(replacement_state, "{}")
        self.assertEqual(len(suspect_files), 1)
        self.assertEqual(suspect_name, "recurring_state.json.suspect.20260506T070809Z")
        self.assertEqual(suspect_contents, "{not-json")


class RecurringDueCheckTests(unittest.TestCase):
    def test_filter_due_recurring_task_entries_uses_schedule_and_state(self) -> None:
        entries = [
            RecurringTaskEntry(
                task_id="R00001",
                filename="R00001_daily.md",
                title="Daily task",
                repo_slug="example",
                schedule="daily",
                task_type="implementation",
                complexity="small",
                max_time_minutes=None,
                retry_on_failure="next_run",
                only_if_last_succeeded=False,
                body="body\n",
            ),
            RecurringTaskEntry(
                task_id="R00002",
                filename="R00002_weekly.md",
                title="Weekly task",
                repo_slug="example",
                schedule="weekly:mon",
                task_type="implementation",
                complexity="small",
                max_time_minutes=None,
                retry_on_failure="next_run",
                only_if_last_succeeded=False,
                body="body\n",
            ),
            RecurringTaskEntry(
                task_id="R00003",
                filename="R00003_retry.md",
                title="Retry task",
                repo_slug="example",
                schedule="daily:weekdays",
                task_type="implementation",
                complexity="small",
                max_time_minutes=None,
                retry_on_failure="immediate",
                only_if_last_succeeded=False,
                body="body\n",
            ),
            RecurringTaskEntry(
                task_id="R00004",
                filename="R00004_requires_success.md",
                title="Success-gated task",
                repo_slug="example",
                schedule="daily",
                task_type="implementation",
                complexity="small",
                max_time_minutes=None,
                retry_on_failure="next_run",
                only_if_last_succeeded=True,
                body="body\n",
            ),
        ]
        state = {
            "R00001": {
                "last_attempted": "2026-05-06",
                "last_succeeded": "2026-05-06",
                "last_status": "SUCCESS",
                "consecutive_failures": 0,
                "quarantined_until": None,
            },
            "R00003": {
                "last_attempted": "2026-05-06",
                "last_succeeded": None,
                "last_status": "FAILURE",
                "consecutive_failures": 1,
                "quarantined_until": None,
            },
            "R00004": {
                "last_attempted": "2026-05-05",
                "last_succeeded": None,
                "last_status": "FAILURE",
                "consecutive_failures": 1,
                "quarantined_until": None,
            },
        }

        due_entries = filter_due_recurring_task_entries(
            entries,
            state,
            today=date(2026, 5, 6),
        )

        self.assertEqual([entry.task_id for entry in due_entries], ["R00003"])

    def test_filter_due_recurring_task_entries_supports_every_n_days(self) -> None:
        entry = RecurringTaskEntry(
            task_id="R00005",
            filename="R00005_every_3_days.md",
            title="Every three days",
            repo_slug="example",
            schedule="every:3d",
            task_type="implementation",
            complexity="small",
            max_time_minutes=None,
            retry_on_failure="next_run",
            only_if_last_succeeded=False,
            body="body\n",
        )

        due_entries = filter_due_recurring_task_entries(
            [entry],
            {
                "R00005": {
                    "last_attempted": "2026-05-03",
                    "last_succeeded": "2026-05-03",
                    "last_status": "SUCCESS",
                    "consecutive_failures": 0,
                    "quarantined_until": None,
                }
            },
            today=date(2026, 5, 6),
        )

        self.assertEqual([due_entry.task_id for due_entry in due_entries], ["R00005"])

    def test_filter_due_recurring_task_entries_honors_next_run_and_quarantine(self) -> None:
        entries = [
            RecurringTaskEntry(
                task_id="R00006",
                filename="R00006_next_run.md",
                title="Next run retry",
                repo_slug="example",
                schedule="daily",
                task_type="implementation",
                complexity="small",
                max_time_minutes=None,
                retry_on_failure="next_run",
                only_if_last_succeeded=False,
                body="body\n",
            ),
            RecurringTaskEntry(
                task_id="R00007",
                filename="R00007_quarantined.md",
                title="Quarantined task",
                repo_slug="example",
                schedule="daily",
                task_type="implementation",
                complexity="small",
                max_time_minutes=None,
                retry_on_failure="immediate",
                only_if_last_succeeded=False,
                body="body\n",
            ),
        ]

        due_entries = filter_due_recurring_task_entries(
            entries,
            {
                "R00006": {
                    "last_attempted": "2026-05-06",
                    "last_succeeded": None,
                    "last_status": "FAILURE",
                    "consecutive_failures": 2,
                    "quarantined_until": None,
                },
                "R00007": {
                    "last_attempted": "2026-05-05",
                    "last_succeeded": None,
                    "last_status": "FAILURE",
                    "consecutive_failures": 3,
                    "quarantined_until": "2026-05-07",
                },
            },
            today=date(2026, 5, 6),
        )

        self.assertEqual([], [due_entry.task_id for due_entry in due_entries])

    def test_filter_due_recurring_task_entries_allows_first_run_when_success_gated(self) -> None:
        entry = RecurringTaskEntry(
            task_id="R00008",
            filename="R00008_first_run.md",
            title="First run",
            repo_slug="example",
            schedule="daily",
            task_type="implementation",
            complexity="small",
            max_time_minutes=None,
            retry_on_failure="next_run",
            only_if_last_succeeded=True,
            body="body\n",
        )

        due_entries = filter_due_recurring_task_entries(
            [entry],
            {},
            today=date(2026, 5, 6),
        )

        self.assertEqual(["R00008"], [due_entry.task_id for due_entry in due_entries])
