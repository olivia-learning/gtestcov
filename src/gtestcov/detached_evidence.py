from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from .fs import ensure_run_dir, resolve_run_dir
from .run_status import GTESTCOV_STATUS, update_run_status, utc_now
from .understanding import generate_project_understanding


DETACHED_META = "detached_evidence.json"
DETACHED_RESULT = "detached_evidence_result.json"
DETACHED_STALE_SECONDS = 24 * 60 * 60


def evidence_start(project_root: Path, target: str, run_id: str | None = None) -> dict[str, Any]:
    root = project_root.resolve()
    active_run_id, run_dir = ensure_run_dir(root, run_id)
    update_run_status(
        run_dir,
        phase="evidence.detached_started",
        step="evidence",
        command="gtestcov evidence start",
        target=target,
        current_operation="detached_worker_start",
    )
    stdout_path = run_dir / "detached_evidence.stdout.log"
    stderr_path = run_dir / "detached_evidence.stderr.log"
    cmd = [
        sys.executable,
        "-m",
        "gtestcov.cli",
        "evidence",
        "collect",
        "--project-root",
        str(root),
        "--run-id",
        active_run_id,
        "--target",
        target,
        "--background-worker",
    ]
    meta = {
        "status": "starting",
        "run_id": active_run_id,
        "target": target,
        "project_root": str(root),
        "started_at": utc_now(),
        "status_path": str(run_dir / GTESTCOV_STATUS),
        "result_path": str(run_dir / DETACHED_RESULT),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "command": cmd,
    }
    _write_json(run_dir / DETACHED_META, meta)
    stdout_handle = stdout_path.open("w", encoding="utf-8")
    stderr_handle = stderr_path.open("w", encoding="utf-8")
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            cmd,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            close_fds=os.name != "nt",
            creationflags=creationflags,
        )
    except Exception as exc:
        stdout_handle.close()
        stderr_handle.close()
        meta.update({"status": "start_failed", "error": f"{type(exc).__name__}: {exc}", "updated_at": utc_now()})
        _write_json(run_dir / DETACHED_META, meta)
        update_run_status(
            run_dir,
            phase="evidence.detached_start_failed",
            step="evidence",
            command="gtestcov evidence start",
            target=target,
            current_operation="failed",
            notes=[meta["error"]],
        )
        return meta
    stdout_handle.close()
    stderr_handle.close()
    meta.update({"status": "running", "pid": process.pid, "updated_at": utc_now()})
    _write_json(run_dir / DETACHED_META, meta)
    return {
        "status": "started",
        "run_id": active_run_id,
        "target": target,
        "pid": process.pid,
        "status_path": str(run_dir / GTESTCOV_STATUS),
        "result_path": str(run_dir / DETACHED_RESULT),
        "collect_command": f"gtestcov evidence collect --project-root {root} --run-id {active_run_id}",
    }


def evidence_status(project_root: Path, run_id: str) -> dict[str, Any]:
    root = project_root.resolve()
    active_run_id, run_dir = resolve_run_dir(root, run_id)
    meta = _read_json(run_dir / DETACHED_META)
    result = _read_json(run_dir / DETACHED_RESULT)
    gtestcov_status = _read_json(run_dir / GTESTCOV_STATUS)
    status, meta, result, process_alive = _reconcile_detached_state(
        run_dir,
        active_run_id,
        meta,
        result,
        gtestcov_status,
    )
    return {
        "status": status,
        "run_id": active_run_id,
        "target": meta.get("target") or gtestcov_status.get("target", ""),
        "pid": meta.get("pid"),
        "process_alive": process_alive,
        "stale_reason": result.get("stale_reason", ""),
        "gtestcov_status": gtestcov_status or {"status": "not_started"},
        "paths": {
            "status_path": str(run_dir / GTESTCOV_STATUS),
            "meta_path": str(run_dir / DETACHED_META),
            "result_path": str(run_dir / DETACHED_RESULT),
            "stdout_path": meta.get("stdout_path", ""),
            "stderr_path": meta.get("stderr_path", ""),
        },
    }


