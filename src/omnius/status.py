from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path


_ATTENTION_STATUSES = {
    "NO_ARTIFACT",
    "PARTIAL",
    "BLOCKED",
    "FAILURE",
    "TIMEOUT",
    "CRASH",
}


@dataclass(frozen=True)
class StatusSnapshot:
    journal_dir: Path
    run_date: str
    pipeline_status: str
    payload: dict[str, object]


def load_status_snapshot(home: Path) -> StatusSnapshot:
    journal_dir = resolve_latest_journal(home)
    payload = build_status_payload(journal_dir)
    pipeline = payload.get("pipeline", {})
    if not isinstance(pipeline, dict):
        pipeline = {}
    return StatusSnapshot(
        journal_dir=journal_dir,
        run_date=str(payload.get("date") or ""),
        pipeline_status=str(pipeline.get("status") or "<unknown>"),
        payload=payload,
    )


def resolve_latest_journal(home: Path) -> Path:
    journal_root = home / "journal"
    candidates: list[tuple[datetime, str, Path]] = []
    for dispatch_log_path in journal_root.rglob("dispatch_log.json"):
        payload = _read_optional_json(dispatch_log_path)
        if not isinstance(payload, dict):
            continue
        pipeline = payload.get("pipeline", {})
        if not isinstance(pipeline, dict):
            pipeline = {}
        started_at = _parse_started_at(pipeline.get("started_at"))
        relative_key = dispatch_log_path.parent.relative_to(journal_root).as_posix()
        candidates.append((started_at, relative_key, dispatch_log_path.parent))

    if not candidates:
        raise FileNotFoundError(f"No Omnius runs found under {journal_root}")
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def build_status_payload(journal_dir: Path) -> dict[str, object]:
    dispatch_log = _read_required_json(journal_dir / "dispatch_log.json")
    manifest = _read_optional_json(journal_dir / "manifest.json")
    preflight = _read_optional_json(journal_dir / "preflight.json")
    pipeline = dispatch_log.get("pipeline", {})
    if not isinstance(pipeline, dict):
        pipeline = {}

    task_rows = _render_task_rows(dispatch_log)
    payload: dict[str, object] = {
        "date": pipeline.get("run_date"),
        "journal_dir": str(journal_dir),
        "summary": _optional_manifest_string(manifest, "summary"),
        "notes": _optional_manifest_string(manifest, "notes"),
        "pipeline": pipeline,
        "preflight": preflight,
        "dayprep": dispatch_log.get("dayprep"),
        "tasks": task_rows,
        "attention": _collect_attention(task_rows),
        "skipped": _collect_skipped(journal_dir=journal_dir, dispatch_log=dispatch_log, manifest=manifest),
    }
    return payload


def render_status_table(payload: dict[str, object]) -> str:
    pipeline = payload.get("pipeline", {})
    if not isinstance(pipeline, dict):
        pipeline = {}
    skipped = payload.get("skipped", {})
    if not isinstance(skipped, dict):
        skipped = {}
    attention = payload.get("attention", [])
    if not isinstance(attention, list):
        attention = []
    tasks = payload.get("tasks", [])
    if not isinstance(tasks, list):
        tasks = []

    lines = [
        f"Run: {_render_run_label(payload)}",
        f"Pipeline: {pipeline.get('status', '<unknown>')}",
        f"Runner: {pipeline.get('runner', '<unknown>')}",
        f"Repo: {pipeline.get('repo_slug', '<unknown>')} @ {pipeline.get('branch', '<unknown>')}",
        f"Tasks: total={len(tasks)} success={_count_tasks(tasks, 'SUCCESS')} attention={len(attention)} skipped={skipped.get('total', 0)}",
    ]
    summary = payload.get("summary")
    if isinstance(summary, str) and summary:
        lines.append(f"Summary: {summary}")
    notes = payload.get("notes")
    if isinstance(notes, str) and notes:
        lines.append(f"Notes: {notes}")

    preflight = payload.get("preflight")
    if isinstance(preflight, dict):
        lines.append(f"Preflight: {'ok' if preflight.get('ok') else 'blocked'}")

    dayprep = payload.get("dayprep")
    if isinstance(dayprep, dict):
        mode = "fallback" if dayprep.get("used_fallback") else "compiled"
        lines.append(f"Day Prep: {mode}")

    if attention:
        lines.append("Attention:")
        lines.extend(_render_attention_lines(attention))
    else:
        lines.append("Attention: none")

    lines.append(
        "Skipped: "
        f"pending_approval={skipped.get('pending_approval', 0)} "
        f"manifest={skipped.get('manifest', 0)} "
        f"budget_exhausted={skipped.get('budget_exhausted', 0)} "
        f"circuit_breaker_skipped={skipped.get('circuit_breaker_skipped', 0)}"
    )
    return "\n".join(lines)


