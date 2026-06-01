from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path


ATTENTION_STATUSES = {
    "NO_ARTIFACT",
    "PARTIAL",
    "BLOCKED",
    "FAILURE",
    "TIMEOUT",
    "CRASH",
}

SCHEDULER_LOGS = {
    "cron": "omnius-cron.log",
    "launchd_stdout": "omnius-launchd.log",
    "launchd_stderr": "omnius-launchd.err",
}


def summarize_logs(home: Path) -> dict[str, object]:
    latest = find_latest_journal(home)
    scheduler_logs = _scheduler_log_payloads(home, include_content=False)
    return {
        "ok": True,
        "home": str(home),
        "scheduler_logs": scheduler_logs,
        "latest_journal": _journal_summary(latest) if latest is not None else None,
        "hint": "Use `omnius logs cron`, `omnius logs dispatch`, `omnius logs worker <task_id>`, or `omnius logs errors`.",
    }


def collect_cron_logs(home: Path) -> dict[str, object]:
    return {
        "ok": True,
        "home": str(home),
        "logs": _scheduler_log_payloads(home, include_content=True),
    }


def load_latest_dispatch_log(home: Path) -> dict[str, object]:
    journal_dir = find_latest_journal(home)
    if journal_dir is None:
        return _error_payload("no_runs", f"No Omnius runs found under {home / 'journal'}")

    path = journal_dir / "dispatch_log.json"
    base = {
        "journal_dir": str(journal_dir),
        "path": str(path),
        "exists": path.exists(),
    }
    if not path.exists():
        return {
            **base,
            **_error_payload("missing_dispatch_log", f"Missing dispatch log: {path}"),
        }

    text = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return {
            **base,
            **_error_payload("malformed_dispatch_log", f"Malformed dispatch log at {path}: {exc.msg}"),
        }
    if not isinstance(payload, dict):
        return {
            **base,
            **_error_payload("malformed_dispatch_log", f"Dispatch log must be a JSON object: {path}"),
        }
    return {
        **base,
        "ok": True,
        "content": text,
        "dispatch_log": payload,
    }


def collect_worker_logs(home: Path, task_id: str) -> dict[str, object]:
    dispatch = load_latest_dispatch_log(home)
    if not dispatch.get("ok"):
        return {
            "ok": False,
            "task_id": task_id,
            "journal_dir": dispatch.get("journal_dir"),
            "error": dispatch.get("error"),
        }

    journal_dir = Path(str(dispatch["journal_dir"]))
    stdout = _artifact_payload(journal_dir / f"{task_id}_stdout.json", parse_json=True)
    stderr = _artifact_payload(journal_dir / f"{task_id}_stderr.log", parse_json=False)
    ok = bool(stdout["exists"] or stderr["exists"])
    result: dict[str, object] = {
        "ok": ok,
        "task_id": task_id,
        "journal_dir": str(journal_dir),
        "stdout": stdout,
        "stderr": stderr,
    }
    if not ok:
        result["error"] = {
            "code": "missing_worker_artifacts",
            "message": f"No stdout/stderr artifacts found for worker task {task_id} in {journal_dir}",
        }
    return result


def collect_error_summary(home: Path) -> dict[str, object]:
    scheduler_logs = _scheduler_log_payloads(home, include_content=True)
    dispatch = load_latest_dispatch_log(home)
    if not dispatch.get("ok"):
        return {
            "ok": False,
            "journal_dir": dispatch.get("journal_dir"),
            "error": dispatch.get("error"),
            "scheduler_logs": scheduler_logs,
            "tasks": [],
        }

    payload = dispatch.get("dispatch_log")
    tasks = _attention_tasks(payload if isinstance(payload, dict) else {})
    return {
        "ok": True,
        "journal_dir": dispatch["journal_dir"],
        "path": dispatch["path"],
        "scheduler_logs": scheduler_logs,
        "tasks": tasks,
        "count": len(tasks),
    }


def render_logs_summary(payload: dict[str, object]) -> str:
    lines = ["Omnius logs"]
    latest = payload.get("latest_journal")
    if isinstance(latest, dict):
        lines.append(f"Latest journal: {latest.get('path')}")
        lines.append(f"Dispatch log: {_exists_label(latest.get('dispatch_log_exists'))} {latest.get('dispatch_log_path')}")
    else:
        lines.append("Latest journal: none")
    lines.append("Scheduler logs:")
    lines.extend(_render_log_availability(payload.get("scheduler_logs")))
    lines.append(str(payload.get("hint")))
    return "\n".join(lines)


def render_cron_logs(payload: dict[str, object]) -> str:
    lines = ["Scheduler logs"]
    logs = payload.get("logs")
    if not isinstance(logs, dict):
        return "\n".join(lines)
    for name in SCHEDULER_LOGS:
        item = logs.get(name)
        if not isinstance(item, dict):
            continue
        lines.append(f"{name}: {_exists_label(item.get('exists'))} {item.get('path')}")
        content = item.get("content")
        if isinstance(content, str) and content:
            lines.extend(_indent(content.rstrip()).splitlines())
    return "\n".join(lines)


def render_dispatch_log(payload: dict[str, object]) -> str:
    if not payload.get("ok"):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message"))
        return "Unable to read latest dispatch log."
    content = payload.get("content")
    if isinstance(content, str):
        return content.rstrip()
    dispatch_log = payload.get("dispatch_log")
    return json.dumps(dispatch_log, indent=2, sort_keys=True)


