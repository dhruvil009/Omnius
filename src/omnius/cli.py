from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys

from omnius.config import ConfigError, RepoConfig, load_config
from omnius.costs import SessionCostRecord, write_session_cost_record
from omnius.dayprep import run_dayprep
from omnius.dispatcher import dispatch_manifest, initialize_dispatch_log, update_dispatch_log
from omnius.planner import (
    build_manifest_tasks,
    build_planner_prompt,
    choose_planner_response,
    load_planner_prompt_template,
    parse_planner_response,
    validate_manifest,
)
from omnius.prefetch import collect_prefetch_snapshot
from omnius.preflight import run_preflight
from omnius.runners import get_runner
from omnius.status import load_status_snapshot, render_status_table
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

    status_parser = subparsers.add_parser(
        "status",
        help="Show the latest Omnius run summary",
        description="Show the latest Omnius run summary",
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable status JSON",
    )
    status_parser.set_defaults(handler=status_command)
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

        prefetch_snapshot = collect_prefetch_snapshot(
            workspace_home,
            today=run_started_at.date(),
        )
        planner_prompt = build_planner_prompt(
            template=load_planner_prompt_template(),
            run_date=run_date,
            journal_dir=str(journal_dir),
            repos_table=_render_repos_table(config.repos),
            local_tasks=prefetch_snapshot.local_tasks_section,
            recurring_tasks=prefetch_snapshot.recurring_tasks_section,
            github_issues="<none>",
            pr_review_comments="<none>",
            pending_approval=prefetch_snapshot.pending_approval_section,
        )
        (journal_dir / "planner_prompt.md").write_text(planner_prompt, encoding="utf-8")

        planner_started_at = datetime.now().astimezone()
        planner_invocation = runner.invoke_planner(task_id="milestone-1-run", prompt=planner_prompt)
        planner_ended_at = datetime.now().astimezone()
        synthesized_planner_response = _build_manifest_response(
            workspace_home=workspace_home,
            run_date=run_date,
            journal_dir=journal_dir,
            local_task_entries=prefetch_snapshot.local_task_entries,
            due_recurring_task_entries=prefetch_snapshot.due_recurring_task_entries,
            default_task_budget_minutes=config.global_config.default_task_budget_minutes,
            planner_plan_text=planner_invocation.plan_text,
        )
        planner_response = choose_planner_response(
            planner_output=planner_invocation.plan_text,
            fallback_manifest_response=synthesized_planner_response,
        )
        (journal_dir / "planner_response.json").write_text(planner_response, encoding="utf-8")

        manifest = parse_planner_response(planner_response)
        validate_manifest(manifest)
        _write_json(journal_dir / "manifest.json", manifest)
        planner_pipeline_patch: dict[str, object] = {}
        if planner_invocation.usage is not None and planner_invocation.usage.cost_usd is not None:
            planner_pipeline_patch["planner_cost_usd"] = planner_invocation.usage.cost_usd
        update_dispatch_log(
            dispatch_log_path,
            patch={
                "pipeline": planner_pipeline_patch,
                "planner": {
                    "task_id": planner_invocation.task_id,
                    "runner_name": planner_invocation.runner_name,
                    "used_runner_output": planner_response == planner_invocation.plan_text,
                    "recurring_state": {
                        "suspect_path": (
                            str(prefetch_snapshot.recurring_state_suspect_path)
                            if prefetch_snapshot.recurring_state_suspect_path is not None
                            else None
                        )
                    },
                }
            },
        )
        if planner_invocation.usage is not None:
            write_session_cost_record(
                costs_dir=workspace_home / "costs",
                session=SessionCostRecord(
                    file_stem=f"{run_date}_{journal_dir.name}_planner",
                    session_name="planner",
                    started_at=planner_started_at.isoformat(),
                    ended_at=planner_ended_at.isoformat(),
                    status="SUCCESS",
                    usage=planner_invocation.usage,
                ),
            )
        dispatch_result = dispatch_manifest(
            manifest=manifest,
            runner=runner,
            config=config,
            workspace_home=workspace_home,
            journal_dir=journal_dir,
            dispatch_log_path=dispatch_log_path,
            planner_usage=planner_invocation.usage,
        )
        dayprep_result = run_dayprep(
            runner=runner,
            workspace_home=workspace_home,
            journal_dir=journal_dir,
            dispatch_log_path=dispatch_log_path,
        )
        update_dispatch_log(
            dispatch_log_path,
            patch={
                "dayprep": {
                    "brief_path": str(dayprep_result.brief_path),
                    "latest_brief_path": str(dayprep_result.latest_brief_path),
                    "used_fallback": dayprep_result.used_fallback,
                    "warning_banner": dayprep_result.warning_banner,
                }
            },
        )
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
        },
    )
    return 0 if _all_tasks_succeeded(dispatch_result) else 1


def _resolve_workspace_home() -> Path:
    raw_home = os.environ.get("OMNIUS_HOME")
    if raw_home is None:
        return (Path.home() / ".omnius").expanduser()
    return Path(raw_home).expanduser()


def status_command(args: argparse.Namespace) -> int:
    try:
        snapshot = load_status_snapshot(_resolve_workspace_home())
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(snapshot.payload, indent=2, sort_keys=True))
    else:
        print(render_status_table(snapshot.payload))
    return 0


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
    workspace_home: Path,
    run_date: str,
    journal_dir: Path,
    local_task_entries: list[object],
    due_recurring_task_entries: list[object],
    default_task_budget_minutes: int,
    planner_plan_text: str,
) -> str:
    manifest_tasks = build_manifest_tasks(
        local_entries=local_task_entries,
        recurring_entries=due_recurring_task_entries,
        default_task_budget_minutes=default_task_budget_minutes,
    )
    summary = _build_manifest_summary(
        local_count=len(local_task_entries),
        recurring_count=len(due_recurring_task_entries),
    )
    payload = {
        "run_date": run_date,
        "journal_dir": str(journal_dir),
        "summary": summary,
        "tasks": manifest_tasks,
        "skipped": [],
        "notes": planner_plan_text,
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _build_manifest_summary(*, local_count: int, recurring_count: int) -> str:
    total_count = local_count + recurring_count
    if recurring_count == 0:
        return f"{total_count} task(s) planned from local queue"
    if local_count == 0:
        return f"{total_count} task(s) planned from recurring queue"
    return f"{total_count} task(s) planned from local and recurring queues"


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


def _all_tasks_succeeded(dispatch_result: dict[str, object]) -> bool:
    tasks = dispatch_result.get("tasks")
    if not isinstance(tasks, dict):
        return True
    for task_state in tasks.values():
        if not isinstance(task_state, dict):
            return False
        if task_state.get("status") != "SUCCESS":
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return int(handler(args))
