from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from omnius.tasks import RecurringTaskEntry


_EVERY_N_DAYS_RE = re.compile(r"^every:(?P<days>\d+)d$")
_WEEKLY_RE = re.compile(r"^weekly:(?P<weekday>mon|tue|wed|thu|fri|sat|sun)$")
_MONTHLY_RE = re.compile(r"^monthly:(?P<day>\d{1,2})$")
_WEEKDAY_NAMES = (
    "mon",
    "tue",
    "wed",
    "thu",
    "fri",
    "sat",
    "sun",
)


def load_recurring_state(
    home: Path,
    *,
    quarantine_corrupt: bool = False,
    quarantined_at: datetime | None = None,
) -> dict[str, dict[str, object]]:
    state_path = _state_path(home)
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        if not quarantine_corrupt:
            raise ValueError("Recurring state file is not valid JSON") from exc
        _quarantine_corrupt_state(state_path, quarantined_at=quarantined_at)
        return {}

    if not isinstance(payload, dict):
        raise ValueError("Recurring state file must contain a JSON object")
    return payload


def save_recurring_state(home: Path, state: dict[str, dict[str, object]]) -> None:
    state_path = _state_path(home)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=state_path.parent,
        prefix=f"{state_path.name}.",
        suffix=".tmp",
        text=True,
    )
    os.close(descriptor)
    temp_path = Path(temp_name)
    temp_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp_path, state_path)


def record_recurring_task_result(
    home: Path,
    *,
    task_id: str,
    run_date: date,
    status: str,
    max_consecutive_failures: int,
) -> None:
    state = load_recurring_state(home)
    state_entry = dict(state.get(task_id, {}))
    state_entry["last_attempted"] = run_date.isoformat()
    state_entry["last_status"] = status

    if status == "SUCCESS":
        state_entry["last_succeeded"] = run_date.isoformat()
        state_entry["consecutive_failures"] = 0
        state_entry.pop("quarantined_until", None)
    else:
        consecutive_failures = _parse_consecutive_failures(state_entry) + 1
        state_entry["consecutive_failures"] = consecutive_failures
        if consecutive_failures >= max_consecutive_failures:
            state_entry["quarantined_until"] = (run_date + timedelta(days=7)).isoformat()
        else:
            state_entry.pop("quarantined_until", None)

    state[task_id] = state_entry
    save_recurring_state(home, state)


def filter_due_recurring_task_entries(
    entries: list[RecurringTaskEntry],
    state: dict[str, dict[str, object]],
    *,
    today: date,
) -> list[RecurringTaskEntry]:
    return [entry for entry in entries if _is_due(entry, state.get(entry.task_id), today=today)]


def _state_path(home: Path) -> Path:
    return home / "state" / "recurring_state.json"


def _quarantine_corrupt_state(state_path: Path, *, quarantined_at: datetime | None) -> None:
    timestamp = (quarantined_at or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suspect_path = state_path.with_name(f"{state_path.name}.suspect.{timestamp}")
    shutil.move(str(state_path), str(suspect_path))
    state_path.write_text("{}", encoding="utf-8")


def _is_due(entry: RecurringTaskEntry, state_entry: dict[str, object] | None, *, today: date) -> bool:
    last_attempted_on = _parse_state_datetime(state_entry, "last_attempted")
    _parse_state_datetime(state_entry, "last_succeeded")
    last_status = _parse_state_status(state_entry, "last_status")
    quarantined_until = _parse_state_datetime(state_entry, "quarantined_until")

    if quarantined_until is not None and quarantined_until > today:
        return False

    if entry.only_if_last_succeeded and state_entry is not None and last_status != "SUCCESS":
        return False

    if last_attempted_on == today:
        return entry.retry_on_failure == "immediate" and last_status == "FAILURE"

    schedule = entry.schedule.strip().lower()

    if schedule == "daily":
        return True

    if schedule == "daily:weekdays":
        return today.weekday() < 5

    weekly_match = _WEEKLY_RE.fullmatch(schedule)
    if weekly_match is not None:
        weekday_name = weekly_match.group("weekday")
        if today.weekday() != _WEEKDAY_NAMES.index(weekday_name):
            return False
        return last_attempted_on is None or last_attempted_on.isocalendar()[:2] != today.isocalendar()[:2]

    monthly_match = _MONTHLY_RE.fullmatch(schedule)
    if monthly_match is not None:
        day_of_month = int(monthly_match.group("day"))
        if today.day != day_of_month:
            return False
        return last_attempted_on is None or (last_attempted_on.year, last_attempted_on.month) != (today.year, today.month)

    every_n_days_match = _EVERY_N_DAYS_RE.fullmatch(schedule)
    if every_n_days_match is not None:
        interval_days = int(every_n_days_match.group("days"))
        return last_attempted_on is None or (today - last_attempted_on).days >= interval_days

    raise ValueError(f"Unsupported recurring schedule: {entry.schedule}")


def _parse_state_datetime(state_entry: dict[str, object] | None, key: str) -> date | None:
    if state_entry is None or key not in state_entry or state_entry[key] in (None, ""):
        return None
    value = state_entry[key]
    if not isinstance(value, str):
        raise ValueError(f"Recurring state field '{key}' must be a date string")
    return date.fromisoformat(value)


def _parse_state_status(state_entry: dict[str, object] | None, key: str) -> str | None:
    if state_entry is None or key not in state_entry or state_entry[key] is None:
        return None
    value = state_entry[key]
    if not isinstance(value, str):
        raise ValueError(f"Recurring state field '{key}' must be a string")
    return value


def _parse_consecutive_failures(state_entry: dict[str, object]) -> int:
    raw_value = state_entry.get("consecutive_failures", 0)
    if type(raw_value) is not int or raw_value < 0:
        raise ValueError("Recurring state field 'consecutive_failures' must be a non-negative integer")
    return raw_value
