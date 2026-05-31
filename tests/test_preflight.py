import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from omnius.preflight import CommandCheck, DiskSpaceCheck, FilesystemCheck, PreflightResult, RepoCheck, RepoStateCheck, run_preflight
from omnius.runners import get_runner
from omnius.runners.base import (
    DayPrepInvocation,
    PlannerInvocation,
    RunnerAdapter,
    RunnerCapability,
    RunnerHealth,
    WorkerRequest,
)


class HealthyRunner(RunnerAdapter):
    @property
    def name(self) -> str:
        return "healthy"

    def health_check(self) -> RunnerHealth:
        return RunnerHealth(ok=True, summary="runner ready")

    def discover_capabilities(self) -> dict[str, RunnerCapability]:
        return {
            "brainstorm": RunnerCapability(name="brainstorm", available=True, detail="stub"),
            "review_diff": RunnerCapability(name="review_diff", available=False, detail="missing"),
        }

    def invoke_planner(self, *, task_id: str, prompt: str) -> PlannerInvocation:
        return PlannerInvocation(
            runner_name=self.name,
            task_id=task_id,
            prompt=prompt,
            plan_text="placeholder plan",
        )

    def build_worker_command(self, request: WorkerRequest) -> list[str]:
        return ["healthy-runner", request.task_id]

    def invoke_dayprep(self, *, task_id: str, prompt: str) -> DayPrepInvocation:
        return DayPrepInvocation(runner_name=self.name, task_id=task_id, brief_markdown="placeholder brief")


class UnhealthyRunner(HealthyRunner):
    @property
    def name(self) -> str:
        return "unhealthy"

    def health_check(self) -> RunnerHealth:
        return RunnerHealth(ok=False, summary="runner offline")


class UnhealthyExplodingRunner(UnhealthyRunner):
    def discover_capabilities(self) -> dict[str, RunnerCapability]:
        raise AssertionError("discover_capabilities should not be called for unhealthy runners")


