from __future__ import annotations

from dataclasses import dataclass
import importlib.resources as resources
import json
from pathlib import Path, PurePosixPath

from omnius.config import SUPPORTED_RUNNERS
from omnius.tasks import SUPPORTED_TASK_TYPES, LocalTaskEntry, RecurringTaskEntry


@dataclass(frozen=True)
class ManifestTask:
    task_id: str
    title: str
    task_type: str
    repo_slug: str
    source_ref: str
    filename: str
    agent: str | None
    max_time_minutes: int
    complexity: str
    priority: int
    source: str
    project_context: str
    file_paths: list[str]
    quality_phases: list[str]
    completion_contract: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.task_id,
            "title": self.title,
            "type": self.task_type,
            "repo_slug": self.repo_slug,
            "source": self.source,
            "source_ref": self.source_ref,
            "filename": self.filename,
            "priority": self.priority,
            "project_context": self.project_context,
            "file_paths": list(self.file_paths),
            "quality_phases": list(self.quality_phases),
            "completion_contract": dict(self.completion_contract),
            "max_time_minutes": self.max_time_minutes,
            "complexity": self.complexity,
        }
        if self.agent is not None:
            payload["agent"] = self.agent
        return payload


@dataclass(frozen=True)
class PlannerResponseSelection:
    response_text: str
    used_runner_output: bool
    fallback_reason: str | None


def load_planner_prompt_template() -> str:
    return _read_package_resource("resources", "prompts", "planner.md")


def load_manifest_schema() -> dict[str, object]:
    return json.loads(_read_package_resource("resources", "schemas", "manifest.schema.json"))


def build_planner_prompt(
    *,
    template: str,
    run_date: str,
    journal_dir: str,
    repos_table: str,
    local_tasks: str,
    recurring_tasks: str,
    github_issues: str,
    pr_review_comments: str,
    pending_approval: str,
) -> str:
    return "\n\n".join(
        [
            template.rstrip(),
            _render_section("RUN_DATE", run_date),
            _render_section("JOURNAL_DIR", journal_dir),
            _render_section("REPOS_TABLE", repos_table),
            _render_section("LOCAL_TASKS", local_tasks),
            _render_section("RECURRING_TASKS", recurring_tasks),
            _render_section("GITHUB_ISSUES", github_issues),
            _render_section("PR_REVIEW_COMMENTS", pr_review_comments),
            _render_section("PENDING_APPROVAL", pending_approval),
        ]
    )


def parse_planner_response(raw_response: str) -> dict[str, object]:
    payload = json.loads(raw_response)
    if not isinstance(payload, dict):
        raise ValueError("Planner response must decode to a JSON object")
    return payload


def validate_manifest(payload: dict[str, object], *, allowed_repo_slugs: set[str] | None = None) -> None:
    _validate_against_schema(load_manifest_schema(), payload, path="manifest")
    _validate_task_agents(payload.get("tasks"))
    _validate_task_ids_are_unique(payload.get("tasks"))
    _validate_task_source_refs(payload.get("tasks"))
    if allowed_repo_slugs is not None:
        _validate_task_repo_slugs(payload.get("tasks"), allowed_repo_slugs=allowed_repo_slugs)


def choose_planner_response(*, planner_output: str, fallback_manifest_response: str) -> str:
    return choose_planner_response_with_metadata(
        planner_output=planner_output,
        fallback_manifest_response=fallback_manifest_response,
    ).response_text


def choose_planner_response_with_metadata(*, planner_output: str, fallback_manifest_response: str) -> PlannerResponseSelection:
    try:
        manifest = parse_planner_response(planner_output)
    except json.JSONDecodeError:
        return PlannerResponseSelection(
            response_text=fallback_manifest_response,
            used_runner_output=False,
            fallback_reason="invalid_json",
        )
    except (ValueError, TypeError) as exc:
        return PlannerResponseSelection(
            response_text=fallback_manifest_response,
            used_runner_output=False,
            fallback_reason=f"invalid_manifest: {exc}",
        )
    try:
        validate_manifest(manifest)
    except (ValueError, TypeError) as exc:
        return PlannerResponseSelection(
            response_text=fallback_manifest_response,
            used_runner_output=False,
            fallback_reason=f"invalid_manifest: {exc}",
        )
    return PlannerResponseSelection(response_text=planner_output, used_runner_output=True, fallback_reason=None)


def build_local_manifest_tasks(
    *,
    entries: list[LocalTaskEntry],
    default_task_budget_minutes: int,
) -> list[dict[str, object]]:
    manifest_tasks: list[dict[str, object]] = []
    for entry in entries:
        metadata = _extract_task_frontmatter(entry.body)
        manifest_tasks.append(
            ManifestTask(
                task_id=entry.task_id,
                title=_require_frontmatter_value(metadata, "title", entry.task_id),
                task_type=_parse_task_type(metadata.get("type"), task_id=entry.task_id),
                repo_slug=_require_frontmatter_value(metadata, "repo", entry.task_id),
                source_ref=str(Path("tasks") / entry.filename),
                filename=entry.filename,
                agent=entry.agent,
                max_time_minutes=default_task_budget_minutes,
                complexity="small",
                priority=3,
                source="local_queue",
                project_context="local task queue",
                file_paths=[],
                quality_phases=["implement", "verify"],
                completion_contract=_default_completion_contract(),
            ).as_dict()
        )
    return manifest_tasks


def build_manifest_tasks(
    *,
    local_entries: list[LocalTaskEntry],
    recurring_entries: list[RecurringTaskEntry],
    default_task_budget_minutes: int,
) -> list[dict[str, object]]:
    manifest_tasks = build_local_manifest_tasks(
        entries=local_entries,
        default_task_budget_minutes=default_task_budget_minutes,
    )
    for entry in recurring_entries:
        manifest_tasks.append(
            ManifestTask(
                task_id=entry.task_id,
                title=entry.title,
                task_type=_parse_task_type(entry.task_type, task_id=entry.task_id),
                repo_slug=entry.repo_slug,
                source_ref=str(Path("tasks") / "recurring" / entry.filename),
                filename=entry.filename,
                agent=None,
                max_time_minutes=entry.max_time_minutes or default_task_budget_minutes,
                complexity=entry.complexity,
                priority=4,
                source="recurring_queue",
                project_context="recurring task queue",
                file_paths=[],
                quality_phases=["implement", "verify"],
                completion_contract=_default_completion_contract(),
            ).as_dict()
        )
    return manifest_tasks


def _render_section(label: str, value: str) -> str:
    return f"{label}\n{value}"


def _validate_task_agents(raw_tasks: object) -> None:
    if not isinstance(raw_tasks, list):
        return
    for index, raw_task in enumerate(raw_tasks):
        if not isinstance(raw_task, dict):
            continue
        agent = raw_task.get("agent")
        if agent is None:
            continue
        if not isinstance(agent, str) or agent not in SUPPORTED_RUNNERS:
            raise ValueError(f"Manifest field tasks[{index}].agent must be one of: {', '.join(sorted(SUPPORTED_RUNNERS))}")


def _validate_task_ids_are_unique(raw_tasks: object) -> None:
    if not isinstance(raw_tasks, list):
        return
    seen: set[str] = set()
    for index, raw_task in enumerate(raw_tasks):
        if not isinstance(raw_task, dict):
            continue
        task_id = raw_task.get("id")
        if not isinstance(task_id, str):
            continue
        if task_id in seen:
            raise ValueError(f"Duplicate manifest task id: {task_id} at tasks[{index}]")
        seen.add(task_id)


def _validate_task_source_refs(raw_tasks: object) -> None:
    if not isinstance(raw_tasks, list):
        return
    for index, raw_task in enumerate(raw_tasks):
        if not isinstance(raw_task, dict):
            continue
        source_ref = raw_task.get("source_ref")
        if not isinstance(source_ref, str):
            continue
        path = PurePosixPath(source_ref)
        if path.is_absolute() or ".." in path.parts or len(path.parts) < 2 or path.parts[0] != "tasks":
            raise ValueError(f"Manifest field tasks[{index}].source_ref must stay under tasks/")


