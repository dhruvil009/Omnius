from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import os
from pathlib import Path
import re
import tempfile

from omnius.runners.base import UsageStats


@dataclass(frozen=True)
class SessionCostRecord:
    file_stem: str
    session_name: str
    started_at: str | None
    ended_at: str | None
    status: str
    task_id: str | None = None
    task_type: str | None = None
    complexity: str | None = None
    usage: UsageStats | None = None


@dataclass(frozen=True)
class AggregateLedgerRow:
    run_date: str
    total_tasks: int
    success_count: int
    total_cost_usd: float
    notes: str


_ROW_RE = re.compile(
    r"^\| (?P<run_date>\d{4}-\d{2}-\d{2}) \| +(?P<total_tasks>\d+) +\| +(?P<success_count>\d+) +\| \$(?P<total_cost_usd>\d+\.\d{2}) +\| (?P<notes>.*) \|$"
)


def write_session_cost_record(*, costs_dir: Path, session: SessionCostRecord) -> Path:
    path = costs_dir / f"{session.file_stem}.md"
    _write_text_atomic(path, _render_session_cost_markdown(session))
    return path


def update_aggregate_cost_ledger(
    *,
    costs_dir: Path,
    run_date: str,
    total_tasks: int,
    success_count: int,
    total_cost_usd: float,
    notes: str,
) -> Path:
    path = costs_dir / "omnius_cost.md"
    rows = _load_aggregate_rows(path)
    rows.append(
        AggregateLedgerRow(
            run_date=run_date,
            total_tasks=total_tasks,
            success_count=success_count,
            total_cost_usd=round(total_cost_usd, 2),
            notes=notes,
        )
    )
    rows.sort(key=lambda row: row.run_date, reverse=True)
    _write_text_atomic(path, _render_aggregate_markdown(rows))
    return path


def _render_session_cost_markdown(session: SessionCostRecord) -> str:
    usage = session.usage or UsageStats()
    lines = [
        "# Omnius Session Cost",
        "",
        f"- Session: {session.session_name}",
        f"- Status: {session.status}",
        f"- Start: {session.started_at or '<unknown>'}",
        f"- End: {session.ended_at or '<unknown>'}",
    ]
    if session.task_id is not None:
        lines.append(f"- Task ID: {session.task_id}")
    if session.task_type is not None:
        lines.append(f"- Task Type: {session.task_type}")
    if session.complexity is not None:
        lines.append(f"- Complexity: {session.complexity}")
    lines.extend(
        [
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| Cost | {_format_currency(usage.cost_usd)} |",
            f"| Input Tokens | {_format_optional_int(usage.input_tokens)} |",
            f"| Output Tokens | {_format_optional_int(usage.output_tokens)} |",
            f"| Cache Read Tokens | {_format_optional_int(usage.cache_read_tokens)} |",
            f"| Cache Create Tokens | {_format_optional_int(usage.cache_create_tokens)} |",
            f"| Turns | {_format_optional_int(usage.turns)} |",
            f"| Model | {usage.model or '<unknown>'} |",
            "",
        ]
    )
    return "\n".join(lines)


def _render_aggregate_markdown(rows: list[AggregateLedgerRow]) -> str:
    if not rows:
        return "# Omnius Cost Ledger\n"
    running_total = sum(row.total_cost_usd for row in rows)
    earliest_date = min(row.run_date for row in rows)
    lines = [
        "# Omnius Cost Ledger",
        "",
        f"_Running total since {earliest_date}: **${running_total:.2f}**_",
        "",
    ]
    grouped: dict[str, list[AggregateLedgerRow]] = {}
    for row in rows:
        month_label = _month_label(row.run_date)
        grouped.setdefault(month_label, []).append(row)
    for month_label in sorted(grouped.keys(), key=_month_sort_key, reverse=True):
        lines.extend(
            [
                f"## {month_label}",
                "| Date       | Tasks | SUCCESS | Cost    | Notes |",
                "|------------|-------|---------|---------|-------|",
            ]
        )
        for row in grouped[month_label]:
            lines.append(
                f"| {row.run_date} | {row.total_tasks:>3}   | {row.success_count:>3}     | ${row.total_cost_usd:.2f}   | {row.notes} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _load_aggregate_rows(path: Path) -> list[AggregateLedgerRow]:
    if not path.exists():
        return []
    rows: list[AggregateLedgerRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ROW_RE.match(line)
        if match is None:
            continue
        rows.append(
            AggregateLedgerRow(
                run_date=match.group("run_date"),
                total_tasks=int(match.group("total_tasks")),
                success_count=int(match.group("success_count")),
                total_cost_usd=float(match.group("total_cost_usd")),
                notes=match.group("notes"),
            )
        )
    return rows


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix=".tmp",
        text=True,
    )
    os.close(descriptor)
    temp_path = Path(temp_name)
    temp_path.write_text(text, encoding="utf-8")
    os.replace(temp_path, path)


def _month_label(run_date: str) -> str:
    parsed = date.fromisoformat(run_date)
    return parsed.strftime("%B %Y")


def _month_sort_key(label: str) -> datetime:
    return datetime.strptime(label, "%B %Y")


def _format_currency(value: float | None) -> str:
    if value is None:
        return "<unknown>"
    return f"${value:.2f}"


def _format_optional_int(value: int | None) -> str:
    if value is None:
        return "<unknown>"
    return str(value)
