from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import sys

from omnius.config import ConfigError, RepoConfig, load_config
from omnius.costs import SessionCostRecord, write_session_cost_record
from omnius.dayprep import run_dayprep
from omnius.dispatcher import dispatch_manifest, initialize_dispatch_log, update_dispatch_log
from omnius.install import InstallRequest, LifecycleRequest, run_doctor, run_install, run_uninstall
from omnius.logs import (
    collect_cron_logs,
    collect_error_summary,
    collect_worker_logs,
    load_latest_dispatch_log,
    render_cron_logs,
    render_dispatch_log,
    render_error_summary,
    render_logs_summary,
    render_worker_logs,
    summarize_logs,
)
from omnius.planner import (
    build_manifest_tasks,
    build_planner_prompt,
    choose_planner_response_with_metadata,
    load_planner_prompt_template,
    parse_planner_response,
    validate_manifest,
)
from omnius.prefetch import collect_prefetch_snapshot
from omnius.preflight import run_preflight
from omnius.runners import get_runner
from omnius.runtime import PipelineAlreadyRunning, acquire_pipeline_lock, recover_pipeline_lock, stop_pipeline
from omnius.status import find_brief, load_status_snapshot, render_attention, render_status_table
from omnius.tasks import (
    SUPPORTED_TASK_TYPES,
    TaskCommandEntry,
    add_local_task,
    complete_local_task,
    list_active_task_entries,
    list_pending_task_entries,
    list_recurring_command_entries,
    show_task_entry,
)
from omnius.workspace import bootstrap_workspace

