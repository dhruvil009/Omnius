from __future__ import annotations

from dataclasses import dataclass
import importlib.resources as resources
import json
from pathlib import Path

from omnius.costs import SessionCostRecord, write_session_cost_record
from omnius.runners.base import RunnerAdapter


DAYPREP_PROVIDER_ARTIFACTS = (
    ("patch_status.md", "Patch Status"),
    ("task_triage.md", "Task Triage"),
    ("chat_catchup.md", "Chat Catchup"),
    ("meeting_prep.md", "Meeting Prep"),
)
_MISSING_PROVIDER_MARKER = "Status: missing"
_ATTENTION_STATUSES = {"NO_ARTIFACT", "PARTIAL", "BLOCKED", "FAILURE", "TIMEOUT", "CRASH"}


@dataclass(frozen=True)
class DayPrepResult:
    brief_path: Path
    latest_brief_path: Path
    used_fallback: bool
    warning_banner: str | None = None


def run_dayprep(
    *,
    runner: RunnerAdapter,
    workspace_home: Path,
    journal_dir: Path,
    dispatch_log_path: Path,
) -> DayPrepResult:
    prompt = build_dayprep_prompt(journal_dir=journal_dir, dispatch_log_path=dispatch_log_path)
    (journal_dir / "dayprep_prompt.md").write_text(prompt, encoding="utf-8")
    latest_brief_path = workspace_home / "daily_brief.md"
    try:
        invocation = runner.invoke_dayprep(task_id="dayprep-run", prompt=prompt)
        brief_text = invocation.brief_markdown
        brief_path = journal_dir / "daily_brief.md"
        warning_banner = None
        if invocation.usage is not None:
            write_session_cost_record(
                costs_dir=workspace_home / "costs",
                session=SessionCostRecord(
                    file_stem=f"{journal_dir.parent.name}_{journal_dir.name}_dayprep",
                    session_name="dayprep",
                    started_at=None,
                    ended_at=None,
                    status="SUCCESS",
                    usage=invocation.usage,
                ),
            )
    except Exception as exc:
        brief_text = render_fallback_brief(
            dispatch_log=_load_json(dispatch_log_path),
            journal_dir=journal_dir,
            error=str(exc),
        )
        brief_path = journal_dir / "daily_brief_fallback.md"
        warning_banner = "Day prep failed; minimal brief only."
    brief_path.write_text(brief_text, encoding="utf-8")
    latest_brief_path.write_text(brief_text, encoding="utf-8")
    return DayPrepResult(
        brief_path=brief_path,
        latest_brief_path=latest_brief_path,
        used_fallback=warning_banner is not None,
        warning_banner=warning_banner,
    )


def build_dayprep_prompt(*, journal_dir: Path, dispatch_log_path: Path) -> str:
    template = _load_dayprep_prompt_template()
    provider_inputs = ensure_dayprep_provider_artifacts(journal_dir)
    manifest_summary = _load_manifest_summary(journal_dir)
    dispatch_log = dispatch_log_path.read_text(encoding="utf-8").strip()
    return "\n\n".join(
        [
            template.rstrip(),
            "MANIFEST_SUMMARY",
            manifest_summary,
            "DAYPREP_PROVIDER_INPUTS",
            _render_provider_inputs(provider_inputs),
            "MISSING_INPUTS",
            _render_missing_inputs(provider_inputs),
            "DISPATCH_LOG_JSON",
            dispatch_log,
        ]
    )


def ensure_dayprep_provider_artifacts(journal_dir: Path) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    for filename, label in DAYPREP_PROVIDER_ARTIFACTS:
        path = journal_dir / filename
        if not path.exists():
            path.write_text(_render_missing_provider_stub(label=label, filename=filename), encoding="utf-8")
        content = path.read_text(encoding="utf-8", errors="replace")
        artifacts.append(
            {
                "filename": filename,
                "label": label,
                "path": str(path),
                "content": content,
                "missing": _MISSING_PROVIDER_MARKER in content,
            }
        )
    return artifacts


def render_fallback_brief(*, dispatch_log: dict[str, object], journal_dir: Path, error: str) -> str:
    pipeline = dispatch_log.get("pipeline", {})
    run_date = pipeline.get("run_date", "<unknown>") if isinstance(pipeline, dict) else "<unknown>"
    task_lines: list[str] = []
    tasks = dispatch_log.get("tasks", {})
    if isinstance(tasks, dict):
        for task_id, task_state in tasks.items():
            if not isinstance(task_state, dict):
                continue
            title = task_state.get("title", task_id)
            status = task_state.get("status", "<unknown>")
            task_lines.append(f"- {task_id} {title} — {status}")
    if not task_lines:
        task_lines.append("- <no tasks recorded>")
    top_actions = _fallback_top_actions(dispatch_log)
    return "\n".join(
        [
            f"# Omnius — {run_date}",
            "",
            "Day prep failed; minimal brief only.",
            "Partial brief: generated without the day-prep compiler.",
            "",
            f"Error: {error}",
            "",
            "## Top Actions",
            *top_actions,
            "",
            "## Tasks",
            *task_lines,
            "",
            f"_Full log: {journal_dir}_",
            "",
        ]
    )


def _render_missing_provider_stub(*, label: str, filename: str) -> str:
    return "\n".join(
        [
            f"# {label}",
            "",
            _MISSING_PROVIDER_MARKER,
            f"No {label.lower()} provider artifact was available for this run.",
            f"Do not infer this input from other sources. Replace {filename} with real provider output when available.",
            "",
        ]
    )


def _render_provider_inputs(provider_inputs: list[dict[str, object]]) -> str:
    sections: list[str] = []
    for item in provider_inputs:
        sections.append(
            "\n".join(
                [
                    f"--- {item['filename']} ({item['label']}) ---",
                    str(item["content"]).rstrip(),
                ]
            )
        )
    return "\n\n".join(sections)


def _render_missing_inputs(provider_inputs: list[dict[str, object]]) -> str:
    missing = [f"- {item['filename']}" for item in provider_inputs if item.get("missing")]
    if not missing:
        return "<none>"
    return "\n".join(missing)


def _fallback_top_actions(dispatch_log: dict[str, object]) -> list[str]:
    tasks = dispatch_log.get("tasks", {})
    if not isinstance(tasks, dict):
        return ["- Review the full journal for run details."]
    actions: list[str] = []
    for task_id, task_state in tasks.items():
        if not isinstance(task_state, dict):
            continue
        status = task_state.get("status")
        if status not in _ATTENTION_STATUSES:
            continue
        title = task_state.get("title") or task_id
        actions.append(f"- Review {task_id}: {title} ({status})")
    if actions:
        return actions
    return ["- Review the full journal if more detail is needed."]


def _load_dayprep_prompt_template() -> str:
    return resources.files("omnius").joinpath("resources", "prompts", "dayprep_compiler.md").read_text(encoding="utf-8")


def _load_manifest_summary(journal_dir: Path) -> str:
    manifest_path = journal_dir / "manifest.json"
    if not manifest_path.exists():
        return "<none>"
    manifest = _load_json(manifest_path)
    summary = manifest.get("summary")
    if not isinstance(summary, str):
        return "<none>"
    return summary


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload
