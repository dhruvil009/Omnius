from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import tempfile
from typing import Callable


PidChecker = Callable[[int], bool]
KillPid = Callable[[int, int], object]


class PipelineAlreadyRunning(RuntimeError):
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        pipeline_id = payload.get("pipeline_id", "<unknown>")
        pid = payload.get("pid", "<unknown>")
        super().__init__(f"Omnius pipeline {pipeline_id} is already running as PID {pid}")


@dataclass(frozen=True)
class PipelineLock:
    state_dir: Path
    pipeline_id: str
    path: Path

    def update_worker(self, *, active_worker_pid: int | None, active_worker_pgid: int | None) -> None:
        payload = read_pipeline_pid(self.state_dir)
        if payload is None or payload.get("pipeline_id") != self.pipeline_id or payload.get("pid") != os.getpid():
            return
        payload["active_worker_pid"] = active_worker_pid
        payload["active_worker_pgid"] = active_worker_pgid
        payload["updated_at"] = _now_iso()
        _write_json_atomic(self.path, payload)

    def release(self) -> None:
        payload = read_pipeline_pid(self.state_dir)
        if payload is None:
            return
        if payload.get("pipeline_id") != self.pipeline_id or payload.get("pid") != os.getpid():
            return
        self.path.unlink(missing_ok=True)


@dataclass(frozen=True)
class RuntimeRecoveryResult:
    status: str
    payload: dict[str, object] | None
    removed_lock: bool


@dataclass(frozen=True)
class RuntimeStopResult:
    status: str
    payload: dict[str, object] | None
    removed_lock: bool
    signaled: list[dict[str, object]]


def acquire_pipeline_lock(
    *,
    state_dir: Path,
    pipeline_id: str,
    journal_dir: Path,
    runner_name: str,
    pid_checker: PidChecker = None,
) -> PipelineLock:
    checker = pid_checker or pid_is_alive
    state_dir.mkdir(parents=True, exist_ok=True)
    path = _pipeline_pid_path(state_dir)
    stale_payload = _remove_stale_lock_if_present(path=path, pid_checker=checker)
    payload: dict[str, object] = {
        "pid": os.getpid(),
        "pipeline_id": pipeline_id,
        "journal_dir": str(journal_dir),
        "runner": runner_name,
        "started_at": _now_iso(),
        "updated_at": _now_iso(),
        "active_worker_pid": None,
        "active_worker_pgid": None,
    }
    if stale_payload is not None:
        payload["stale_replaced"] = _stale_summary(stale_payload)

    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except FileExistsError:
        existing = read_pipeline_pid(state_dir) or {}
        if _payload_pid_is_alive(existing, checker):
            raise PipelineAlreadyRunning(existing)
        path.unlink(missing_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    return PipelineLock(state_dir=state_dir, pipeline_id=pipeline_id, path=path)


def read_pipeline_pid(state_dir: Path) -> dict[str, object] | None:
    path = _pipeline_pid_path(state_dir)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def recover_pipeline_lock(
    *,
    state_dir: Path,
    pid_checker: PidChecker = None,
) -> RuntimeRecoveryResult:
    checker = pid_checker or pid_is_alive
    payload = read_pipeline_pid(state_dir)
    if payload is None:
        return RuntimeRecoveryResult(status="no_lock", payload=None, removed_lock=False)
    if _payload_pid_is_alive(payload, checker):
        return RuntimeRecoveryResult(status="running", payload=payload, removed_lock=False)
    _pipeline_pid_path(state_dir).unlink(missing_ok=True)
    return RuntimeRecoveryResult(status="stale_removed", payload=payload, removed_lock=True)


def stop_pipeline(
    *,
    state_dir: Path,
    dry_run: bool,
    force: bool,
    pid_checker: PidChecker = None,
    kill_pid: KillPid = os.kill,
    kill_pgid: KillPid = os.killpg,
) -> RuntimeStopResult:
    checker = pid_checker or pid_is_alive
    payload = read_pipeline_pid(state_dir)
    if payload is None:
        return RuntimeStopResult(status="no_lock", payload=None, removed_lock=False, signaled=[])

    running = _payload_pid_is_alive(payload, checker)
    if dry_run:
        return RuntimeStopResult(
            status="running" if running else "stale",
            payload=payload,
            removed_lock=False,
            signaled=[],
        )
    if not force:
        return RuntimeStopResult(status="force_required", payload=payload, removed_lock=False, signaled=[])
    if not running:
        _pipeline_pid_path(state_dir).unlink(missing_ok=True)
        return RuntimeStopResult(status="stale_removed", payload=payload, removed_lock=True, signaled=[])

    signaled: list[dict[str, object]] = []
    worker_pgid = _coerce_pid(payload.get("active_worker_pgid"))
    worker_pid = _coerce_pid(payload.get("active_worker_pid"))
    pipeline_pid = _coerce_pid(payload.get("pid"))
    if worker_pgid is not None:
        _safe_signal_group(kill_pgid=kill_pgid, pgid=worker_pgid, sig=signal.SIGTERM, signaled=signaled)
    elif worker_pid is not None:
        _safe_signal_pid(kill_pid=kill_pid, pid=worker_pid, sig=signal.SIGTERM, signaled=signaled, role="worker")
    if pipeline_pid is not None:
        _safe_signal_pid(kill_pid=kill_pid, pid=pipeline_pid, sig=signal.SIGTERM, signaled=signaled, role="pipeline")
    _pipeline_pid_path(state_dir).unlink(missing_ok=True)
    return RuntimeStopResult(status="signaled", payload=payload, removed_lock=True, signaled=signaled)


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pipeline_pid_path(state_dir: Path) -> Path:
    return state_dir / "pipeline.pid"


def _remove_stale_lock_if_present(*, path: Path, pid_checker: PidChecker) -> dict[str, object] | None:
    if not path.exists():
        return None
    payload = read_pipeline_pid(path.parent) or {}
    if _payload_pid_is_alive(payload, pid_checker):
        raise PipelineAlreadyRunning(payload)
    path.unlink(missing_ok=True)
    return payload


def _payload_pid_is_alive(payload: dict[str, object], pid_checker: PidChecker) -> bool:
    pid = _coerce_pid(payload.get("pid"))
    if pid is None:
        return False
    return pid_checker(pid)


def _coerce_pid(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _stale_summary(payload: dict[str, object]) -> dict[str, object]:
    summary: dict[str, object] = {}
    for key in ("pid", "pipeline_id", "journal_dir", "started_at"):
        if key in payload:
            summary[key] = payload[key]
    return summary


def _safe_signal_group(
    *,
    kill_pgid: KillPid,
    pgid: int,
    sig: int,
    signaled: list[dict[str, object]],
) -> None:
    try:
        kill_pgid(pgid, sig)
    except ProcessLookupError:
        return
    signaled.append({"target": "worker_pgid", "pid": pgid, "signal": int(sig)})


def _safe_signal_pid(
    *,
    kill_pid: KillPid,
    pid: int,
    sig: int,
    signaled: list[dict[str, object]],
    role: str,
) -> None:
    try:
        kill_pid(pid, sig)
    except ProcessLookupError:
        return
    signaled.append({"target": role, "pid": pid, "signal": int(sig)})


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


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()