_GITHUB_SOURCES_ENABLED = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omnius")
    subparsers = parser.add_subparsers(dest="command")

    install_parser = subparsers.add_parser(
        "install",
        help="Install or update the Omnius scheduler setup",
        description="Install or update the Omnius scheduler setup",
    )
    install_parser.add_argument(
        "--backend",
        choices=("cron", "launchd"),
        help="Override the scheduler backend for this install",
    )
    install_parser.add_argument(
        "--runner",
        choices=("codex", "claude"),
        help="Set the default runner when creating a new config",
    )
    install_parser.add_argument(
        "--repo-path",
        help="Set the primary repo path when creating a new config",
    )
    install_parser.add_argument(
        "--repo-slug",
        help="Set the primary repo slug when creating a new config",
    )
    install_parser.add_argument(
        "--repo-branch",
        help="Set the primary repo branch when creating a new config",
    )
    install_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Require flags or repo detection instead of prompting",
    )
    install_parser.set_defaults(handler=install_command)

    install_cron_parser = subparsers.add_parser(
        "install-cron",
        help="Install or update the Omnius cron schedule",
        description="Install or update the Omnius cron schedule",
    )
    _add_install_creation_arguments(install_cron_parser)
    install_cron_parser.set_defaults(handler=install_cron_command)

    install_launchd_parser = subparsers.add_parser(
        "install-launchd",
        help="Install or update the Omnius launchd schedule",
        description="Install or update the Omnius launchd schedule",
    )
    _add_install_creation_arguments(install_launchd_parser)
    install_launchd_parser.set_defaults(handler=install_launchd_command)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Show Omnius install and scheduler health",
        description="Show Omnius install and scheduler health",
    )
    doctor_parser.add_argument(
        "--backend",
        choices=("cron", "launchd"),
        help="Inspect a specific scheduler backend",
    )
    doctor_parser.set_defaults(handler=doctor_command)

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
    status_parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Show the latest run for a specific date",
    )
    status_parser.add_argument(
        "--brief",
        action="store_true",
        help="Print the selected run's daily brief",
    )
    status_parser.add_argument(
        "--attention",
        action="store_true",
        help="Print only attention items for the selected run",
    )
    status_parser.add_argument(
        "--session-start",
        action="store_true",
        help="Print status once per latest run for shell session hooks",
    )
    status_parser.set_defaults(handler=status_command)

    logs_parser = subparsers.add_parser(
        "logs",
        help="Show Omnius logs",
        description="Show Omnius logs",
    )
    _add_json_argument(logs_parser)
    logs_parser.set_defaults(handler=logs_command)
    logs_subparsers = logs_parser.add_subparsers(dest="logs_command")

    logs_cron_parser = logs_subparsers.add_parser(
        "cron",
        help="Show scheduler cron and launchd logs",
        description="Show scheduler cron and launchd logs",
    )
    _add_json_argument(logs_cron_parser)
    logs_cron_parser.set_defaults(handler=logs_cron_command)

    logs_dispatch_parser = logs_subparsers.add_parser(
        "dispatch",
        help="Show the latest dispatch log",
        description="Show the latest dispatch log",
    )
    _add_json_argument(logs_dispatch_parser)
    logs_dispatch_parser.set_defaults(handler=logs_dispatch_command)

    logs_worker_parser = logs_subparsers.add_parser(
        "worker",
        help="Show worker stdout/stderr artifacts for the latest run",
        description="Show worker stdout/stderr artifacts for the latest run",
    )
    logs_worker_parser.add_argument("task_id")
    _add_json_argument(logs_worker_parser)
    logs_worker_parser.set_defaults(handler=logs_worker_command)

    logs_errors_parser = logs_subparsers.add_parser(
        "errors",
        help="Show latest task errors and scheduler stderr availability",
        description="Show latest task errors and scheduler stderr availability",
    )
    _add_json_argument(logs_errors_parser)
    logs_errors_parser.set_defaults(handler=logs_errors_command)

    stop_parser = subparsers.add_parser(
        "stop",
        help="Stop a running Omnius pipeline",
        description="Stop a running Omnius pipeline",
    )
    stop_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the running pipeline without signaling it",
    )
    stop_parser.add_argument(
        "--force",
        action="store_true",
        help="Signal the active worker and pipeline process, then remove the runtime lock",
    )
    stop_parser.set_defaults(handler=stop_command)

    recover_parser = subparsers.add_parser(
        "recover",
        help="Recover from a stale Omnius runtime lock",
        description="Recover from a stale Omnius runtime lock",
    )
    recover_parser.set_defaults(handler=recover_command)

    task_parser = subparsers.add_parser(
        "task",
        help="Manage Omnius local tasks",
        description="Manage Omnius local tasks",
    )
    task_parser.set_defaults(handler=lambda _args, parser=task_parser: _print_parser_help(parser))
    task_subparsers = task_parser.add_subparsers(dest="task_command")

    task_list_parser = task_subparsers.add_parser(
        "list",
        help="List active local tasks",
        description="List active local tasks",
    )
    _add_json_argument(task_list_parser)
    task_list_parser.set_defaults(handler=task_list_command)

    task_show_parser = task_subparsers.add_parser(
        "show",
        help="Show one task by ID",
        description="Show one task by ID",
    )
    task_show_parser.add_argument("task_id", metavar="id")
    _add_json_argument(task_show_parser)
    task_show_parser.set_defaults(handler=task_show_command)

    task_add_parser = task_subparsers.add_parser(
        "add",
        help="Add an active local task",
        description="Add an active local task",
    )
    task_add_parser.add_argument("--title", required=True, help="Task title")
    task_add_parser.add_argument("--repo", required=True, help="Configured repo slug")
    task_add_parser.add_argument("--body", required=True, help="Task markdown body")
    task_add_parser.add_argument("--agent", choices=("codex", "claude"), help="Optional task runner override")
    task_add_parser.add_argument("--type", choices=SUPPORTED_TASK_TYPES, default="implementation", help="Task type")
    task_add_parser.add_argument("--max-time", type=int, help="Maximum task runtime in minutes")
    _add_json_argument(task_add_parser)
    task_add_parser.set_defaults(handler=task_add_command)

    task_complete_parser = task_subparsers.add_parser(
        "complete",
        help="Move an active local task to completed",
        description="Move an active local task to completed",
    )
    task_complete_parser.add_argument("task_id", metavar="id")
    _add_json_argument(task_complete_parser)
    task_complete_parser.set_defaults(handler=task_complete_command)

    task_pending_parser = task_subparsers.add_parser(
        "pending",
        help="List pending-approval tasks",
        description="List pending-approval tasks",
    )
    _add_json_argument(task_pending_parser)
    task_pending_parser.set_defaults(handler=task_pending_command)

    task_recurring_parser = task_subparsers.add_parser(
        "recurring",
        help="List recurring tasks",
        description="List recurring tasks",
    )
    _add_json_argument(task_recurring_parser)
    task_recurring_parser.set_defaults(handler=task_recurring_command)

    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help="Remove Omnius-managed scheduler setup",
        description="Remove Omnius-managed scheduler setup",
    )
    uninstall_parser.add_argument(
        "--backend",
        choices=("cron", "launchd"),
        help="Remove a specific scheduler backend",
    )
    uninstall_parser.set_defaults(handler=uninstall_command)
    return parser