def render_worker_logs(payload: dict[str, object]) -> str:
    lines = [f"Worker {payload.get('task_id')} logs"]
    lines.append(f"Journal: {payload.get('journal_dir')}")
    for key in ("stdout", "stderr"):
        item = payload.get(key)
        if not isinstance(item, dict):
            continue
        lines.append(f"{key}: {_exists_label(item.get('exists'))} {item.get('path')}")
        content = item.get("content")
        if isinstance(content, str) and content:
            lines.extend(_indent(content.rstrip()).splitlines())
    if not payload.get("ok"):
        error = payload.get("error")
        if isinstance(error, dict):
            lines.append(str(error.get("message")))
    return "\n".join(lines)


def render_error_summary(payload: dict[str, object]) -> str:
    lines = ["Omnius errors"]
    if not payload.get("ok"):
        error = payload.get("error")
        if isinstance(error, dict):
            lines.append(str(error.get("message")))
    else:
        lines.append(f"Journal: {payload.get('journal_dir')}")
        tasks = payload.get("tasks")
        if isinstance(tasks, list) and tasks:
            lines.append("Task attention:")
            for task in tasks:
                if isinstance(task, dict):
                    detail = f": {task.get('detail')}" if task.get("detail") else ""
                    lines.append(f"- {task.get('id')} {task.get('title')} [{task.get('status')}]{detail}")
        else:
            lines.append("Task attention: none")
    lines.append("Scheduler logs:")
    lines.extend(_render_log_availability(payload.get("scheduler_logs")))
    return "\n".join(lines)


def find_latest_journal(home: Path) -> Path | None:
    journal_root = home / "journal"
    if not journal_root.exists():
        return None
    candidates: list[tuple[datetime, str, Path]] = []
    for date_dir in journal_root.iterdir():
        if not date_dir.is_dir():
            continue
        for run_dir in date_dir.iterdir():
            if not run_dir.is_dir():
                continue
            dispatch_path = run_dir / "dispatch_log.json"
            timestamp = _journal_sort_timestamp(run_dir, dispatch_path)
            relative_key = run_dir.relative_to(journal_root).as_posix()
            candidates.append((timestamp, relative_key, run_dir))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def _journal_sort_timestamp(journal_dir: Path, dispatch_path: Path) -> datetime:
    if dispatch_path.exists():
        try:
            payload = json.loads(dispatch_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            pipeline = payload.get("pipeline")
            if isinstance(pipeline, dict):
                parsed = _parse_datetime(pipeline.get("started_at"))
                if parsed is not None:
                    return parsed
    parsed_from_path = _parse_datetime(f"{journal_dir.parent.name}T{_time_from_dir_name(journal_dir.name)}")
    return parsed_from_path or datetime.min.replace(tzinfo=timezone.utc)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _time_from_dir_name(name: str) -> str:
    base = name.split("-", 1)[0]
    if len(base) == 6 and base.isdigit():
        return f"{base[:2]}:{base[2:4]}:{base[4:]}"
    if len(base) == 4 and base.isdigit():
        return f"{base[:2]}:{base[2:]}:00"
    return "00:00:00"


def _journal_summary(journal_dir: Path) -> dict[str, object]:
    dispatch_path = journal_dir / "dispatch_log.json"
    return {
        "path": str(journal_dir),
        "dispatch_log_path": str(dispatch_path),
        "dispatch_log_exists": dispatch_path.exists(),
    }


def _scheduler_log_payloads(home: Path, *, include_content: bool) -> dict[str, object]:
    payload: dict[str, object] = {}
    for name, filename in SCHEDULER_LOGS.items():
        path = home / "logs" / filename
        item: dict[str, object] = {
            "path": str(path),
            "exists": path.exists(),
        }
        if include_content:
            item["content"] = path.read_text(encoding="utf-8") if path.exists() else None
        payload[name] = item
    return payload


def _artifact_payload(path: Path, *, parse_json: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "path": str(path),
        "exists": path.exists(),
        "content": None,
    }
    if not path.exists():
        return payload
    content = path.read_text(encoding="utf-8")
    payload["content"] = content
    if parse_json:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            payload["error"] = {
                "code": "malformed_worker_stdout",
                "message": f"Malformed worker stdout JSON at {path}: {exc.msg}",
            }
        else:
            payload["json"] = parsed
    return payload


def _attention_tasks(dispatch_log: dict[str, object]) -> list[dict[str, object]]:
    tasks = dispatch_log.get("tasks")
    if not isinstance(tasks, dict):
        return []
    rows: list[dict[str, object]] = []
    for task_id in sorted(tasks):
        task = tasks.get(task_id)
        if not isinstance(task, dict):
            continue
        status = task.get("status")
        if status not in ATTENTION_STATUSES and not task.get("error"):
            continue
        detail = task.get("error") or task.get("reason") or task.get("notes") or task.get("summary")
        rows.append(
            {
                "id": task.get("id") or task_id,
                "title": task.get("title"),
                "status": status,
                "detail": detail,
                "agent": task.get("agent"),
                "branch": task.get("branch"),
            }
        )
    return rows


def _error_payload(code: str, message: str) -> dict[str, object]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _render_log_availability(raw_logs: object) -> list[str]:
    if not isinstance(raw_logs, dict):
        return []
    lines: list[str] = []
    for name in SCHEDULER_LOGS:
        item = raw_logs.get(name)
        if isinstance(item, dict):
            lines.append(f"- {name}: {_exists_label(item.get('exists'))} {item.get('path')}")
    return lines


def _exists_label(value: object) -> str:
    return "present" if value else "missing"


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" for line in text.splitlines())
