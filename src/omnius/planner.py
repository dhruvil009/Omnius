from __future__ import annotations

from dataclasses import dataclass
import importlib.resources as resources
import json
from pathlib import Path

from omnius.tasks import LocalTaskEntry


@dataclass(frozen=True)
class ManifestTask:
    task_id: str
    title: str
    task_type: str
    repo_slug: str
    source_ref: str
    filename: str
    max_time_minutes: int
    complexity: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.task_id,
            "title": self.title,
            "type": self.task_type,
            "repo_slug": self.repo_slug,
            "source_ref": self.source_ref,
            "filename": self.filename,
            "max_time_minutes": self.max_time_minutes,
            "complexity": self.complexity,
        }


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


def validate_manifest(payload: dict[str, object]) -> None:
    _validate_against_schema(load_manifest_schema(), payload, path="manifest")


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
                task_type="implementation",
                repo_slug=_require_frontmatter_value(metadata, "repo", entry.task_id),
                source_ref=str(Path("tasks") / entry.filename),
                filename=entry.filename,
                max_time_minutes=default_task_budget_minutes,
                complexity="small",
            ).as_dict()
        )
    return manifest_tasks


def _render_section(label: str, value: str) -> str:
    return f"{label}\n{value}"


def _validate_against_schema(schema: object, value: object, *, path: str) -> None:
    if not isinstance(schema, dict):
        raise ValueError("Manifest schema must be a JSON object")

    expected_type = schema.get("type")
    if isinstance(expected_type, str):
        _validate_type(expected_type, value, path=path)

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
