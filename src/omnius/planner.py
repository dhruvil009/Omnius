from __future__ import annotations

import importlib.resources as resources
import json


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
    raise ValueError(f"Unsupported schema type: {expected_type}")


def _read_package_resource(*path_parts: str) -> str:
    resource = resources.files(__package__)
    for part in path_parts:
        resource = resource.joinpath(part)
    return resource.read_text(encoding="utf-8")