def _print_parser_help(parser: argparse.ArgumentParser) -> int:
    parser.print_help()
    return 0


def _add_json_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON",
    )


def install_command(args: argparse.Namespace) -> int:
    request = _build_install_request(args)
    return run_install(request=request, workspace_home=_resolve_workspace_home(), cwd=Path.cwd())


def install_cron_command(args: argparse.Namespace) -> int:
    request = _build_install_request(args, backend="cron")
    return run_install(request=request, workspace_home=_resolve_workspace_home(), cwd=Path.cwd())


def install_launchd_command(args: argparse.Namespace) -> int:
    request = _build_install_request(args, backend="launchd")
    return run_install(request=request, workspace_home=_resolve_workspace_home(), cwd=Path.cwd())


def _build_install_request(args: argparse.Namespace, backend: str | None = None) -> InstallRequest:
    selected_backend = backend if backend is not None else args.backend
    return InstallRequest(
        backend=selected_backend,
        runner=args.runner,
        repo_path=args.repo_path,
        repo_slug=args.repo_slug,
        repo_branch=args.repo_branch,
        non_interactive=bool(args.non_interactive),
    )


def _add_install_creation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--runner",
        choices=("codex", "claude"),
        help="Set the default runner when creating a new config",
    )
    parser.add_argument(
        "--repo-path",
        help="Set the primary repo path when creating a new config",
    )
    parser.add_argument(
        "--repo-slug",
        help="Set the primary repo slug when creating a new config",
    )
    parser.add_argument(
        "--repo-branch",
        help="Set the primary repo branch when creating a new config",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Require flags or repo detection instead of prompting",
    )

def doctor_command(args: argparse.Namespace) -> int:
    return run_doctor(
        request=LifecycleRequest(backend=args.backend),
        workspace_home=_resolve_workspace_home(),
    )


