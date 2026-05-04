import tempfile
import unittest
from pathlib import Path

from omnius.workspace import bootstrap_workspace


class WorkspaceBootstrapTests(unittest.TestCase):
    def test_bootstrap_workspace_creates_expected_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"

            paths = bootstrap_workspace(home)

            self.assertTrue(paths.tasks_dir.exists())
            self.assertTrue(paths.tasks_recurring_dir.exists())
            self.assertTrue(paths.tasks_completed_dir.exists())
            self.assertTrue(paths.tasks_pending_approval_dir.exists())
            self.assertTrue(paths.journal_dir.exists())
            self.assertTrue(paths.state_dir.exists())
            self.assertTrue(paths.logs_dir.exists())
            self.assertTrue(paths.inbox_dir.exists())
            self.assertTrue(paths.prompts_dir.exists())
            self.assertTrue(paths.schemas_dir.exists())
            self.assertEqual(
                (home / "tasks.md").read_text(),
                "## Format\n"
                "- <ID>: <Title> [file: <filename>.md]\n\n"
                "## Active\n\n"
                "## Completed\n",
            )
            self.assertEqual((home / "state" / "recurring_state.json").read_text().strip(), "{}")

    def test_bootstrap_workspace_preserves_existing_seed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omnius"
            home.mkdir(parents=True)
            (home / "state").mkdir()
            (home / "tasks.md").write_text("sentinel tasks\n")
            (home / "state" / "recurring_state.json").write_text('{"sentinel": true}\n')

            bootstrap_workspace(home)

            self.assertEqual((home / "tasks.md").read_text(), "sentinel tasks\n")
            self.assertEqual(
                (home / "state" / "recurring_state.json").read_text(),
                '{"sentinel": true}\n',
            )
