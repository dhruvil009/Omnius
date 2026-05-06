import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PYTHON = sys.executable


@unittest.skipIf(sys.version_info < (3, 11), "package requires Python >= 3.11")
class RunPipelineTests(unittest.TestCase):
    def test_run_command_executes_local_milestone_one_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = tmp_path / "repo"
            repo.mkdir()
            (repo / ".git").write_text("gitdir: /tmp/example\n", encoding="utf-8")

            self._write_config(home=home, repo=repo)
            self._write_local_task(home)
            fake_bin = self._write_fake_preflight_binaries(tmp_path)

            result = self._run_cli(home=home, fake_bin=fake_bin)

            self.assertEqual(result.returncode, 0, result.stderr)
            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)

            journal_dir = journals[0].parent
            self.assertTrue((journal_dir / "preflight.json").exists())
            self.assertTrue((journal_dir / "planner_prompt.md").exists())
            self.assertTrue((journal_dir / "planner_response.json").exists())
            self.assertTrue((journal_dir / "manifest.json").exists())
            self.assertTrue((journal_dir / "dispatch_log.json").exists())

            manifest = json.loads((journal_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["summary"], "1 task(s) planned from local queue")
            self.assertEqual(manifest["skipped"], [])
            self.assertEqual(
                manifest["tasks"],
                [
                    {
                        "id": "O00001",
                        "title": "Add sample",
                        "type": "implementation",
                        "repo_slug": "example",
                        "source_ref": "tasks/O00001_add_sample.md",
                        "filename": "O00001_add_sample.md",
                        "max_time_minutes": 120,
                        "complexity": "small",
                    }
                ],
            )

            dispatch_log = json.loads((journal_dir / "dispatch_log.json").read_text(encoding="utf-8"))
            self.assertEqual(dispatch_log["pipeline"]["status"], "completed")

    def test_run_command_exits_nonzero_without_traceback_when_config_has_no_repos(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            self._write_config(home=home, repo=None)

            result = self._run_cli(home=home)

            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("Config must define at least one repo", result.stderr)

            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            dispatch_log = json.loads(journals[0].read_text(encoding="utf-8"))
            self.assertEqual(dispatch_log["pipeline"]["status"], "failed")
            self.assertIn("ended_at", dispatch_log["pipeline"])

    def test_run_command_finalizes_dispatch_log_when_late_pipeline_step_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = tmp_path / "repo"
            repo.mkdir()
            (repo / ".git").write_text("gitdir: /tmp/example\n", encoding="utf-8")

            self._write_config(home=home, repo=repo)
            self._write_malformed_local_task(home)
            fake_bin = self._write_fake_preflight_binaries(tmp_path)

            result = self._run_cli(home=home, fake_bin=fake_bin)

            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("Malformed task entry", result.stderr)

            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            journal_dir = journals[0].parent
            dispatch_log = json.loads(journals[0].read_text(encoding="utf-8"))

            self.assertTrue((journal_dir / "preflight.json").exists())
            self.assertEqual(dispatch_log["pipeline"]["status"], "failed")
            self.assertIn("ended_at", dispatch_log["pipeline"])

    def _run_cli(self, *, home: Path, fake_bin: Optional[Path] = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["OMNIUS_HOME"] = str(home)
        env["PYTHONPATH"] = str(SRC)
        if fake_bin is not None:
            env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
        return subprocess.run(
            [PYTHON, "-m", "omnius", "run"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            env=env,
        )

    def _write_config(self, *, home: Path, repo: Optional[Path]) -> None:
        home.mkdir(parents=True, exist_ok=True)
        repos_block = ""
        if repo is not None:
            repos_block = textwrap.dedent(
                f"""

                [[repos]]
                slug = "example"
                path = "{repo}"
                branch = "main"
                role = "author"
                labels = ["omnius"]
                """
            )
        (home / "omnius.toml").write_text(
            textwrap.dedent(
                f"""
                [global]
                timezone = "America/Los_Angeles"
                pipeline_cron = "0 21 * * 0-4"
                pipeline_budget_minutes = 540
                default_task_budget_minutes = 120
                max_consecutive_failures = 3
                notification_backend = "none"

                [runner]
                default = "codex"

                [runners.codex]
                enabled = true

                [capabilities]
                brainstorm = "auto"
                review_diff = "auto"
                autonomous_testing = "auto"
                second_opinion = "auto"
                """
            ).strip()
            + repos_block
            + "\n",
            encoding="utf-8",
        )

    def _write_local_task(self, home: Path) -> None:
        tasks_dir = home / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        (home / "tasks.md").write_text(
            "## Format\n"
            "- <ID>: <Title> [file: <filename>.md]\n\n"
            "## Active\n"
            "- O00001: Add sample [file: O00001_add_sample.md]\n\n"
            "## Completed\n",
            encoding="utf-8",
        )
        (tasks_dir / "O00001_add_sample.md").write_text(
            "---\n"
            "title: Add sample\n"
            "repo: example\n"
            "---\n"
            "Task body\n",
            encoding="utf-8",
        )

    def _write_malformed_local_task(self, home: Path) -> None:
        tasks_dir = home / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        (home / "tasks.md").write_text(
            "## Format\n"
            "- <ID>: <Title> [file: <filename>.md]\n\n"
            "## Active\n"
            "- malformed entry\n\n"
            "## Completed\n",
            encoding="utf-8",
        )

    def _write_fake_preflight_binaries(self, tmp_path: Path) -> Path:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        self._write_executable(
            fake_bin / "gh",
            textwrap.dedent(
                """\
                #!/bin/sh
                if [ "$1" = "--version" ]; then
                    echo "gh version 2.61.0"
                    exit 0
                fi
                if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
                    echo "github.com"
                    exit 0
                fi
                echo "unexpected gh args: $@" >&2
                exit 1
                """
            ),
        )
        self._write_executable(
            fake_bin / "git",
            textwrap.dedent(
                """\
                #!/bin/sh
                if [ "$1" = "--version" ]; then
                    echo "git version 2.45.0"
                    exit 0
                fi
                echo "unexpected git args: $@" >&2
                exit 1
                """
            ),
        )
        return fake_bin

    def _write_executable(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