def run_command(_args: argparse.Namespace) -> int:
    workspace_home = _resolve_workspace_home()
    workspace_paths = bootstrap_workspace(workspace_home)
    try:
        config = load_config(workspace_home / "omnius.toml")
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    runner = get_runner(config.runner.default, planner_dayprep_mode=config.runner.planner_dayprep_mode)

    run_started_at = datetime.now().astimezone()
    run_date = run_started_at.strftime("%Y-%m-%d")
    journal_dir = _allocate_journal_dir(workspace_paths.journal_dir, run_started_at)
    pipeline_id = run_started_at.strftime("pipeline-%Y%m%d-%H%M%S")
    primary_repo = config.repos[0] if config.repos else None

    dispatch_log_path = journal_dir / "dispatch_log.json"
    try:
        pipeline_lock = acquire_pipeline_lock(
            state_dir=workspace_home / "state",
            pipeline_id=pipeline_id,
            journal_dir=journal_dir,
            runner_name=runner.name,
        )
    except PipelineAlreadyRunning as exc:
        print(str(exc), file=sys.stderr)
        return 1

    previous_signal_handlers = _install_runtime_signal_handlers(pipeline_lock)
    initialize_dispatch_log(
        dispatch_log_path,
        pipeline_id=pipeline_id,
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
        exit_code = _finalize_pipeline_failure(
            dispatch_log_path,
            abort_reason="config",
            error="Config must define at least one repo for 'omnius run'",
        )
        _release_runtime_lock(pipeline_lock, previous_signal_handlers)
        return exit_code

    try:
        preflight = run_preflight(
            runner=runner,
            repo_path=Path(primary_repo.path).expanduser(),
            capability_policy=_capability_policy(config),
            check_github=_github_checks_enabled(),
            check_repo_state=True,
            check_filesystem=True,
            check_disk=True,
            workspace_home=workspace_home,
            journal_dir=journal_dir,
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
            _release_runtime_lock(pipeline_lock, previous_signal_handlers)
            return 1

        prefetch_snapshot = collect_prefetch_snapshot(
            workspace_home,
            today=run_started_at.date(),
        )
        update_dispatch_log(
            dispatch_log_path,
            patch={
                "snapshot": {
                    "pending_approval_count": len(prefetch_snapshot.pending_approval_filenames),
                }
            },
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
        planner_selection = choose_planner_response_with_metadata(
            planner_output=planner_invocation.plan_text,
            fallback_manifest_response=synthesized_planner_response,
        )
        planner_response = planner_selection.response_text
        (journal_dir / "planner_response.json").write_text(planner_response, encoding="utf-8")

        manifest = parse_planner_response(planner_response)
        validate_manifest(manifest, allowed_repo_slugs={repo.slug for repo in config.repos})
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
                    "command": planner_invocation.command,
                    "returncode": planner_invocation.returncode,
                    "used_runner_output": planner_selection.used_runner_output,
                    "fallback_reason": planner_selection.fallback_reason,
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
            worker_observer=lambda pid, pgid: pipeline_lock.update_worker(
                active_worker_pid=pid,
                active_worker_pgid=pgid,
            ),
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
                    "command": dayprep_result.command,
                    "returncode": dayprep_result.returncode,
                }
            },
        )
    except Exception as exc:
        exit_code = _finalize_pipeline_failure(
            dispatch_log_path,
            abort_reason="pipeline_error",
            error=str(exc),
        )
        _release_runtime_lock(pipeline_lock, previous_signal_handlers)
        return exit_code

    update_dispatch_log(
        dispatch_log_path,
        patch={
            "pipeline": {
                "status": "completed",
                "ended_at": datetime.now().astimezone().isoformat(),
            },
        },
    )
    exit_code = 0 if _all_tasks_succeeded(dispatch_result) else 1
    _release_runtime_lock(pipeline_lock, previous_signal_handlers)
    return exit_code


def _resolve_workspace_home() -> Path:
    raw_home = os.environ.get("OMNIUS_HOME")
    if raw_home is None:
        return (Path.home() / ".omnius").expanduser()
    return Path(raw_home).expanduser()


