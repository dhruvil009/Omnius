import tempfile
import textwrap
import unittest
from pathlib import Path

from omnius.tasks import load_local_task_entries, render_local_tasks_section
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
