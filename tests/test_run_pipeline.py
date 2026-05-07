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
    def test_run_command_executes_local_task_to_completion_and_archives_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_local_task(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={"OMNIUS_CODEX_BIN": str(fake_codex)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)

            journal_dir = journals[0].parent
            self.assertTrue((journal_dir / "preflight.json").exists())
            self.assertTrue((journal_dir / "planner_prompt.md").exists())
            self.assertTrue((journal_dir / "planner_response.json").exists())
            self.assertTrue((journal_dir / "manifest.json").exists())
            self.assertTrue((journal_dir / "dispatch_log.json").exists())
            self.assertTrue((journal_dir / "O00001_prompt.md").exists())
            self.assertTrue((journal_dir / "O00001_stdout.json").exists())
            self.assertTrue((journal_dir / "O00001_stderr.log").exists())

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
            self.assertEqual(dispatch_log["tasks"]["O00001"]["status"], "SUCCESS")
            self.assertEqual(dispatch_log["tasks"]["O00001"]["summary"], "done")
            self.assertTrue((home / "tasks" / "completed" / "O00001_add_sample.md").exists())
            self.assertFalse((home / "tasks" / "O00001_add_sample.md").exists())
            self.assertFalse((repo / ".omnius" / "worktrees" / journal_dir.parent.name / "O00001").exists())

    def test_run_command_populates_prompt_and_manifest_with_due_recurring_and_pending_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_local_task(home)
            self._write_recurring_task(home)
            self._write_pending_approval_task(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={"OMNIUS_CODEX_BIN": str(fake_codex)},
            )

            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)

            journal_dir = journals[0].parent
            planner_prompt = (journal_dir / "planner_prompt.md").read_text(encoding="utf-8")
            manifest = json.loads((journal_dir / "manifest.json").read_text(encoding="utf-8"))

            self.assertIn("RECURRING_TASKS\n", planner_prompt)
            self.assertIn("R00001", planner_prompt)
            self.assertIn("PENDING_APPROVAL\n", planner_prompt)
            self.assertIn("proposed_follow_up.md", planner_prompt)
            self.assertEqual(manifest["summary"], "2 task(s) planned from local and recurring queues")
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
                    },
                    {
                        "id": "R00001",
                        "title": "Daily cleanup",
                        "type": "maintenance",
                        "repo_slug": "example",
                        "source_ref": "tasks/recurring/R00001_daily_cleanup.md",
                        "filename": "R00001_daily_cleanup.md",
                        "max_time_minutes": 45,
                        "complexity": "medium",
                    },
                ],
            )
            self.assertTrue((journal_dir / "planner_response.json").exists())

    def test_run_command_falls_back_to_local_manifest_when_planner_output_is_not_a_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_local_task(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={"OMNIUS_CODEX_BIN": str(fake_codex)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            journal_dir = journals[0].parent
            planner_response = json.loads((journal_dir / "planner_response.json").read_text(encoding="utf-8"))
            manifest = json.loads((journal_dir / "manifest.json").read_text(encoding="utf-8"))
            dispatch_log = json.loads((journal_dir / "dispatch_log.json").read_text(encoding="utf-8"))

            self.assertEqual(planner_response, manifest)
            self.assertEqual(manifest["summary"], "1 task(s) planned from local queue")
            self.assertEqual([task["id"] for task in manifest["tasks"]], ["O00001"])
            self.assertEqual(dispatch_log["pipeline"]["status"], "completed")
            self.assertFalse(dispatch_log["planner"]["used_runner_output"])

    def test_run_command_surfaces_quarantined_recurring_state_in_prompt_and_dispatch_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_local_task(home)
            self._write_recurring_task(home)
            self._write_corrupt_recurring_state(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={"OMNIUS_CODEX_BIN": str(fake_codex)},
            )

            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            journal_dir = journals[0].parent
            planner_prompt = (journal_dir / "planner_prompt.md").read_text(encoding="utf-8")
            dispatch_log = json.loads((journal_dir / "dispatch_log.json").read_text(encoding="utf-8"))

            self.assertIn("recurring_state.json.suspect.", planner_prompt)
            self.assertIn("recurring_state", dispatch_log["planner"])
            self.assertIn("suspect_path", dispatch_log["planner"]["recurring_state"])
            self.assertIn("recurring_state.json.suspect.", dispatch_log["planner"]["recurring_state"]["suspect_path"])

    def test_run_command_returns_nonzero_when_worker_result_is_non_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / ".omnius"
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_local_task(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={
                    "OMNIUS_CODEX_BIN": str(fake_codex),
                    "OMNIUS_FAKE_CODEX_RESULT": "FAILURE",
                },
            )

            self.assertNotEqual(result.returncode, 0)
            journals = sorted((home / "journal").rglob("dispatch_log.json"))
            self.assertEqual(len(journals), 1)
            dispatch_log = json.loads(journals[0].read_text(encoding="utf-8"))
            self.assertEqual(dispatch_log["pipeline"]["status"], "completed")
            self.assertEqual(dispatch_log["tasks"]["O00001"]["status"], "FAILURE")
            self.assertTrue((home / "tasks" / "O00001_add_sample.md").exists())
            self.assertFalse((home / "tasks" / "completed" / "O00001_add_sample.md").exists())

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
            repo = self._create_repo_with_origin(tmp_path)

            self._write_config(home=home, repo=repo)
            self._write_malformed_local_task(home)
            fake_bin, fake_codex = self._write_fake_run_binaries(tmp_path)

            result = self._run_cli(
                home=home,
                fake_bin=fake_bin,
                extra_env={"OMNIUS_CODEX_BIN": str(fake_codex)},
            )

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

    def _run_cli(
        self,
        *,
        home: Path,
        fake_bin: Optional[Path] = None,
        extra_env: Optional[dict[str, str]] = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["OMNIUS_HOME"] = str(home)
        env["PYTHONPATH"] = str(SRC)
        if fake_bin is not None:
            env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
        if extra_env is not None:
            env.update(extra_env)
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

    def _write_recurring_task(self, home: Path) -> None:
        (home / "tasks" / "recurring").mkdir(parents=True, exist_ok=True)
        (home / "tasks" / "recurring" / "R00001_daily_cleanup.md").write_text(
            textwrap.dedent(
                """
                ---
                title: Daily cleanup
                repo: example
                schedule: daily
                type: maintenance
                complexity: medium
                max_time_minutes: 45
                ---
                Recurring body
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

    def _write_pending_approval_task(self, home: Path) -> None:
        (home / "tasks" / "pending_approval").mkdir(parents=True, exist_ok=True)
        (home / "tasks" / "pending_approval" / "proposed_follow_up.md").write_text(
            "Needs review\n",
            encoding="utf-8",
        )

    def _write_corrupt_recurring_state(self, home: Path) -> None:
        (home / "state").mkdir(parents=True, exist_ok=True)
        (home / "state" / "recurring_state.json").write_text("{not-json", encoding="utf-8")

    def _write_fake_run_binaries(self, tmp_path: Path) -> tuple[Path, Path]:
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
        fake_codex = fake_bin / "omnius-fake-codex"
        self._write_executable(
            fake_codex,
            textwrap.dedent(
                """\
                #!/bin/sh
                set -eu
                [ "$1" = "exec" ] || {
                    echo "expected exec mode" >&2
                    exit 10
                }
                shift
                worktree=""
                saw_output_schema="0"
                prompt=""
                while [ "$#" -gt 0 ]; do
                    case "$1" in
                        --cd)
                            shift
                            worktree="$1"
                            ;;
                        --sandbox)
                            shift
                            [ "$1" = "workspace-write" ] || {
                                echo "unexpected sandbox: $1" >&2
                                exit 11
                            }
                            ;;
                        --ask-for-approval)
                            shift
                            [ "$1" = "never" ] || {
                                echo "unexpected approval mode: $1" >&2
                                exit 12
                            }
                            ;;
                        --output-schema)
                            shift
                            [ -f "$1" ] || {
                                echo "missing schema file: $1" >&2
                                exit 13
                            }
                            saw_output_schema="1"
                            ;;
                        *)
                            prompt="$1"
                            ;;
                    esac
                    shift
                done
                [ "$saw_output_schema" = "1" ] || {
                    echo "missing --output-schema" >&2
                    exit 14
                }
                [ -n "$worktree" ] || {
                    echo "missing --cd path" >&2
                    exit 15
                }
                actual_pwd="$(pwd -P)"
                expected_pwd="$(cd "$worktree" && pwd -P)"
                [ "$actual_pwd" = "$expected_pwd" ] || {
                    echo "unexpected cwd: $actual_pwd != $expected_pwd" >&2
                    exit 16
                }
                [ -n "$prompt" ] || {
                    echo "missing prompt payload" >&2
                    exit 17
                }
                case "${OMNIUS_FAKE_CODEX_RESULT:-SUCCESS}" in
                    SUCCESS)
                        printf '{"status":"SUCCESS","branch":"%s","summary":"done"}\n' "$OMNIUS_BRANCH"
                        ;;
                    FAILURE)
                        printf '{"status":"FAILURE","error":"worker failed"}\n'
                        ;;
                    *)
                        echo "unsupported fake result: ${OMNIUS_FAKE_CODEX_RESULT}" >&2
                        exit 18
                        ;;
                esac
                """
            ),
        )
        return fake_bin, fake_codex

    def _create_repo_with_origin(self, tmp_path: Path) -> Path:
        origin = tmp_path / "origin.git"
        repo = tmp_path / "repo"
        subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True, text=True)
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "omnius@example.com"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Omnius Test"],
            check=True,
            capture_output=True,
            text=True,
        )
        (repo / ".gitignore").write_text(".omnius/\n", encoding="utf-8")
        (repo / "README.md").write_text("example\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "initial"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "origin", str(origin)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "push", "-u", "origin", "main"],
            check=True,
            capture_output=True,
            text=True,
        )
        return repo

    def _write_executable(self, path: Path, content: str) -> None:
        path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