def evidence_collect(
    project_root: Path,
    run_id: str,
    *,
    target: str = "",
    background_worker: bool = False,
) -> dict[str, Any]:
    root = project_root.resolve()
    active_run_id, run_dir = resolve_run_dir(root, run_id)
    result_path = run_dir / DETACHED_RESULT
    if result_path.exists() and not background_worker:
        return _read_json(result_path)
    meta_path = run_dir / DETACHED_META
    meta = _read_json(meta_path)
    active_target = target or meta.get("target", "")
    if not active_target:
        return {"status": "not_started", "run_id": active_run_id, "error": "target is required"}
    if not background_worker:
        status = evidence_status(root, active_run_id)
        if status["status"] == "failed":
            result = _read_json(result_path)
            return result or {
                "status": "failed",
                "run_id": active_run_id,
                "target": active_target,
                "error": "detached evidence worker failed before writing result",
            }
        if status["status"] == "done":
            return _read_json(result_path)
        return {
            "status": "running",
            "run_id": active_run_id,
            "target": active_target,
            "pid": status.get("pid"),
            "process_alive": status.get("process_alive"),
            "status_path": str(run_dir / GTESTCOV_STATUS),
            "result_path": str(result_path),
        }
    try:
        understanding, evidence_path = generate_project_understanding(root, active_target, active_run_id)
        result = {
            "status": "done",
            "run_id": active_run_id,
            "target": active_target,
            "evidence_path": str(evidence_path),
            "understanding": understanding.model_dump(mode="json"),
            "evidence_cache": understanding.codrax_evidence.cache,
        }
        if meta:
            meta.update({"status": "done", "updated_at": utc_now(), "evidence_path": str(evidence_path)})
            _write_json(meta_path, meta)
        _write_json(result_path, result)
        return result
    except Exception as exc:
        result = {
            "status": "failed",
            "run_id": active_run_id,
            "target": active_target,
            "error": f"{type(exc).__name__}: {exc}",
        }
        if meta:
            meta.update({"status": "failed", "updated_at": utc_now(), "error": result["error"]})
            _write_json(meta_path, meta)
        _write_json(result_path, result)
        raise


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _reconcile_detached_state(
    run_dir: Path,
    run_id: str,
    meta: dict[str, Any],
    result: dict[str, Any],
    gtestcov_status: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any], bool | None]:
    result_status = result.get("status")
    if result_status in {"done", "failed"}:
        return str(result_status), meta, result, None

    status = str(meta.get("status") or result_status or "not_started")
    process_alive: bool | None = None
    if status not in {"starting", "running"}:
        return status, meta, result, process_alive

    pid = _coerce_pid(meta.get("pid"))
    if pid is not None:
        process_alive = _pid_is_running(pid)
        if process_alive is False:
            return _mark_detached_failed(
                run_dir,
                run_id,
                meta,
                gtestcov_status,
                stale_reason="pid_not_running",
                error=f"detached evidence worker pid {pid} exited before writing result",
                process_alive=False,
            )

    if _is_stale(meta):
        return _mark_detached_failed(
            run_dir,
            run_id,
            meta,
            gtestcov_status,
            stale_reason="stale_metadata",
            error=f"detached evidence worker has not updated status for {DETACHED_STALE_SECONDS} seconds",
            process_alive=process_alive,
        )

    return "running", meta, result, process_alive


def _mark_detached_failed(
    run_dir: Path,
    run_id: str,
    meta: dict[str, Any],
    gtestcov_status: dict[str, Any],
    *,
    stale_reason: str,
    error: str,
    process_alive: bool | None,
) -> tuple[str, dict[str, Any], dict[str, Any], bool | None]:
    now = utc_now()
    target = meta.get("target") or gtestcov_status.get("target", "")
    result = {
        "status": "failed",
        "run_id": run_id,
        "target": target,
        "error": error,
        "stale_reason": stale_reason,
        "pid": meta.get("pid"),
        "process_alive": process_alive,
        "updated_at": now,
    }
    meta.update(
        {
            "status": "failed",
            "updated_at": now,
            "error": error,
            "stale_reason": stale_reason,
            "process_alive": process_alive,
        }
    )
    _write_json(run_dir / DETACHED_META, meta)
    _write_json(run_dir / DETACHED_RESULT, result)
    update_run_status(
        run_dir,
        phase="evidence.detached_failed",
        step="evidence",
        command="gtestcov evidence status",
        target=target,
        current_operation="detached_worker_stale",
        notes=[error],
        extra={"detached_stale_reason": stale_reason, "detached_pid": meta.get("pid")},
    )
    return "failed", meta, result, process_alive


def _coerce_pid(value: Any) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _pid_is_running(pid: int) -> bool | None:
    if os.name == "nt":
        return _windows_pid_is_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _windows_pid_is_running(pid: int) -> bool | None:
    try:
        import ctypes
    except ImportError:
        return None
    kernel32 = ctypes.windll.kernel32
    synchronize = 0x00100000
    process_query_limited_information = 0x1000
    handle = kernel32.OpenProcess(synchronize | process_query_limited_information, False, pid)
    if not handle:
        error_invalid_parameter = 87
        error = kernel32.GetLastError()
        return False if error == error_invalid_parameter else None
    try:
        wait_object_0 = 0
        wait_timeout = 258
        result = kernel32.WaitForSingleObject(handle, 0)
        if result == wait_timeout:
            return True
        if result == wait_object_0:
            return False
        return None
    finally:
        kernel32.CloseHandle(handle)


def _is_stale(meta: dict[str, Any]) -> bool:
    updated_at = str(meta.get("updated_at") or meta.get("started_at") or "")
    if not updated_at:
        return False
    try:
        from datetime import datetime, timezone

        previous = datetime.fromisoformat(updated_at)
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc).replace(microsecond=0)
    except ValueError:
        return False
    return (now - previous).total_seconds() > DETACHED_STALE_SECONDS
