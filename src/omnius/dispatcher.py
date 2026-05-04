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
    if path.exists():
        raise FileExistsError(path)

    payload = {
        "pipeline": {
            "pipeline_id": pipeline_id,
            "runner": runner_name,
            "repo_slug": repo_slug,
            "branch": branch,
        },
        "tasks": {},
    }
    _write_json_atomic(path, payload)
    return payload


def load_dispatch_log(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def update_dispatch_log(path: Path, *, task_id: str, task_payload: dict[str, object]) -> dict[str, object]:
    payload = load_dispatch_log(path)
    tasks = dict(payload.get("tasks", {}))
    tasks[task_id] = task_payload
    payload["tasks"] = tasks
    _write_json_atomic(path, payload)
    return payload