def _read_required_json(path: Path) -> dict[str, object]:
    payload = _read_optional_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object at {path}")
    return payload


def _read_optional_json(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    payload = json.loads(text)
    if not isinstance(payload, dict):
        return None
    return payload


def _render_task_rows(dispatch_log: dict[str, object]) -> list[dict[str, object]]:
    tasks = dispatch_log.get("tasks", {})
    if not isinstance(tasks, dict):
        return []

    rows: list[dict[str, object]] = []
    for task_id in sorted(tasks):
        task_state = tasks.get(task_id)
        if not isinstance(task_state, dict):
            continue
        row = {
            "id": task_state.get("id", task_id),
            "title": task_state.get("title"),
            "status": task_state.get("status"),
            "repo_slug": task_state.get("repo_slug"),
            "agent": task_state.get("agent"),
            "branch": task_state.get("branch"),
            "duration_seconds": task_state.get("duration_seconds"),
        }
        for key in ("summary", "notes", "reason", "error", "pr_url", "cost_usd", "turns", "tokens", "start", "end"):
            value = task_state.get(key)
            if value is not None:
                row[key] = value
        rows.append(row)
    return rows


def _collect_attention(task_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [row for row in task_rows if row.get("status") in _ATTENTION_STATUSES]


def _collect_skipped(
    *,
    journal_dir: Path,
    dispatch_log: dict[str, object],
    manifest: dict[str, object] | None,
) -> dict[str, int]:
    tasks = dispatch_log.get("tasks", {})
    if not isinstance(tasks, dict):
        tasks = {}
    manifest_skipped = manifest.get("skipped") if isinstance(manifest, dict) else []
    if not isinstance(manifest_skipped, list):
        manifest_skipped = []

    pending_approval = _pending_approval_count_from_dispatch_log(dispatch_log)
    budget_exhausted = _count_task_statuses(tasks, "BUDGET_EXHAUSTED")
    circuit_breaker_skipped = _count_task_statuses(tasks, "CIRCUIT_BREAKER_SKIPPED")
    payload = {
        "pending_approval": pending_approval,
        "manifest": len(manifest_skipped),
        "budget_exhausted": budget_exhausted,
        "circuit_breaker_skipped": circuit_breaker_skipped,
    }
    payload["total"] = sum(payload.values())
    return payload


def _count_task_statuses(tasks: dict[str, object], status: str) -> int:
    return sum(1 for task_state in tasks.values() if isinstance(task_state, dict) and task_state.get("status") == status)


def _count_tasks(task_rows: list[object], status: str) -> int:
    return sum(1 for row in task_rows if isinstance(row, dict) and row.get("status") == status)


def _render_attention_lines(attention: list[object]) -> list[str]:
    lines: list[str] = []
    for row in attention:
        if not isinstance(row, dict):
            continue
        detail = row.get("summary") or row.get("notes") or row.get("reason") or row.get("error") or ""
        suffix = f": {detail}" if detail else ""
        status_label = str(row.get("status", "<unknown>"))
        agent = row.get("agent")
        if isinstance(agent, str) and agent:
            status_label = f"{status_label} via {agent}"
        lines.append(f"- {row.get('id', '<unknown>')} {row.get('title', '<untitled>')} [{status_label}]{suffix}")
    return lines


def _render_run_label(payload: dict[str, object]) -> str:
    journal_dir = payload.get("journal_dir")
    run_date = payload.get("date") or "<unknown>"
    if not isinstance(journal_dir, str):
        return str(run_date)
    path = Path(journal_dir)
    return f"{run_date} {path.name}"


def _optional_manifest_string(manifest: dict[str, object] | None, key: str) -> str | None:
    if not isinstance(manifest, dict):
        return None
    value = manifest.get(key)
    if not isinstance(value, str):
        return None
    return value


def _parse_started_at(raw_value: object) -> datetime:
    if isinstance(raw_value, str):
        try:
            return datetime.fromisoformat(raw_value)
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


def _pending_approval_count_from_dispatch_log(dispatch_log: dict[str, object]) -> int:
    snapshot = dispatch_log.get("snapshot", {})
    if not isinstance(snapshot, dict):
        return 0
    raw_value = snapshot.get("pending_approval_count", 0)
    if type(raw_value) is not int or raw_value < 0:
        return 0
    return raw_value