def status_command(args: argparse.Namespace) -> int:
    workspace_home = _resolve_workspace_home()
    if args.session_start:
        if os.environ.get("OMNIUS_DISABLE") == "1" or os.environ.get("OMNIUS_WORKER") == "1":
            return 0
        try:
            snapshot = load_status_snapshot(workspace_home, run_date=args.date)
        except FileNotFoundError as exc:
            if args.date:
                print(str(exc), file=sys.stderr)
                return 1
            return 0
        except (ValueError, json.JSONDecodeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if _session_start_seen(workspace_home, snapshot.journal_dir):
            return 0
        exit_code = _emit_session_start_snapshot(args, snapshot)
        if exit_code == 0:
            _record_session_start_seen(workspace_home, snapshot.journal_dir)
        return exit_code

    try:
        snapshot = load_status_snapshot(workspace_home, run_date=args.date)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return _emit_status_snapshot(args, snapshot)


def _emit_status_snapshot(args: argparse.Namespace, snapshot) -> int:
    if args.attention:
        attention = snapshot.payload.get("attention", [])
        if not isinstance(attention, list):
            attention = []
        if args.json:
            print(json.dumps(attention, indent=2, sort_keys=True))
        else:
            print(render_attention(snapshot.payload))
        return 0

    if args.brief:
        brief = find_brief(snapshot.payload)
        if not brief.get("exists"):
            print(f"No daily brief found for {snapshot.journal_dir}", file=sys.stderr)
            return 1
        if args.json:
            payload = dict(snapshot.payload)
            payload["brief"] = brief
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            content = brief.get("content")
            print(content if isinstance(content, str) else "", end="")
        return 0

    if args.json:
        print(json.dumps(snapshot.payload, indent=2, sort_keys=True))
    else:
        print(render_status_table(snapshot.payload))
    return 0


def _emit_session_start_snapshot(args: argparse.Namespace, snapshot) -> int:
    if args.json:
        print(json.dumps(snapshot.payload, indent=2, sort_keys=True))
    else:
        print(render_status_table(snapshot.payload))
    return 0


def _session_start_cache_path(workspace_home: Path) -> Path:
    return workspace_home / "state" / "session_start_seen.json"


def _session_start_seen(workspace_home: Path, journal_dir: Path) -> bool:
    cache = _read_session_start_cache(_session_start_cache_path(workspace_home))
    seen_journals = cache.get("seen_journals", {})
    return isinstance(seen_journals, dict) and str(journal_dir) in seen_journals


def _record_session_start_seen(workspace_home: Path, journal_dir: Path) -> None:
    cache_path = _session_start_cache_path(workspace_home)
    cache = _read_session_start_cache(cache_path)
    seen_journals = cache.get("seen_journals", {})
    if not isinstance(seen_journals, dict):
        seen_journals = {}
    seen_journals[str(journal_dir)] = datetime.now().astimezone().isoformat()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(cache_path, {"seen_journals": seen_journals})


def _read_session_start_cache(cache_path: Path) -> dict[str, object]:
    if not cache_path.exists():
        return {"seen_journals": {}}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError):
        return {"seen_journals": {}}
    if not isinstance(payload, dict):
        return {"seen_journals": {}}
    return payload


def logs_command(args: argparse.Namespace) -> int:
    payload = summarize_logs(_bootstrap_logs_workspace())
    return _emit_logs_payload(payload, json_output=bool(args.json), renderer=render_logs_summary)


def logs_cron_command(args: argparse.Namespace) -> int:
    payload = collect_cron_logs(_bootstrap_logs_workspace())
    return _emit_logs_payload(payload, json_output=bool(args.json), renderer=render_cron_logs)


def logs_dispatch_command(args: argparse.Namespace) -> int:
    payload = load_latest_dispatch_log(_bootstrap_logs_workspace())
    if args.json and payload.get("ok"):
        print(json.dumps(payload["dispatch_log"], indent=2, sort_keys=True))
        return 0
    return _emit_logs_payload(payload, json_output=bool(args.json), renderer=render_dispatch_log)


def logs_worker_command(args: argparse.Namespace) -> int:
    payload = collect_worker_logs(_bootstrap_logs_workspace(), args.task_id)
    return _emit_logs_payload(payload, json_output=bool(args.json), renderer=render_worker_logs)


def logs_errors_command(args: argparse.Namespace) -> int:
    payload = collect_error_summary(_bootstrap_logs_workspace())
    return _emit_logs_payload(payload, json_output=bool(args.json), renderer=render_error_summary)


