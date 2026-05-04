from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys

from omnius.config import ConfigError, RepoConfig, load_config
from omnius.dispatcher import initialize_dispatch_log, update_dispatch_log
from omnius.planner import (
    build_planner_prompt,
    load_planner_prompt_template,
    parse_planner_response,
    validate_manifest,
)
from omnius.preflight import run_preflight
from omnius.runners import get_runner
from omnius.tasks import load_local_task_entries, render_local_tasks_section
from omnius.workspace import bootstrap_workspace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omnius")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser(
        "run",
        help="Execute one Omnius pipeline run",
        description="Execute one Omnius pipeline run",
    )
    run_parser.set_defaults(handler=run_command)
    return parser


def run_command(_args: argparse.Namespace) -> int:
    workspace_home = _resolve_workspace_home()
    workspace_paths = bootstrap_workspace(workspace_home)
    try:
        config = load_config(workspace_home / "omnius.toml")
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    runner = get_runner(config.runner.default)

    run_started_at = datetime.now().astimezone()
    run_date = run_started_at.strftime("%Y-%m-%d")
    journal_dir = workspace_paths.journal_dir / run_date / run_started_at.strftime("%H%M")
    journal_dir.mkdir(parents=True, exist_ok=True)
    primary_repo = config.repos[0] if config.repos else None

    dispatch_log_path = journal_dir / "dispatch_log.json"
    initialize_dispatch_log(
        dispatch_log_path,
        pipeline_id=run_started_at.strftime("pipeline-%Y%m%d-%H%M%S"),
        runner_name=runner.name,
        repo_slug=primary_repo.slug if primary_repo is not None else "<none>",
        branch=primary_repo.branch if primary_repo is not None else "<none>",
    )
    update_dispatch_log(
        dispatch_log_path,
        patch={
            "pipeline": {
                "status": "running",
                "run_date": run_date,
                "journal_dir": str(journal_dir),
                "started_at": run_started_at.isoformat(),
            },
        },
    )
    if primary_repo is None:
        return _finalize_pipeline_failure(
            dispatch_log_path,
            abort_reason="config",
            error="Config must define at least one repo for 'omnius run'",
        )

    try:
        preflight = run_preflight(
            runner=runner,
            repo_path=Path(primary_repo.path).expanduser(),
            required_capabilities=_required_capabilities(config),
        )
        preflight_payload = {
            "ok": preflight.ok,
            "abort_reason": preflight.abort_reason,
            "runner_name": preflight.runner_name,
            "payload": preflight.payload,
        }
        _write_json(journal_dir / "preflight.json", preflight_payload)
        update_dispatch_log(
            dispatch_log_path,
            patch={
                "preflight": preflight_payload,
            },
        )
        if not preflight.ok:
            update_dispatch_log(
                dispatch_log_path,
                patch={
                    "pipeline": {
                        "status": "aborted",
                        "ended_at": datetime.now().astimezone().isoformat(),
                        "abort_reason": preflight.abort_reason,
                    },
                },
            )
            return 1

        local_task_entries = load_local_task_entries(workspace_home)
        planner_prompt = build_planner_prompt(
            template=load_planner_prompt_template(),
            run_date=run_date,
            journal_dir=str(journal_dir),
            repos_table=_render_repos_table(config.repos),
            local_tasks=render_local_tasks_section(local_task_entries),
            recurring_tasks="<none>",
            github_issues="<none>",
            pr_review_comments="<none>",
            pending_approval="<none>",
        )
        (journal_dir / "planner_prompt.md").write_text(planner_prompt, encoding="utf-8")

        planner_invocation = runner.invoke_planner(task_id="milestone-1-run", prompt=planner_prompt)
        planner_response = _build_manifest_response(
            run_date=run_date,
            journal_dir=journal_dir,
            local_task_entries=local_task_entries,
            planner_plan_text=planner_invocation.plan_text,
        )
        (journal_dir / "planner_response.json").write_text(planner_response, encoding="utf-8")

        manifest = parse_planner_response(planner_response)
        validate_manifest(manifest)
        _write_json(journal_dir / "manifest.json", manifest)
    except Exception as exc:
        return _finalize_pipeline_failure(
            dispatch_log_path,
            abort_reason="pipeline_error",
            error=str(exc),
        )

    update_dispatch_log(
        dispatch_log_path,
        patch={
            "pipeline": {
                "status": "completed",
                "ended_at": datetime.now().astimezone().isoformat(),
            },
            "planner": {
                "task_id": planner_invocation.task_id,
                "runner_name": planner_invocation.runner_name,
            },
        },
    )
    return 0


def _resolve_workspace_home() -> Path:
    raw_home = os.environ.get("OMNIUS_HOME")
    if raw_home is None:
        return (Path.home() / ".omnius").expanduser()
    return Path(raw_home).expanduser()


def _required_capabilities(_config: object) -> list[str]:
    return [
        "brainstorm",
        "review_diff",
        "autonomous_testing",
        "second_opinion",
    ]


def _render_repos_table(repos: list[RepoConfig]) -> str:
    if not repos:
        return "<none>"
    return "\n".join(f"{repo.slug} | {repo.path} | {repo.branch} | {repo.role}" for repo in repos)


def _build_manifest_response(
    *,
    run_date: str,
    journal_dir: Path,
    local_task_entries: list[object],
    planner_plan_text: str,
) -> str:
    payload = {
        "run_date": run_date,
        "journal_dir": str(journal_dir),
        "summary": f"0 tasks planned from {len(local_task_entries)} local task(s)",
        "tasks": [],
        "skipped": [getattr(entry, "task_id") for entry in local_task_entries],
        "notes": planner_plan_text,
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _finalize_pipeline_failure(
    dispatch_log_path: Path,
    *,
    abort_reason: str,
    error: str,
) -> int:
    print(error, file=sys.stderr)
    try:
        update_dispatch_log(
            dispatch_log_path,
            patch={
                "pipeline": {
                    "status": "failed",
                    "ended_at": datetime.now().astimezone().isoformat(),
                    "abort_reason": abort_reason,
                    "error": error,
                },
            },
        )
    except Exception:
        pass
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return int(handler(args))
