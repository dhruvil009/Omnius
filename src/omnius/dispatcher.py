from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import importlib.resources as resources

from omnius.config import OmniusConfig, RepoConfig
from omnius.recurring import record_recurring_task_result
from omnius.runners.base import RunnerAdapter, WorkerRequest
from omnius.tasks import archive_local_task_success, move_local_task_to_pending_approval


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix=".tmp",
        text=True,
    )
    os.close(descriptor)
    temp_path = Path(temp_name)
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp_path, path)


def initialize_dispatch_log(
    path: Path,
    *,
    pipeline_id: str,
    runner_name: str,
    repo_slug: str,
    branch: str,
) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "pipeline": {
            "pipeline_id": pipeline_id,
            "runner": runner_name,
            "repo_slug": repo_slug,
            "branch": branch,
            "circuit_breaker": {
                "state": "closed",
                "consecutive_failures": 0,
            },
        },
        "tasks": {},
    }
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def load_dispatch_log(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_patch(base: dict[str, object], patch: dict[str, object]) -> dict[str, object]:
    merged = dict(base)
    for key, value in patch.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_patch(existing, value)
            continue
        merged[key] = value
    return merged


def update_dispatch_log(path: Path, *, patch: dict[str, object]) -> dict[str, object]:
    payload = load_dispatch_log(path)
    merged = _merge_patch(payload, patch)
    _write_json_atomic(path, merged)
    return merged


@dataclass(frozen=True)
class DispatchTask:
    task_id: str
    title: str
    task_type: str
    repo_slug: str
    source_ref: str
    filename: str
    max_time_minutes: int
    complexity: str


def dispatch_manifest(
    *,
    manifest: dict[str, object],
    runner: RunnerAdapter,
    config: OmniusConfig,
    workspace_home: Path,
    journal_dir: Path,
    dispatch_log_path: Path,
) -> dict[str, object]:
    repo_lookup = {repo.slug: repo for repo in config.repos}
    run_date = date.fromisoformat(str(manifest["run_date"]))
    failure_threshold = config.global_config.max_consecutive_failures
    consecutive_failures = 0
    elapsed_pipeline_seconds = 0.0
    update_dispatch_log(
        dispatch_log_path,
        patch={
            "pipeline": {
                "circuit_breaker": {
                    "consecutive_failures": consecutive_failures,
                    "state": "closed",
                    "threshold": failure_threshold,
                }
            }
        },
    )
    for raw_task in manifest.get("tasks", []):
        task = _parse_dispatch_task(raw_task)
        remaining_budget_minutes = config.global_config.pipeline_budget_minutes - (elapsed_pipeline_seconds / 60)
        if remaining_budget_minutes <= 0:
            task_state = _build_skipped_task_state(task=task, status="BUDGET_EXHAUSTED")
            update_dispatch_log(
                dispatch_log_path,
                patch={"tasks": {task.task_id: task_state}},
            )
            continue
        if consecutive_failures >= failure_threshold:
            task_state = _build_skipped_task_state(task=task, status="CIRCUIT_BREAKER_SKIPPED")
            update_dispatch_log(
                dispatch_log_path,
                patch={"tasks": {task.task_id: task_state}},
            )
            continue

        repo = repo_lookup[task.repo_slug]
        task_state = _dispatch_one_task(
            task=task,
            repo=repo,
            runner=runner,
            workspace_home=workspace_home,
            journal_dir=journal_dir,
            max_time_minutes=min(task.max_time_minutes, remaining_budget_minutes),
        )
        elapsed_pipeline_seconds += float(task_state["duration_seconds"])
        _apply_task_side_effects(
            task=task,
            task_state=task_state,
            workspace_home=workspace_home,
            run_date=run_date,
            failure_threshold=failure_threshold,
        )
        consecutive_failures = 0 if task_state["status"] == "SUCCESS" else consecutive_failures + 1
        update_dispatch_log(
            dispatch_log_path,
            patch={
                "tasks": {task.task_id: task_state},
                "pipeline": {
                    "circuit_breaker": {
                        "consecutive_failures": consecutive_failures,
                        "state": "open" if consecutive_failures >= failure_threshold else "closed",
                    }
                },
            },
        )
    return load_dispatch_log(dispatch_log_path)


def _dispatch_one_task(
    *,
    task: DispatchTask,
    repo: RepoConfig,
    runner: RunnerAdapter,
    workspace_home: Path,
    journal_dir: Path,
    max_time_minutes: float,
) -> dict[str, object]:
    started_at = time.monotonic()
    ended_at_wall_clock: str | None = None
    repo_path = Path(repo.path).expanduser()
    branch = f"omnius/{journal_dir.parent.name}/{task.task_id}"
    base_ref = f"origin/{repo.branch}"
    worktree_path = repo_path / ".omnius" / "worktrees" / journal_dir.parent.name / task.task_id
    prompt_path = journal_dir / f"{task.task_id}_prompt.md"
    stdout_path = journal_dir / f"{task.task_id}_stdout.json"
    stderr_path = journal_dir / f"{task.task_id}_stderr.log"

    try:
        _prepare_worktree(repo_path=repo_path, base_branch=repo.branch, branch=branch, worktree_path=worktree_path)
        task_source_path = workspace_home / task.source_ref
        task_body = task_source_path.read_text(encoding="utf-8")
        prompt_text = _render_worker_prompt(
            task=task,
            branch=branch,
            base_ref=base_ref,
            journal_dir=journal_dir,
            task_body=task_body,
        )
        prompt_path.write_text(prompt_text, encoding="utf-8")
        request = WorkerRequest(
            task_id=task.task_id,
            prompt=prompt_text,
            prompt_path=prompt_path,
            worktree_path=worktree_path,
            journal_dir=journal_dir,
            branch=branch,
            base_ref=base_ref,
            max_time_minutes=max_time_minutes,
        )
        _run_worker_process(
            runner=runner,
            request=request,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        task_state = _classify_worker_result(
            task=task,
            stdout_path=stdout_path,
            branch=branch,
        )
        ended_at_wall_clock = _now_iso()
    except _WorkerTimeout:
        task_state = {
            "id": task.task_id,
            "title": task.title,
            "repo_slug": task.repo_slug,
            "status": "TIMEOUT",
            "branch": branch,
        }
        ended_at_wall_clock = _now_iso()
    except Exception as exc:
        task_state = {
            "id": task.task_id,
            "title": task.title,
            "repo_slug": task.repo_slug,
            "status": "FAILURE",
            "branch": branch,
            "error": str(exc),
        }
        ended_at_wall_clock = _now_iso()
    finally:
        _cleanup_worktree(repo_path=repo_path, worktree_path=worktree_path)

    duration_seconds = time.monotonic() - started_at
    task_state["start_monotonic"] = started_at
    task_state["end"] = ended_at_wall_clock
    task_state["duration_seconds"] = round(duration_seconds, 3)
    return task_state


def _apply_task_side_effects(
    *,
    task: DispatchTask,
    task_state: dict[str, object],
    workspace_home: Path,
    run_date: date,
    failure_threshold: int,
) -> None:
    status = str(task_state["status"])
    if task.task_id.startswith("O"):
        if status == "SUCCESS":
            archive_local_task_success(
                home=workspace_home,
                task_id=task.task_id,
                filename=task.filename,
                run_date=run_date.isoformat(),
            )
        elif status == "PARTIAL":
            move_local_task_to_pending_approval(
                home=workspace_home,
                task_id=task.task_id,
                filename=task.filename,
            )
        return

    if task.task_id.startswith("R"):
        record_recurring_task_result(
            workspace_home,
            task_id=task.task_id,
            run_date=run_date,
            status=status,
            max_consecutive_failures=failure_threshold,
        )


def _build_skipped_task_state(*, task: DispatchTask, status: str) -> dict[str, object]:
    return {
        "id": task.task_id,
        "title": task.title,
        "repo_slug": task.repo_slug,
        "status": status,
    }


def _prepare_worktree(*, repo_path: Path, base_branch: str, branch: str, worktree_path: Path) -> None:
    _remove_existing_worktree(repo_path=repo_path, worktree_path=worktree_path)
    _run_git(repo_path, ["fetch", "origin", base_branch])
    if _branch_exists(repo_path, branch):
        _run_git(repo_path, ["worktree", "add", str(worktree_path), branch])
        return
    _run_git(repo_path, ["worktree", "add", "-b", branch, str(worktree_path), f"origin/{base_branch}"])


def _remove_existing_worktree(*, repo_path: Path, worktree_path: Path) -> None:
    listed = subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    worktree_line = f"worktree {worktree_path}"
    if worktree_line in listed.stdout.splitlines():
        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "remove", "--force", str(worktree_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    if worktree_path.exists():
        for child in sorted(worktree_path.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        worktree_path.rmdir()


def _cleanup_worktree(*, repo_path: Path, worktree_path: Path) -> None:
    if not worktree_path.exists():
        return
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "remove", "--force", str(worktree_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def _render_worker_prompt(
    *,
    task: DispatchTask,
    branch: str,
    base_ref: str,
    journal_dir: Path,
    task_body: str,
) -> str:
    template = _load_worker_prompt_template()
    return template.format(
        task_id=task.task_id,
        title=task.title,
        source_ref=task.source_ref,
        branch=branch,
        base_ref=base_ref,
        journal_dir=str(journal_dir),
        max_time_minutes=task.max_time_minutes,
        complexity=task.complexity,
        task_type=task.task_type,
        task_body=task_body,
    )


def _load_worker_prompt_template() -> str:
    resource = resources.files("omnius").joinpath("resources", "prompts", "worker_implementation.md")
    return resource.read_text(encoding="utf-8")


def _run_worker_process(
    *,
    runner: RunnerAdapter,
    request: WorkerRequest,
    stdout_path: Path,
    stderr_path: Path,
) -> None:
    worker_env = {
        **os.environ,
        "OMNIUS_WORKER": "1",
        "OMNIUS_TASK_ID": request.task_id,
        "OMNIUS_JOURNAL_DIR": str(request.journal_dir),
        "OMNIUS_BRANCH": request.branch,
        "OMNIUS_BASE_REF": request.base_ref,
    }
    command = runner.build_worker_command(request)
    with stdout_path.open("w", encoding="utf-8") as stdout_handle:
        with stderr_path.open("w", encoding="utf-8") as stderr_handle:
            process = subprocess.Popen(
                command,
                cwd=request.worktree_path,
                env=worker_env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
                start_new_session=True,
            )
            timeout_seconds = request.max_time_minutes * 60
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                _terminate_process_group(process)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                raise _WorkerTimeout from exc
            if process.returncode not in (0, 1):
                # Non-zero exit codes are still interpreted through the JSON envelope.
                return


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        time.sleep(0.1)
        os.killpg(process.pid, signal.SIGKILL)
        return
    except (PermissionError, ProcessLookupError):
        pass

    try:
        process.terminate()
    except ProcessLookupError:
        return
    time.sleep(0.1)
    try:
        process.kill()
    except ProcessLookupError:
        return


def _classify_worker_result(*, task: DispatchTask, stdout_path: Path, branch: str) -> dict[str, object]:
    stdout_text = stdout_path.read_text(encoding="utf-8").strip()
    try:
        payload = json.loads(stdout_text)
    except json.JSONDecodeError:
        return {
            "id": task.task_id,
            "title": task.title,
            "repo_slug": task.repo_slug,
            "status": "CRASH",
            "branch": branch,
            "error": "Malformed worker JSON output",
        }
    if not isinstance(payload, dict):
        return {
            "id": task.task_id,
            "title": task.title,
            "repo_slug": task.repo_slug,
            "status": "CRASH",
            "branch": branch,
            "error": "Worker output must be a JSON object",
        }

    status = payload.get("status")
    if status == "SUCCESS":
        summary = payload.get("summary")
        result_branch = payload.get("branch")
        if not isinstance(summary, str) or not isinstance(result_branch, str):
            return {
                "id": task.task_id,
                "title": task.title,
                "repo_slug": task.repo_slug,
                "status": "CRASH",
                "branch": branch,
                "error": "SUCCESS worker output missing required fields",
            }
        return {
            "id": task.task_id,
            "title": task.title,
            "repo_slug": task.repo_slug,
            "status": "SUCCESS",
            "branch": result_branch,
            "summary": summary,
            "pr_url": payload.get("pr_url"),
        }

    if status == "PARTIAL":
        return {
            "id": task.task_id,
            "title": task.title,
            "repo_slug": task.repo_slug,
            "status": "PARTIAL",
            "branch": payload.get("branch") or branch,
            "notes": payload.get("notes"),
        }
    if status == "BLOCKED":
        return {
            "id": task.task_id,
            "title": task.title,
            "repo_slug": task.repo_slug,
            "status": "BLOCKED",
            "branch": branch,
            "reason": payload.get("reason"),
        }
    if status == "FAILURE":
        return {
            "id": task.task_id,
            "title": task.title,
            "repo_slug": task.repo_slug,
            "status": "FAILURE",
            "branch": branch,
            "error": payload.get("error"),
        }
    return {
        "id": task.task_id,
        "title": task.title,
        "repo_slug": task.repo_slug,
        "status": "CRASH",
        "branch": branch,
        "error": "Worker output status was missing or unsupported",
    }


def _parse_dispatch_task(raw_task: object) -> DispatchTask:
    if not isinstance(raw_task, dict):
        raise ValueError("Manifest task entries must be JSON objects")
    return DispatchTask(
        task_id=str(raw_task["id"]),
        title=str(raw_task["title"]),
        task_type=str(raw_task["type"]),
        repo_slug=str(raw_task["repo_slug"]),
        source_ref=str(raw_task["source_ref"]),
        filename=str(raw_task["filename"]),
        max_time_minutes=int(raw_task["max_time_minutes"]),
        complexity=str(raw_task["complexity"]),
    )


def _run_git(repo_path: Path, args: list[str]) -> None:
    subprocess.run(
        ["git", "-C", str(repo_path), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _branch_exists(repo_path: Path, branch: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", "--quiet", branch],
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def _now_iso() -> str:
    return __import__("datetime").datetime.now().astimezone().isoformat()


class _WorkerTimeout(RuntimeError):
    pass
