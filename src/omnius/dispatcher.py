from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix=".tmp",
        text=True,
    )
    os.close(descriptor)
    temp_path = Path(temp_name)
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp_path, path)


def initialize_dispatch_log(
    path: Path,
    *,
    pipeline_id: str,
    runner_name: str,
    repo_slug: str,
    branch: str,
) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "pipeline": {
            "pipeline_id": pipeline_id,
            "runner": runner_name,
            "repo_slug": repo_slug,
            "branch": branch,
        },
        "tasks": {},
    }
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def load_dispatch_log(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_patch(base: dict[str, object], patch: dict[str, object]) -> dict[str, object]:
    merged = dict(base)
    for key, value in patch.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_patch(existing, value)
            continue
        merged[key] = value
    return merged


def update_dispatch_log(path: Path, *, patch: dict[str, object]) -> dict[str, object]:
    payload = load_dispatch_log(path)
    merged = _merge_patch(payload, patch)
    _write_json_atomic(path, merged)
    return merged