def _validate_task_repo_slugs(raw_tasks: object, *, allowed_repo_slugs: set[str]) -> None:
    if not isinstance(raw_tasks, list):
        return
    for index, raw_task in enumerate(raw_tasks):
        if not isinstance(raw_task, dict):
            continue
        repo_slug = raw_task.get("repo_slug")
        if not isinstance(repo_slug, str):
            continue
        if repo_slug not in allowed_repo_slugs:
            raise ValueError(f"Manifest task {index} referenced unknown repo_slug: {repo_slug}")


def _validate_against_schema(schema: object, value: object, *, path: str) -> None:
    if not isinstance(schema, dict):
        raise ValueError("Manifest schema must be a JSON object")

    expected_type = schema.get("type")
    if isinstance(expected_type, str):
        _validate_type(expected_type, value, path=path)

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        raise ValueError(f"Manifest field {path} must be one of: {', '.join(str(item) for item in enum_values)}")

    minimum = schema.get("minimum")
    if isinstance(minimum, int) and isinstance(value, int) and value < minimum:
        raise ValueError(f"Manifest field {path} must be >= {minimum}")

    if expected_type == "object":
        assert isinstance(value, dict)
        required = schema.get("required", [])
        if not isinstance(required, list):
            raise ValueError("Manifest schema 'required' must be a list")
        for key in required:
            if key not in value:
                raise ValueError(f"Manifest missing required field: {path}.{key}")

        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError("Manifest schema 'properties' must be an object")
        for key, property_schema in properties.items():
            if key in value:
                _validate_against_schema(property_schema, value[key], path=f"{path}.{key}")
        return

    if expected_type == "array":
        assert isinstance(value, list)
        item_schema = schema.get("items")
        if item_schema is None:
            return
        for index, item in enumerate(value):
            _validate_against_schema(item_schema, item, path=f"{path}[{index}]")


def _validate_type(expected_type: str, value: object, *, path: str) -> None:
    if expected_type == "object":
        if not isinstance(value, dict):
            raise ValueError(f"Manifest field {path} must be an object")
        return
    if expected_type == "array":
        if not isinstance(value, list):
            raise ValueError(f"Manifest field {path} must be an array")
        return
    if expected_type == "string":
        if not isinstance(value, str):
            raise ValueError(f"Manifest field {path} must be a string")
        return
    if expected_type == "integer":
        if type(value) is not int:
            raise ValueError(f"Manifest field {path} must be an integer")
        return
    if expected_type == "boolean":
        if type(value) is not bool:
            raise ValueError(f"Manifest field {path} must be a boolean")
        return
    raise ValueError(f"Unsupported schema type: {expected_type}")


def _read_package_resource(*path_parts: str) -> str:
    resource = resources.files(__package__)
    for part in path_parts:
        resource = resource.joinpath(part)
    return resource.read_text(encoding="utf-8")


def _extract_task_frontmatter(body: str) -> dict[str, str]:
    lines = body.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        raise ValueError("Task body must start with YAML frontmatter")

    metadata: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return metadata
        key, separator, value = line.partition(":")
        if separator == "":
            raise ValueError(f"Malformed task frontmatter line: {line}")
        metadata[key.strip()] = value.strip()

    raise ValueError("Task body frontmatter must be closed with '---'")


def _require_frontmatter_value(metadata: dict[str, str], key: str, task_id: str) -> str:
    value = metadata.get(key)
    if not value:
        raise ValueError(f"Task {task_id} frontmatter must define '{key}'")
    return value


def _parse_task_type(raw_value: str | None, *, task_id: str) -> str:
    if raw_value is None or raw_value == "":
        return "implementation"
    normalized = raw_value.strip()
    if normalized in SUPPORTED_TASK_TYPES:
        return normalized
    raise ValueError(f"Task {task_id} frontmatter has invalid 'type': {raw_value}")


def _default_completion_contract() -> dict[str, object]:
    return {
        "artifact": "committed_branch_or_pr",
        "archive_on": "SUCCESS_WITH_ARTIFACT",
    }