def stop_command(args: argparse.Namespace) -> int:
    result = stop_pipeline(
        state_dir=_resolve_workspace_home() / "state",
        dry_run=bool(args.dry_run),
        force=bool(args.force),
    )
    message = _render_stop_result(result.status, result.payload, result.removed_lock)
    if result.status == "force_required":
        print(message, file=sys.stderr)
        return 1
    print(message)
    return 0


def recover_command(_args: argparse.Namespace) -> int:
    result = recover_pipeline_lock(state_dir=_resolve_workspace_home() / "state")
    print(_render_recover_result(result.status, result.payload, result.removed_lock))
    return 0


def task_list_command(args: argparse.Namespace) -> int:
    return _emit_task_entries(
        lambda home: list_active_task_entries(home),
        json_output=bool(args.json),
        empty_message="No active tasks.",
    )


def task_show_command(args: argparse.Namespace) -> int:
    try:
        entry = show_task_entry(_bootstrap_task_workspace(), args.task_id)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(entry.as_payload(), indent=2, sort_keys=True))
    else:
        print(_render_task_detail(entry))
    return 0


def task_add_command(args: argparse.Namespace) -> int:
    try:
        entry = add_local_task(
            home=_bootstrap_task_workspace(),
            title=args.title,
            repo_slug=args.repo,
            body=args.body,
            agent=args.agent,
            task_type=args.type,
            max_time_minutes=args.max_time,
        )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(entry.as_payload(), indent=2, sort_keys=True))
    else:
        print(f"Added {entry.task_id}: {entry.title} [file: {entry.filename}]")
    return 0


def task_complete_command(args: argparse.Namespace) -> int:
    try:
        entry = complete_local_task(home=_bootstrap_task_workspace(), task_id=args.task_id)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(entry.as_payload(), indent=2, sort_keys=True))
    else:
        print(f"Completed {entry.task_id}: {entry.title} [file: {entry.filename}]")
    return 0


def task_pending_command(args: argparse.Namespace) -> int:
    return _emit_task_entries(
        lambda home: list_pending_task_entries(home),
        json_output=bool(args.json),
        empty_message="No pending tasks.",
    )


def task_recurring_command(args: argparse.Namespace) -> int:
    return _emit_task_entries(
        lambda home: list_recurring_command_entries(home),
        json_output=bool(args.json),
        empty_message="No recurring tasks.",
    )


def uninstall_command(args: argparse.Namespace) -> int:
    return run_uninstall(
        request=LifecycleRequest(backend=args.backend),
        workspace_home=_resolve_workspace_home(),
    )


def _bootstrap_task_workspace() -> Path:
    return bootstrap_workspace(_resolve_workspace_home()).home


def _bootstrap_logs_workspace() -> Path:
    return bootstrap_workspace(_resolve_workspace_home()).home


def _emit_logs_payload(
    payload: dict[str, object],
    *,
    json_output: bool,
    renderer: Callable[[dict[str, object]], str],
) -> int:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        rendered = renderer(payload)
        if payload.get("ok"):
            print(rendered)
        else:
            print(rendered, file=sys.stderr)
    return 0 if payload.get("ok") else 1


def _emit_task_entries(
    loader: Callable[[Path], list[TaskCommandEntry]],
    *,
    json_output: bool,
    empty_message: str,
) -> int:
    try:
        entries = loader(_bootstrap_task_workspace())
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if json_output:
        print(json.dumps([entry.as_payload() for entry in entries], indent=2, sort_keys=True))
    elif entries:
        print("\n".join(_render_task_summary_line(entry) for entry in entries))
    else:
        print(empty_message)
    return 0


def _render_task_summary_line(entry: TaskCommandEntry) -> str:
    return f"{entry.task_id}  {entry.title}  [{entry.status}]  {entry.filename}"


