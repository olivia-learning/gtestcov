from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fs import resolve_run_dir


GTESTCOV_STATUS = "gtestcov_status.json"
GTESTCOV_EVENTS = "gtestcov_events.ndjson"
CODRAX_STATUS = "codrax_status.json"
DEFAULT_EVENT_LOG_MAX_BYTES = 512 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def update_run_status(
    run_dir: Path,
    *,
    phase: str,
    step: str = "",
    command: str = "",
    target: str = "",
    current_operation: str = "",
    last_artifact: str = "",
    notes: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / GTESTCOV_STATUS
    existing = _read_json(path)
    now = utc_now()
    started_at = existing.get("started_at") or now
    status = {
        **existing,
        "run_id": run_dir.name,
        "phase": phase,
        "step": step,
        "command": command or existing.get("command", ""),
        "target": target or existing.get("target", ""),
        "started_at": started_at,
        "updated_at": now,
        "current_operation": current_operation,
        "last_artifact": last_artifact or existing.get("last_artifact", ""),
        "notes": notes or [],
    }
    if extra:
        status.update(extra)
    status["elapsed_seconds"] = _seconds_between(started_at, now)
    path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    append_run_event(run_dir, phase, step=step, current_operation=current_operation, artifact=last_artifact, notes=notes)
    return status


def append_run_event(
    run_dir: Path,
    phase: str,
    *,
    step: str = "",
    current_operation: str = "",
    artifact: str = "",
    notes: list[str] | None = None,
    max_bytes: int = DEFAULT_EVENT_LOG_MAX_BYTES,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / GTESTCOV_EVENTS
    record = {
        "ts": utc_now(),
        "run_id": run_dir.name,
        "phase": phase,
        "step": step,
        "current_operation": current_operation,
        "artifact": artifact,
        "notes": notes or [],
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    _trim_event_log(path, max_bytes)


def show_status(project_root: Path, run_id: str = "latest") -> dict[str, Any]:
    root = project_root.resolve()
    active_run_id, run_dir = resolve_run_dir(root, run_id)
    return {
        "project_root": str(root),
        "run_id": active_run_id,
        "gtestcov_status": _read_json(run_dir / GTESTCOV_STATUS) or {"status": "not_started"},
        "codrax_status": _read_json(run_dir / CODRAX_STATUS) or {"status": "not_started"},
        "paths": {
            "gtestcov_status": str(run_dir / GTESTCOV_STATUS),
            "gtestcov_events": str(run_dir / GTESTCOV_EVENTS),
            "codrax_status": str(run_dir / CODRAX_STATUS),
        },
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _seconds_between(started_at: str, ended_at: str) -> float:
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(ended_at)
    except ValueError:
        return 0.0
    return round(max(0.0, (end - start).total_seconds()), 3)


def _trim_event_log(path: Path, max_bytes: int) -> None:
    if max_bytes <= 0 or not path.exists() or path.stat().st_size <= max_bytes:
        return
    marker = {"ts": utc_now(), "phase": "gtestcov_events_truncated", "notes": ["older events dropped"]}
    marker_bytes = (json.dumps(marker, ensure_ascii=False) + "\n").encode("utf-8")
    keep = max(0, max_bytes - len(marker_bytes))
    data = path.read_bytes()
    tail = data[-keep:] if keep else b""
    path.write_bytes(marker_bytes + tail)