class PreflightTests(unittest.TestCase):
    def test_stub_runners_return_deterministic_placeholder_data(self) -> None:
        codex = get_runner("codex")
        claude = get_runner("claude")

        codex_plan = codex.invoke_planner(task_id="task-001", prompt="Plan task")
        claude_plan = claude.invoke_planner(task_id="task-002", prompt="Plan task")

        self.assertTrue(codex.health_check().ok)
        self.assertTrue(claude.health_check().ok)
        self.assertEqual(codex.discover_capabilities()["brainstorm"].detail, "milestone-1 stub")
        self.assertEqual(claude.discover_capabilities()["review_diff"].detail, "milestone-1 stub")
        self.assertEqual(codex_plan.runner_name, "codex")
        self.assertEqual(claude_plan.runner_name, "claude")
        self.assertIn("placeholder", codex_plan.plan_text)
        self.assertIn("placeholder", claude_plan.plan_text)

    def test_run_preflight_returns_combined_payload_and_capability_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            (repo_path / ".git").write_text("gitdir: /tmp/example\n", encoding="utf-8")

            result = run_preflight(
                runner=HealthyRunner(),
                repo_path=repo_path,
                capability_policy={
                    "brainstorm": "auto",
                    "review_diff": "force",
                    "second_opinion": "disable",
                },
                gh_check=CommandCheck(name="gh", ok=True, detail="2.0.0"),
                git_check=CommandCheck(name="git", ok=True, detail="2.45.0"),
                python_check=CommandCheck(name="python", ok=True, detail="3.11.9"),
                repo_check=RepoCheck(path=repo_path, exists=True, is_git_repo=True),
            )

        self.assertIsInstance(result, PreflightResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.abort_reason, "")
        self.assertEqual(result.runner_name, "healthy")
        self.assertEqual(result.payload["runner"]["summary"], "runner ready")
        self.assertEqual(result.payload["gh"]["detail"], "2.0.0")
        self.assertEqual(result.payload["git"]["detail"], "2.45.0")
        self.assertEqual(result.payload["python"]["detail"], "3.11.9")
        self.assertTrue(result.payload["repo"]["is_git_repo"])
        self.assertEqual(
            result.payload["capabilities"],
            {
                "brainstorm": {
                    "available": True,
                    "detail": "stub",
                    "enabled": True,
                    "policy": "auto",
                    "runner_available": True,
                },
                "review_diff": {
                    "available": False,
                    "detail": "missing",
                    "enabled": True,
                    "policy": "force",
                    "runner_available": False,
                },
                "second_opinion": {
                    "available": False,
                    "detail": "disabled by user policy",
                    "enabled": False,
                    "policy": "disable",
                    "runner_available": False,
                },
            },
        )
        self.assertEqual(result.payload["available_capabilities"], ["brainstorm"])
        self.assertEqual(result.payload["forced_unavailable_capabilities"], ["review_diff"])

    def test_run_preflight_aborts_when_repo_is_missing_or_not_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "missing-repo"

            for repo_check in (
                RepoCheck(path=repo_path, exists=False, is_git_repo=False),
                RepoCheck(path=repo_path, exists=True, is_git_repo=False),
            ):
                with self.subTest(repo_check=repo_check):
                    result = run_preflight(
                        runner=HealthyRunner(),
                        repo_path=repo_path,
                        capability_policy={},
                        check_github=False,
                        git_check=CommandCheck(name="git", ok=True, detail="2.45.0"),
                        python_check=CommandCheck(name="python", ok=True, detail="3.11.9"),
                        repo_check=repo_check,
                    )

                    self.assertFalse(result.ok)
                    self.assertEqual(result.abort_reason, "repo")

    def test_run_preflight_aborts_when_repo_has_dirty_or_in_progress_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)

            dirty_result = run_preflight(
                runner=HealthyRunner(),
                repo_path=repo_path,
                capability_policy={},
                check_github=False,
                git_check=CommandCheck(name="git", ok=True, detail="2.45.0"),
                python_check=CommandCheck(name="python", ok=True, detail="3.11.9"),
                repo_check=RepoCheck(path=repo_path, exists=True, is_git_repo=True),
                repo_state_check=RepoStateCheck(clean=False, merge_in_progress=False, rebase_in_progress=False, detail="M README.md"),
            )
            merge_result = run_preflight(
                runner=HealthyRunner(),
                repo_path=repo_path,
                capability_policy={},
                check_github=False,
                git_check=CommandCheck(name="git", ok=True, detail="2.45.0"),
                python_check=CommandCheck(name="python", ok=True, detail="3.11.9"),
                repo_check=RepoCheck(path=repo_path, exists=True, is_git_repo=True),
                repo_state_check=RepoStateCheck(clean=True, merge_in_progress=True, rebase_in_progress=False, detail="merge in progress"),
            )
            rebase_result = run_preflight(
                runner=HealthyRunner(),
                repo_path=repo_path,
                capability_policy={},
                check_github=False,
                git_check=CommandCheck(name="git", ok=True, detail="2.45.0"),
                python_check=CommandCheck(name="python", ok=True, detail="3.11.9"),
                repo_check=RepoCheck(path=repo_path, exists=True, is_git_repo=True),
                repo_state_check=RepoStateCheck(clean=True, merge_in_progress=False, rebase_in_progress=True, detail="rebase in progress"),
            )

        self.assertEqual(dirty_result.abort_reason, "repo_state")
        self.assertEqual(merge_result.abort_reason, "repo_state")
        self.assertEqual(rebase_result.abort_reason, "repo_state")
        self.assertFalse(dirty_result.payload["repo_state"]["clean"])
        self.assertTrue(merge_result.payload["repo_state"]["merge_in_progress"])
        self.assertTrue(rebase_result.payload["repo_state"]["rebase_in_progress"])

    def test_run_preflight_default_repo_state_detects_dirty_git_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "omnius@example.com"], cwd=repo_path, check=True)
            subprocess.run(["git", "config", "user.name", "Omnius Test"], cwd=repo_path, check=True)
            (repo_path / "README.md").write_text("clean\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True, capture_output=True, text=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_path, check=True, capture_output=True, text=True)
            (repo_path / "README.md").write_text("dirty\n", encoding="utf-8")

            result = run_preflight(
                runner=HealthyRunner(),
                repo_path=repo_path,
                capability_policy={},
                check_github=False,
                check_repo_state=True,
                git_check=CommandCheck(name="git", ok=True, detail="2.45.0"),
                python_check=CommandCheck(name="python", ok=True, detail="3.11.9"),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.abort_reason, "repo_state")
        self.assertFalse(result.payload["repo_state"]["clean"])
        self.assertIn("README.md", result.payload["repo_state"]["detail"])

    def test_run_preflight_aborts_when_runtime_paths_are_not_writable_or_disk_is_low(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            repo_check = RepoCheck(path=repo_path, exists=True, is_git_repo=True)
            repo_state = RepoStateCheck(clean=True, merge_in_progress=False, rebase_in_progress=False, detail="clean")

            filesystem_result = run_preflight(
                runner=HealthyRunner(),
                repo_path=repo_path,
                capability_policy={},
                check_github=False,
                git_check=CommandCheck(name="git", ok=True, detail="2.45.0"),
                python_check=CommandCheck(name="python", ok=True, detail="3.11.9"),
                repo_check=repo_check,
                repo_state_check=repo_state,
                filesystem_check=FilesystemCheck(ok=False, detail="journal not writable"),
                disk_space_check=DiskSpaceCheck(path=repo_path, ok=True, free_bytes=10_000_000_000, min_free_bytes=536_870_912, detail="ok"),
            )
            disk_result = run_preflight(
                runner=HealthyRunner(),
                repo_path=repo_path,
                capability_policy={},
                check_github=False,
                git_check=CommandCheck(name="git", ok=True, detail="2.45.0"),
                python_check=CommandCheck(name="python", ok=True, detail="3.11.9"),
                repo_check=repo_check,
                repo_state_check=repo_state,
                filesystem_check=FilesystemCheck(ok=True, detail="ok"),
                disk_space_check=DiskSpaceCheck(path=repo_path, ok=False, free_bytes=1, min_free_bytes=536_870_912, detail="low disk"),
            )

        self.assertFalse(filesystem_result.ok)
        self.assertEqual(filesystem_result.abort_reason, "filesystem")
        self.assertEqual(filesystem_result.payload["filesystem"]["detail"], "journal not writable")
        self.assertFalse(disk_result.ok)
        self.assertEqual(disk_result.abort_reason, "disk")
        self.assertEqual(disk_result.payload["disk"]["detail"], "low disk")

    def test_run_preflight_skips_github_checks_for_local_only_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            (repo_path / ".git").write_text("gitdir: /tmp/example\n", encoding="utf-8")

            with patch("omnius.preflight._default_gh_payload", side_effect=AssertionError("gh probe should be skipped")):
                result = run_preflight(
                    runner=HealthyRunner(),
                    repo_path=repo_path,
                    capability_policy={"brainstorm": "auto"},
                    check_github=False,
                    git_check=CommandCheck(name="git", ok=True, detail="2.45.0"),
                    python_check=CommandCheck(name="python", ok=True, detail="3.11.9"),
                    repo_check=RepoCheck(path=repo_path, exists=True, is_git_repo=True),
                )

        self.assertTrue(result.ok)
        self.assertTrue(result.payload["gh"]["skipped"])
        self.assertEqual(result.payload["gh"]["detail"], "GitHub checks skipped for this run")

    def test_run_preflight_aborts_when_runner_is_unhealthy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            (repo_path / ".git").write_text("gitdir: /tmp/example\n", encoding="utf-8")

            result = run_preflight(
                runner=UnhealthyRunner(),
                repo_path=repo_path,
                capability_policy={"brainstorm": "auto"},
                gh_check=CommandCheck(name="gh", ok=True, detail="2.0.0"),
                git_check=CommandCheck(name="git", ok=True, detail="2.45.0"),
                python_check=CommandCheck(name="python", ok=True, detail="3.11.9"),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.abort_reason, "runner")

    def test_run_preflight_skips_capability_discovery_for_unhealthy_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            (repo_path / ".git").write_text("gitdir: /tmp/example\n", encoding="utf-8")

            result = run_preflight(
                runner=UnhealthyExplodingRunner(),
                repo_path=repo_path,
                capability_policy={"brainstorm": "auto"},
                gh_check=CommandCheck(name="gh", ok=True, detail="2.0.0"),
                git_check=CommandCheck(name="git", ok=True, detail="2.45.0"),
                python_check=CommandCheck(name="python", ok=True, detail="3.11.9"),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.abort_reason, "runner")
        self.assertEqual(
            result.payload["capabilities"],
            {
                "brainstorm": {
                    "available": False,
                    "detail": "capability discovery skipped for unhealthy runner",
                    "enabled": True,
                    "policy": "auto",
                    "runner_available": False,
                },
            },
        )

    def test_run_preflight_aborts_on_bad_tooling_or_python_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            (repo_path / ".git").write_text("gitdir: /tmp/example\n", encoding="utf-8")

            gh_result = run_preflight(
                runner=HealthyRunner(),
                repo_path=repo_path,
                capability_policy={},
                gh_check=CommandCheck(name="gh", ok=False, detail="not installed"),
                git_check=CommandCheck(name="git", ok=True, detail="2.45.0"),
                python_check=CommandCheck(name="python", ok=True, detail="3.11.9"),
            )
            git_result = run_preflight(
                runner=HealthyRunner(),
                repo_path=repo_path,
                capability_policy={},
                gh_check=CommandCheck(name="gh", ok=True, detail="2.0.0"),
                git_check=CommandCheck(name="git", ok=False, detail="missing"),
                python_check=CommandCheck(name="python", ok=True, detail="3.11.9"),
            )
            python_result = run_preflight(
                runner=HealthyRunner(),
                repo_path=repo_path,
                capability_policy={},
                gh_check=CommandCheck(name="gh", ok=True, detail="2.0.0"),
                git_check=CommandCheck(name="git", ok=True, detail="2.45.0"),
                python_check=CommandCheck(name="python", ok=False, detail="3.10.14"),
            )

        self.assertEqual(gh_result.abort_reason, "gh")
        self.assertEqual(git_result.abort_reason, "git")
        self.assertEqual(python_result.abort_reason, "python")

    def test_run_preflight_default_checks_abort_when_gh_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            (repo_path / ".git").write_text("gitdir: /tmp/example\n", encoding="utf-8")

            def fake_which(name: str) -> Optional[str]:
                if name == "gh":
                    return None
                return f"/usr/bin/{name}"

            with patch("omnius.preflight.shutil.which", side_effect=fake_which):
                result = run_preflight(
                    runner=HealthyRunner(),
                    repo_path=repo_path,
                    capability_policy={},
                    python_check=CommandCheck(name="python", ok=True, detail="3.11.9"),
                )

        self.assertFalse(result.ok)
        self.assertEqual(result.abort_reason, "gh")
        self.assertFalse(result.payload["gh"]["ok"])
        self.assertEqual(result.payload["gh"]["detail"], "command not found")

    def test_run_preflight_default_checks_abort_when_git_version_probe_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            (repo_path / ".git").write_text("gitdir: /tmp/example\n", encoding="utf-8")

            def fake_which(name: str) -> Optional[str]:
                return f"/usr/bin/{name}"

            def fake_run(argv: list[str], capture_output: bool, text: bool, check: bool) -> subprocess.CompletedProcess:
                self.assertEqual(capture_output, True)
                self.assertEqual(text, True)
                self.assertEqual(check, False)
                if argv[0] == "/usr/bin/gh":
                    return subprocess.CompletedProcess(argv, 0, stdout="gh 2.0.0\n", stderr="")
                if argv[0] == "/usr/bin/git":
                    return subprocess.CompletedProcess(argv, 1, stdout="", stderr="git broken\n")
                raise AssertionError(argv)

            with patch("omnius.preflight.shutil.which", side_effect=fake_which):
                with patch("omnius.preflight.subprocess.run", side_effect=fake_run):
                    result = run_preflight(
                        runner=HealthyRunner(),
                        repo_path=repo_path,
                        capability_policy={},
                        python_check=CommandCheck(name="python", ok=True, detail="3.11.9"),
                    )

        self.assertFalse(result.ok)
        self.assertEqual(result.abort_reason, "git")
        self.assertTrue(result.payload["gh"]["ok"])
        self.assertFalse(result.payload["git"]["ok"])
        self.assertEqual(result.payload["git"]["detail"], "git broken")

    def test_run_preflight_default_checks_abort_when_gh_auth_probe_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            (repo_path / ".git").write_text("gitdir: /tmp/example\n", encoding="utf-8")

            def fake_which(name: str) -> Optional[str]:
                return f"/usr/bin/{name}"

            def fake_run(argv: list[str], capture_output: bool, text: bool, check: bool) -> subprocess.CompletedProcess:
                self.assertEqual(capture_output, True)
                self.assertEqual(text, True)
                self.assertEqual(check, False)
                if argv == ["/usr/bin/gh", "--version"]:
                    return subprocess.CompletedProcess(argv, 0, stdout="gh 2.0.0\n", stderr="")
                if argv == ["/usr/bin/gh", "auth", "status"]:
                    return subprocess.CompletedProcess(argv, 1, stdout="", stderr="not logged in\n")
                if argv == ["/usr/bin/git", "--version"]:
                    return subprocess.CompletedProcess(argv, 0, stdout="git version 2.45.0\n", stderr="")
                raise AssertionError(argv)

            with patch("omnius.preflight.shutil.which", side_effect=fake_which):
                with patch("omnius.preflight.subprocess.run", side_effect=fake_run):
                    result = run_preflight(
                        runner=HealthyRunner(),
                        repo_path=repo_path,
                        capability_policy={},
                        python_check=CommandCheck(name="python", ok=True, detail="3.11.9"),
                    )

        self.assertFalse(result.ok)
        self.assertEqual(result.abort_reason, "gh")
        self.assertFalse(result.payload["gh"]["ok"])
        self.assertEqual(result.payload["gh"]["detail"], "not logged in")
        self.assertEqual(
            result.payload["gh"]["probes"],
            {
                "version": {"ok": True, "detail": "gh 2.0.0"},
                "auth": {"ok": False, "detail": "not logged in"},
            },
        )

    def test_run_preflight_default_checks_keep_both_gh_probe_diagnostics_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            (repo_path / ".git").write_text("gitdir: /tmp/example\n", encoding="utf-8")

            def fake_which(name: str) -> Optional[str]:
                return f"/usr/bin/{name}"

            def fake_run(argv: list[str], capture_output: bool, text: bool, check: bool) -> subprocess.CompletedProcess:
                self.assertEqual(capture_output, True)
                self.assertEqual(text, True)
                self.assertEqual(check, False)
                if argv == ["/usr/bin/gh", "--version"]:
                    return subprocess.CompletedProcess(argv, 0, stdout="gh 2.1.0\n", stderr="")
                if argv == ["/usr/bin/gh", "auth", "status"]:
                    return subprocess.CompletedProcess(argv, 0, stdout="logged in\n", stderr="")
                if argv == ["/usr/bin/git", "--version"]:
                    return subprocess.CompletedProcess(argv, 0, stdout="git version 2.45.0\n", stderr="")
                raise AssertionError(argv)

            with patch("omnius.preflight.shutil.which", side_effect=fake_which):
                with patch("omnius.preflight.subprocess.run", side_effect=fake_run):
                    result = run_preflight(
                        runner=HealthyRunner(),
                        repo_path=repo_path,
                        capability_policy={},
                        python_check=CommandCheck(name="python", ok=True, detail="3.11.9"),
                    )

        self.assertTrue(result.ok)
        self.assertEqual(result.abort_reason, "")
        self.assertTrue(result.payload["gh"]["ok"])
        self.assertEqual(result.payload["gh"]["detail"], "logged in")
        self.assertEqual(
            result.payload["gh"]["probes"],
            {
                "version": {"ok": True, "detail": "gh 2.1.0"},
                "auth": {"ok": True, "detail": "logged in"},
            },
        )
