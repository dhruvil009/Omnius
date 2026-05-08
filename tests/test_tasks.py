import tempfile
import textwrap
import unittest
from pathlib import Path

from omnius.tasks import (
    RecurringTaskEntry,
    archive_local_task_success,
    load_local_task_entries,
    load_recurring_task_entries,
    render_local_tasks_section,
)
from omnius.workspace import bootstrap_workspace


class TaskParsingTests(unittest.TestCase):
    def test_load_local_task_entries_reads_only_active_index_entries_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)
            (home / "tasks.md").write_text(
                textwrap.dedent(
                    """
                    ## Format
                    - <ID>: <Title> [file: <filename>.md]

                    ## Active
                    - O00001: Add sample [file: O00001_add_sample.md]
                    - O00002: Fix parser [file: O00002_fix_parser.md]

                    ## Completed
                    - O99999: Done already [file: O99999_done.md]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (home / "tasks" / "O00001_add_sample.md").write_text(
                textwrap.dedent(
                    """
                    ---
                    title: Add sample
                    repo: example
                    ---
                    Body 1
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (home / "tasks" / "O00002_fix_parser.md").write_text(
                textwrap.dedent(
                    """
                    ---
                    title: Fix parser
                    repo: example
                    ---
                    Body 2
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (home / "tasks" / "O99999_done.md").write_text("completed\n", encoding="utf-8")

            entries = load_local_task_entries(home)

        self.assertEqual([entry.task_id for entry in entries], ["O00001", "O00002"])
        self.assertEqual(entries[0].filename, "O00001_add_sample.md")
        self.assertIn("Body 1", entries[0].body)
        self.assertIn("Body 2", entries[1].body)
        self.assertIsNone(entries[0].agent)
        self.assertIsNone(entries[1].agent)

    def test_load_local_task_entries_parses_optional_agent_override(self) -> None:
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
                textwrap.dedent(
                    """
                    ---
                    title: Add sample
                    repo: example
                    agent: claude
                    ---
                    Body 1
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            [entry] = load_local_task_entries(home)

        self.assertEqual(entry.agent, "claude")

    def test_load_local_task_entries_rejects_invalid_agent_override(self) -> None:
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
                textwrap.dedent(
                    """
                    ---
                    title: Add sample
                    repo: example
                    agent: bogus
                    ---
                    Body 1
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "agent"):
                load_local_task_entries(home)

    def test_render_local_tasks_section_formats_loaded_entries(self) -> None:
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
                "Task body\n",
                encoding="utf-8",
            )

            rendered = render_local_tasks_section(load_local_task_entries(home))

        self.assertEqual(
            rendered,
            "--- Task ID: O00001 | File: O00001_add_sample.md ---\nTask body\n",
        )

    def test_render_local_tasks_section_returns_none_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)

            rendered = render_local_tasks_section(load_local_task_entries(home))

        self.assertEqual(rendered, "<none>")

    def test_load_local_task_entries_rejects_malformed_non_empty_active_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)
            (home / "tasks.md").write_text(
                "## Format\n"
                "- <ID>: <Title> [file: <filename>.md]\n\n"
                "## Active\n"
                "not a task entry\n\n"
                "## Completed\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Malformed task entry"):
                load_local_task_entries(home)

    def test_archive_local_task_success_moves_file_and_updates_index(self) -> None:
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
            (home / "tasks" / "O00001_add_sample.md").write_text("Task body\n", encoding="utf-8")

            archive_local_task_success(
                home=home,
                task_id="O00001",
                filename="O00001_add_sample.md",
                run_date="2026-05-05",
            )

            updated_index = (home / "tasks.md").read_text(encoding="utf-8")
            original_task_exists = (home / "tasks" / "O00001_add_sample.md").exists()
            archived_task_exists = (home / "tasks" / "completed" / "O00001_add_sample.md").exists()

        self.assertFalse(original_task_exists)
        self.assertTrue(archived_task_exists)
        self.assertNotIn("- O00001: Add sample [file: O00001_add_sample.md]", updated_index)
        self.assertIn("- 2026-05-05: O00001: Add sample [file: O00001_add_sample.md]", updated_index)


class RecurringTaskParsingTests(unittest.TestCase):
    def test_load_recurring_task_entries_reads_recurring_markdown_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)
            (home / "tasks" / "recurring" / "R00001_review_backlog.md").write_text(
                textwrap.dedent(
                    """
                    ---
                    title: Review backlog
                    repo: example
                    schedule: weekly:wed
                    type: maintenance
                    complexity: medium
                    max_time_minutes: 45
                    retry_on_failure: immediate
                    only_if_last_succeeded: true
                    ---
                    Check stale issues and triage them.
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (home / "tasks" / "recurring" / "notes.md").write_text("ignore me\n", encoding="utf-8")

            entries = load_recurring_task_entries(home)

        self.assertEqual(
            entries,
            [
                RecurringTaskEntry(
                    task_id="R00001",
                    filename="R00001_review_backlog.md",
                    title="Review backlog",
                    repo_slug="example",
                    schedule="weekly:wed",
                    task_type="maintenance",
                    complexity="medium",
                    max_time_minutes=45,
                    retry_on_failure="immediate",
                    only_if_last_succeeded=True,
                    body=textwrap.dedent(
                        """
                        ---
                        title: Review backlog
                        repo: example
                        schedule: weekly:wed
                        type: maintenance
                        complexity: medium
                        max_time_minutes: 45
                        retry_on_failure: immediate
                        only_if_last_succeeded: true
                        ---
                        Check stale issues and triage them.
                        """
                    ).strip()
                    + "\n",
                )
            ],
        )

    def test_load_recurring_task_entries_applies_defaults_for_optional_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)
            (home / "tasks" / "recurring" / "R00002_cleanup.md").write_text(
                textwrap.dedent(
                    """
                    ---
                    title: Cleanup
                    repo: example
                    schedule: daily:weekdays
                    ---
                    Light cleanup.
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            [entry] = load_recurring_task_entries(home)

        self.assertEqual(entry.task_id, "R00002")
        self.assertEqual(entry.task_type, "implementation")
        self.assertEqual(entry.complexity, "small")
        self.assertIsNone(entry.max_time_minutes)
        self.assertEqual(entry.retry_on_failure, "next_run")
        self.assertFalse(entry.only_if_last_succeeded)

    def test_load_recurring_task_entries_requires_schedule_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)
            (home / "tasks" / "recurring" / "R00003_missing_schedule.md").write_text(
                textwrap.dedent(
                    """
                    ---
                    title: Missing schedule
                    repo: example
                    ---
                    Broken task.
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "schedule"):
                load_recurring_task_entries(home)

    def test_load_recurring_task_entries_rejects_unknown_retry_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)
            (home / "tasks" / "recurring" / "R00004_invalid_retry.md").write_text(
                textwrap.dedent(
                    """
                    ---
                    title: Invalid retry
                    repo: example
                    schedule: daily
                    retry_on_failure: true
                    ---
                    Broken task.
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "retry_on_failure"):
                load_recurring_task_entries(home)

    def test_load_recurring_task_entries_rejects_non_spec_schedule_forms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)
            (home / "tasks" / "recurring" / "R00005_invalid_schedule.md").write_text(
                textwrap.dedent(
                    """
                    ---
                    title: Invalid schedule
                    repo: example
                    schedule: weekdays
                    ---
                    Broken task.
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "schedule"):
                load_recurring_task_entries(home)

    def test_load_recurring_task_entries_rejects_non_positive_max_time_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)
            (home / "tasks" / "recurring" / "R00006_invalid_budget.md").write_text(
                textwrap.dedent(
                    """
                    ---
                    title: Invalid budget
                    repo: example
                    schedule: daily
                    max_time_minutes: 0
                    ---
                    Broken task.
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, r"Task R00006.*max_time_minutes"):
                load_recurring_task_entries(home)

    def test_load_recurring_task_entries_rejects_malformed_max_time_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            bootstrap_workspace(home)
            (home / "tasks" / "recurring" / "R00007_invalid_budget.md").write_text(
                textwrap.dedent(
                    """
                    ---
                    title: Invalid budget
                    repo: example
                    schedule: daily
                    max_time_minutes: forty-five
                    ---
                    Broken task.
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, r"Task R00007.*max_time_minutes"):
                load_recurring_task_entries(home)