def _render_task_detail(entry: TaskCommandEntry) -> str:
    lines = [
        f"Task {entry.task_id} ({entry.status})",
        f"Title: {entry.title}",
        f"File: {entry.path}",
    ]
    for key, label in (
        ("repo", "Repo"),
        ("agent", "Agent"),
        ("type", "Type"),
        ("max_time_minutes", "Max Time"),
        ("schedule", "Schedule"),
        ("completed_on", "Completed On"),
    ):
        value = entry.metadata.get(key)
        if value is not None:
            lines.append(f"{label}: {value}")
    lines.extend(["", "Body:", entry.body.rstrip()])
    return "\n".join(lines)


def _allocate_journal_dir(journal_root: Path, run_started_at: datetime) -> Path:
    date_dir = journal_root / run_started_at.strftime("%Y-%m-%d")
    base_name = run_started_at.strftime("%H%M%S")
    candidate = date_dir / base_name
    suffix = 1
    while candidate.exists():
        candidate = date_dir / f"{base_name}-{suffix:02d}"
        suffix += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def _capability_policy(config: object) -> dict[str, str]:
    capabilities = getattr(config, "capabilities", None)
    if capabilities is None or not hasattr(capabilities, "as_dict"):
        return {}
    return dict(capabilities.as_dict())


def _github_checks_enabled() -> bool:
    # GitHub-backed planner inputs are not wired yet in main; keep local runs local-only.
    return _GITHUB_SOURCES_ENABLED


def _render_repos_table(repos: list[RepoConfig]) -> str:
    if not repos:
        return "<none>"
    return "\n".join(f"{repo.slug} | {repo.path} | {repo.branch} | {repo.role}" for repo in repos)


def _install_runtime_signal_handlers(pipeline_lock: object) -> dict[int, object]:
    previous_handlers: dict[int, object] = {}

    def handle_signal(signum: int, _frame: object) -> None:
        release = getattr(pipeline_lock, "release", None)
        if callable(release):
            release()
        previous = previous_handlers.get(signum)
        if callable(previous):
            previous(signum, _frame)
            return
        raise SystemExit(128 + signum)

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[int(signum)] = signal.getsignal(signum)
        signal.signal(signum, handle_signal)
    return previous_handlers


def _release_runtime_lock(pipeline_lock: object, previous_signal_handlers: dict[int, object]) -> None:
    release = getattr(pipeline_lock, "release", None)
    if callable(release):
        release()
    for signum, previous in previous_signal_handlers.items():
        signal.signal(signum, previous)


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
        "version": 2,
        "created_at": datetime.now().astimezone().isoformat(),
        "mode": "fallback_synthesized",
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


def _render_stop_result(status: str, payload: dict[str, object] | None, removed_lock: bool) -> str:
    if payload is None:
        return "No Omnius pipeline lock found."
    label = _runtime_lock_label(payload)
    if status == "running":
        return f"Omnius pipeline is running: {label}"
    if status == "stale":
        return f"Omnius pipeline lock is stale: {label}"
    if status == "signaled":
        suffix = " Removed runtime lock." if removed_lock else ""
        return f"Signaled Omnius pipeline: {label}.{suffix}"
    if status == "stale_removed":
        return f"Removed stale Omnius pipeline lock: {label}"
    if status == "force_required":
        return f"Omnius pipeline is running: {label}. Re-run with --force to stop it."
    return f"Omnius pipeline state: {status}: {label}"


def _render_recover_result(status: str, payload: dict[str, object] | None, removed_lock: bool) -> str:
    if payload is None:
        return "No Omnius pipeline lock found."
    label = _runtime_lock_label(payload)
    if status == "running":
        return f"Omnius pipeline is still running: {label}"
    if status == "stale_removed":
        suffix = " Removed runtime lock." if removed_lock else ""
        return f"Recovered stale Omnius pipeline lock: {label}.{suffix}"
    return f"Omnius pipeline recovery state: {status}: {label}"


def _runtime_lock_label(payload: dict[str, object]) -> str:
    return (
        f"pipeline_id={payload.get('pipeline_id', '<unknown>')} "
        f"pid={payload.get('pid', '<unknown>')} "
        f"journal={payload.get('journal_dir', '<unknown>')}"
    )


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
