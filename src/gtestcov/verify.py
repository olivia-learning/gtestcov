from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .audit import audit_generated_tests
from .coverage_goal import read_coverage_goal
from .fs import resolve_run_dir
from .memory import refresh_memory
from .models import relpath
from .next_round import plan_next_round
from .preflight import preflight_check
from .profile import load_profile
from .run_status import update_run_status


COMMAND_TAIL_MAX_BYTES = 64 * 1024
COMMAND_HEARTBEAT_SECONDS = 0.5
COVERAGE_FRESHNESS_TOLERANCE_SECONDS = 2.0


def verify_iteration(
    project_root: Path,
    run_id: str = "latest",
    target: str = "",
    line_coverage: float | None = None,
    max_stagnant_rounds: int | None = None,
    min_improvement: float | None = None,
    build_timeout: int | None = None,
    test_timeout: int | None = None,
    coverage_timeout: int | None = None,
) -> dict[str, Any]:
    root = project_root.resolve()
    profile = load_profile(root)
    run_id, run_dir = resolve_run_dir(root, run_id)
    coverage_goal = read_coverage_goal(run_dir)
    target = target or coverage_goal.get("target", "")
    threshold = (
        float(line_coverage)
        if line_coverage is not None
        else float(coverage_goal.get("line_coverage", profile.coverage.changed_line if not target else profile.targets.default_line_coverage))
    )
    results: dict[str, Any] = {"run_id": run_id, "commands": {}, "coverage": {}, "audit": {}}
    update_run_status(
        run_dir,
        phase="verify.start",
        command="verify",
        target=target,
        current_operation="preflight",
        extra={"threshold_percent": threshold},
    )
    preflight = preflight_check(root, run_id, target, include_codrax=False)
    results["preflight"] = preflight
    results["audit"] = preflight["audit"]

    if not preflight["passed"]:
        update_run_status(
            run_dir,
            phase="verify.blocked_by_preflight",
            command="verify",
            target=target,
            current_operation="write_verify_json",
            last_artifact=str(run_dir / "preflight_fix_task.md"),
            extra={"violation_count": len(preflight["audit"]["violations"])},
        )
        results["blocked_by_preflight"] = True
        results["commands"] = {
            item["label"]: _skipped_command(item, "blocked_by_preflight", root)
            for item in _command_plan(profile, build_timeout, test_timeout, coverage_timeout)
        }
        results["coverage"] = {
            "found": False,
            "line_rate_percent": None,
            "target": target,
            "threshold_percent": threshold,
            "meets_threshold": False,
        }
        results["passed"] = False
        verify_path = run_dir / "verify.json"
        verify_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        (run_dir / "review_checklist.md").write_text(render_review_checklist(results), encoding="utf-8")
        refresh_memory(root, run_id)
        update_run_status(
            run_dir,
            phase="verify.done",
            command="verify",
            target=target,
            current_operation="done",
            last_artifact=str(verify_path),
            extra={"passed": False, "blocked_by_preflight": True},
        )
        return results

    for command_item in _command_plan(profile, build_timeout, test_timeout, coverage_timeout):
        label = command_item["label"]
        update_run_status(
            run_dir,
            phase=f"verify.{label}.running",
            command="verify",
            target=target,
            current_operation=f"run_{label}_command",
            extra={"configured": bool(command_item["command"]), "timeout_seconds": command_item["timeout_seconds"]},
        )
        results["commands"][label] = _run_command(command_item, root, run_dir=run_dir, target=target)
        update_run_status(
            run_dir,
            phase=f"verify.{label}.done",
            command="verify",
            target=target,
            current_operation="coverage_parse" if label == "coverage" else "run_next_command",
            extra={
                "configured": results["commands"][label]["configured"],
                "returncode": results["commands"][label]["returncode"],
                "timeout": results["commands"][label].get("timeout", False),
            },
        )

    update_run_status(
        run_dir,
        phase="verify.coverage_parse",
        command="verify",
        target=target,
        current_operation="find_coverage_report",
    )
    coverage_report = _find_coverage_report(root, run_dir, profile.build.coverage_xml, results["commands"].get("coverage", {}))
    results["coverage_report"] = coverage_report
    coverage_path = Path(coverage_report["selected_path"]) if coverage_report.get("selected_path") else None
    if coverage_path:
        coverage = parse_coverage_report(coverage_path, target=target)
        coverage["path"] = relpath(coverage_path, root)
    else:
        coverage = {"found": False, "line_rate_percent": None}
    coverage["report_freshness"] = coverage_report.get("freshness", "missing")
    coverage["report_reason"] = coverage_report.get("reason", "")
    coverage["selected_coverage_report"] = coverage_report.get("selected", "")
    coverage["selected_coverage_report_copy"] = coverage_report.get("selected_copy", "")
    coverage["selected_coverage_report_sha256"] = coverage_report.get("selected_sha256", "")
    coverage["target"] = target
    coverage["threshold_percent"] = threshold
    coverage["meets_threshold"] = (
        coverage.get("line_rate_percent") is not None and coverage["line_rate_percent"] >= threshold
    )
    results["coverage"] = coverage

    audit = preflight["audit"]
    coverage_ok = coverage["meets_threshold"] if target else (
        coverage["line_rate_percent"] is None or coverage["meets_threshold"]
    )
    results["passed"] = _commands_ok(results) and not audit["violations"] and coverage_ok

    verify_path = run_dir / "verify.json"
    verify_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    if target:
        update_run_status(
            run_dir,
            phase="verify.next_round",
            command="verify",
            target=target,
            current_operation="plan_next_round",
            extra={"coverage_meets_threshold": coverage["meets_threshold"]},
        )
        results["next_round"] = plan_next_round(
            root,
            run_id,
            max_stagnant_rounds=max_stagnant_rounds,
            min_improvement=min_improvement,
            record_iteration=True,
        )
        verify_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    (run_dir / "review_checklist.md").write_text(render_review_checklist(results), encoding="utf-8")
    refresh_memory(root, run_id)
    update_run_status(
        run_dir,
        phase="verify.done",
        command="verify",
        target=target,
        current_operation="done",
        last_artifact=str(verify_path),
        extra={"passed": results["passed"], "coverage": coverage},
    )
    return results


def _command_plan(
    profile,
    build_timeout: int | None = None,
    test_timeout: int | None = None,
    coverage_timeout: int | None = None,
) -> list[dict[str, Any]]:
    return [
        _command_plan_item(
            "build",
            profile.build.incremental_build_command,
            "profile.build.incremental_build_command",
            profile.build.build_command,
            "profile.build.build_command",
            profile.build.build_timeout_seconds,
            build_timeout,
        ),
        _command_plan_item(
            "test",
            profile.build.filtered_test_command,
            "profile.build.filtered_test_command",
            profile.build.test_command,
            "profile.build.test_command",
            profile.build.test_timeout_seconds,
            test_timeout,
        ),
        _command_plan_item(
            "coverage",
            profile.build.target_coverage_command,
            "profile.build.target_coverage_command",
            profile.build.coverage_command,
            "profile.build.coverage_command",
            profile.build.coverage_timeout_seconds,
            coverage_timeout,
        ),
    ]


def _command_plan_item(
    label: str,
    preferred_command: str,
    preferred_source: str,
    fallback_command: str,
    fallback_source: str,
    profile_timeout: int,
    timeout_override: int | None,
) -> dict[str, Any]:
    command = preferred_command or fallback_command
    source = preferred_source if preferred_command else fallback_source if fallback_command else "not_configured"
    return {
        "label": label,
        "command": command,
        "source": source,
        "timeout_seconds": profile_timeout if timeout_override is None else timeout_override,
        "timeout_source": f"profile.build.{label}_timeout_seconds" if timeout_override is None else "cli_override",
    }


def _run_command(
    command_item: dict[str, Any],
    cwd: Path,
    *,
    run_dir: Path | None = None,
    target: str = "",
) -> dict[str, Any]:
    label = str(command_item.get("label", ""))
    command = str(command_item.get("command", ""))
    timeout_seconds = int(command_item.get("timeout_seconds", 600) or 0)
    provenance = _command_provenance(command_item, cwd)
    stdout_path, stderr_path = _command_log_paths(run_dir, label)
    if not command:
        _write_empty_logs(stdout_path, stderr_path)
        return {
            "configured": False,
            "command": command,
            "provenance": provenance,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "diagnostics": [],
            "timeout": False,
            "timeout_seconds": timeout_seconds,
            "stdout_tail_path": str(stdout_path) if stdout_path else "",
            "stderr_tail_path": str(stderr_path) if stderr_path else "",
        }
    diagnostics: list[str] = []
    _write_empty_logs(stdout_path, stderr_path)
    start_epoch = time.time()
    started_at = _iso_from_epoch(start_epoch)
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
    except OSError as exc:
        finish_epoch = time.time()
        diagnostics.append(f"{label or 'command'} command failed to start: {exc}")
        if stderr_path:
            stderr_path.write_text(str(exc), encoding="utf-8")
        return {
            "configured": True,
            "command": command,
            "provenance": provenance,
            "returncode": 127,
            "stdout": "",
            "stderr": str(exc),
            "diagnostics": diagnostics,
            "timeout": False,
            "timeout_seconds": timeout_seconds,
            "started_at": started_at,
            "started_epoch": start_epoch,
            "finished_at": _iso_from_epoch(finish_epoch),
            "finished_epoch": finish_epoch,
            "stdout_tail_path": str(stdout_path) if stdout_path else "",
            "stderr_tail_path": str(stderr_path) if stderr_path else "",
        }
    threads = [
        threading.Thread(target=_stream_to_tail_log, args=(process.stdout, stdout_path), daemon=True),
        threading.Thread(target=_stream_to_tail_log, args=(process.stderr, stderr_path), daemon=True),
    ]
    for thread in threads:
        thread.start()
    started = time.monotonic()
    last_heartbeat = started
    timed_out = False
    while True:
        now = time.monotonic()
        elapsed = now - started
        if run_dir and now - last_heartbeat >= COMMAND_HEARTBEAT_SECONDS:
            update_run_status(
                run_dir,
                phase=f"verify.{label}.heartbeat",
                command="verify",
                target=target,
                current_operation=f"run_{label}_command",
                extra={
                    "configured": True,
                    "timeout_seconds": timeout_seconds,
                    "elapsed_seconds_current_command": round(elapsed, 3),
                    "stdout_tail_path": str(stdout_path) if stdout_path else "",
                    "stderr_tail_path": str(stderr_path) if stderr_path else "",
                },
            )
            last_heartbeat = now
        if process.poll() is not None:
            break
        if timeout_seconds and elapsed >= timeout_seconds:
            timed_out = True
            process_cleanup = _terminate_timed_out_process(process)
            break
        time.sleep(0.1)
    process.wait()
    finish_epoch = time.time()
    for thread in threads:
        thread.join(timeout=2)
    stdout_tail = _read_tail_log(stdout_path)
    stderr_tail = _read_tail_log(stderr_path)
    if timed_out:
        diagnostics.append(f"{label or 'command'} command timed out after {timeout_seconds} seconds")
        diagnostics.extend(process_cleanup["diagnostics"])
        artifacts = _write_timeout_artifacts(
            run_dir,
            label,
            command,
            timeout_seconds,
            stdout_tail,
            stderr_tail,
            process_cleanup,
            provenance,
        )
        return {
            "configured": True,
            "command": command,
            "provenance": provenance,
            "returncode": 124,
            "stdout": stdout_tail,
            "stderr": stderr_tail,
            "diagnostics": diagnostics,
            "timeout": True,
            "timeout_seconds": timeout_seconds,
            "started_at": started_at,
            "started_epoch": start_epoch,
            "finished_at": _iso_from_epoch(finish_epoch),
            "finished_epoch": finish_epoch,
            "stdout_tail_path": str(stdout_path) if stdout_path else "",
            "stderr_tail_path": str(stderr_path) if stderr_path else "",
            "timeout_artifacts": artifacts,
            "process_cleanup": process_cleanup,
        }
    returncode = process.returncode
    combined = f"{stdout_tail}\n{stderr_tail}"
    if label == "test" and re.search(r"No tests were found|No tests were run|Total Tests:\s*0", combined, re.I):
        diagnostics.append("test command completed without discovering tests")
        if returncode == 0:
            returncode = 1
    return {
        "configured": True,
        "command": command,
        "provenance": provenance,
        "returncode": returncode,
        "stdout": stdout_tail,
        "stderr": stderr_tail,
        "diagnostics": diagnostics,
        "timeout": False,
        "timeout_seconds": timeout_seconds,
        "started_at": started_at,
        "started_epoch": start_epoch,
        "finished_at": _iso_from_epoch(finish_epoch),
        "finished_epoch": finish_epoch,
        "stdout_tail_path": str(stdout_path) if stdout_path else "",
        "stderr_tail_path": str(stderr_path) if stderr_path else "",
    }


def _skipped_command(command_item: dict[str, Any], reason: str, cwd: Path) -> dict[str, Any]:
    command = str(command_item.get("command", ""))
    return {
        "configured": bool(command),
        "command": command,
        "provenance": _command_provenance(command_item, cwd),
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "diagnostics": [reason],
        "skipped": True,
        "timeout": False,
        "timeout_seconds": int(command_item.get("timeout_seconds", 0) or 0),
    }


def _command_provenance(command_item: dict[str, Any], cwd: Path) -> dict[str, Any]:
    command = str(command_item.get("command", ""))
    configured = bool(command)
    return {
        "label": str(command_item.get("label", "")),
        "command": command,
        "command_sha256": _sha256_text(command) if configured else "",
        "cwd": str(cwd),
        "source": str(command_item.get("source", "not_configured")),
        "timeout_seconds": int(command_item.get("timeout_seconds", 0) or 0),
        "timeout_source": str(command_item.get("timeout_source", "not_configured")),
        "shell": True,
        "requires_user_review": configured,
        "review_status": "profile_command_unverified" if configured else "not_configured",
        "risk_note": (
            "This command is executed through the shell from project_profile.yaml. "
            "Review project_profile.yaml before running verify on a new project."
            if configured
            else "No command is configured for this step."
        ),
    }


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso_from_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).replace(microsecond=0).isoformat()


def _command_log_paths(run_dir: Path | None, label: str) -> tuple[Path | None, Path | None]:
    if run_dir is None or not label:
        return None, None
    commands_dir = run_dir / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    return commands_dir / f"{label}.stdout.tail.log", commands_dir / f"{label}.stderr.tail.log"


def _write_empty_logs(stdout_path: Path | None, stderr_path: Path | None) -> None:
    for path in (stdout_path, stderr_path):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")


def _stream_to_tail_log(stream, path: Path | None) -> None:
    if stream is None or path is None:
        return
    try:
        with path.open("a", encoding="utf-8", errors="replace") as handle:
            for chunk in stream:
                handle.write(chunk)
                handle.flush()
                _trim_tail_log(path)
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _trim_tail_log(path: Path, limit: int = COMMAND_TAIL_MAX_BYTES) -> None:
    if not path.exists() or path.stat().st_size <= limit:
        return
    data = path.read_bytes()
    path.write_bytes(data[-limit:])


def _read_tail_log(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return _tail_text(path.read_bytes(), COMMAND_TAIL_MAX_BYTES)


def _write_timeout_artifacts(
    run_dir: Path | None,
    label: str,
    command: str,
    timeout_seconds: int,
    stdout_tail: str,
    stderr_tail: str,
    process_cleanup: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, str]:
    if run_dir is None or not label:
        return {}
    commands_dir = run_dir / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    json_path = commands_dir / f"{label}.timeout.json"
    md_path = commands_dir / f"{label}.timeout.md"
    data = {
        "label": label,
        "command": command,
        "timeout_seconds": timeout_seconds,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "process_cleanup": process_cleanup,
        "provenance": provenance,
    }
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    md_path.write_text(
        "\n".join(
            [
                f"# {label} Command Timeout",
                "",
                f"- Timeout seconds: `{timeout_seconds}`",
                f"- Command: `{command}`",
                f"- Command source: `{provenance.get('source', '')}`",
                f"- Command SHA256: `{provenance.get('command_sha256', '')}`",
                f"- CWD: `{provenance.get('cwd', '')}`",
                f"- Shell: `{str(provenance.get('shell', True)).lower()}`",
                f"- Command review status: `{provenance.get('review_status', '')}`",
                f"- Process cleanup method: `{process_cleanup.get('method', '')}`",
                f"- Process tree guaranteed: `{str(process_cleanup.get('process_tree_guaranteed', False)).lower()}`",
                f"- Manual check recommended: `{str(process_cleanup.get('manual_check_recommended', False)).lower()}`",
                f"- Cleanup warning: {process_cleanup.get('warning', '')}",
                "",
                "## Stdout Tail",
                "```text",
                stdout_tail,
                "```",
                "",
                "## Stderr Tail",
                "```text",
                stderr_tail,
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {"json": str(json_path), "markdown": str(md_path)}


def _terminate_timed_out_process(process: subprocess.Popen[str]) -> dict[str, Any]:
    cleanup = {
        "method": "process.kill",
        "pid": process.pid,
        "shell": True,
        "platform": os.name,
        "process_tree_guaranteed": False,
        "manual_check_recommended": os.name == "nt",
        "warning": (
            "On Windows, subprocess.kill() with shell=True can terminate the shell without proving "
            "that all child processes exited; inspect the build/test tool if it may spawn long-lived children."
            if os.name == "nt"
            else "subprocess.kill() terminates the shell process; child process-tree cleanup is not guaranteed."
        ),
        "diagnostics": [],
    }
    try:
        process.kill()
        cleanup["sent_kill"] = True
    except OSError as exc:
        cleanup["sent_kill"] = False
        cleanup["kill_error"] = f"{type(exc).__name__}: {exc}"
    cleanup["diagnostics"].append(cleanup["warning"])
    return cleanup


def _tail_text(value: str | bytes | None, limit: int = 8000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[-limit:]


def _commands_ok(results: dict[str, Any]) -> bool:
    for command in results["commands"].values():
        if command["configured"] and command["returncode"] != 0:
            return False
    return True


def _find_coverage_report(root: Path, run_dir: Path, configured: str, coverage_command: dict[str, Any]) -> dict[str, Any]:
    candidates = _coverage_report_candidates(root, run_dir, configured)
    for candidate in candidates:
        _classify_coverage_candidate(candidate, coverage_command)

    selected = next((candidate for candidate in candidates if candidate["exists"] and candidate["freshness"] == "fresh"), None)
    if selected is None and not coverage_command.get("configured"):
        selected = next(
            (candidate for candidate in candidates if candidate["exists"] and candidate["freshness"] == "unknown_existing_report"),
            None,
        )

    result = {
        "selected": "",
        "selected_path": "",
        "selected_copy": "",
        "selected_copy_path": "",
        "selected_sha256": "",
        "freshness": "missing",
        "reason": "no_coverage_report_found",
        "freshness_tolerance_seconds": COVERAGE_FRESHNESS_TOLERANCE_SECONDS,
        "coverage_command_started_at": coverage_command.get("started_at", ""),
        "coverage_command_start_epoch": coverage_command.get("started_epoch"),
        "coverage_command_finished_at": coverage_command.get("finished_at", ""),
        "coverage_command_end_epoch": coverage_command.get("finished_epoch"),
        "coverage_command_returncode": coverage_command.get("returncode"),
        "coverage_command_timeout": coverage_command.get("timeout", False),
        "candidates": candidates,
    }
    if selected is None:
        if any(candidate["exists"] for candidate in candidates):
            result["freshness"] = "stale_or_unverified"
            result["reason"] = "coverage_report_stale_or_unverified"
        return result

    selected_path = Path(selected["path"])
    result.update(
        {
            "selected": selected["display_path"],
            "selected_path": str(selected_path),
            "selected_sha256": selected["sha256"],
            "freshness": selected["freshness"],
            "reason": selected["reason"],
        }
    )
    copied = _copy_selected_coverage_report(root, run_dir, selected_path)
    if copied:
        result["selected_copy"] = relpath(copied, root)
        result["selected_copy_path"] = str(copied)
    return result


def _coverage_report_candidates(root: Path, run_dir: Path, configured: str) -> list[dict[str, Any]]:
    raw_candidates: list[tuple[Path, str]] = []
    if configured:
        raw_candidates.extend([(run_dir / configured, "configured_path"), (root / configured, "configured_path")])
    raw_candidates.extend(
        [
            (run_dir / "coverage.xml", "run_dir"),
            (run_dir / "summary.txt", "run_dir"),
            (root / "coverage.xml", "project_root"),
            (root / "coverage" / "coverage.xml", "coverage_dir"),
            (root / "coverage" / "summary.txt", "coverage_dir"),
        ]
    )
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_path, source in raw_candidates:
        path = raw_path.resolve()
        key = str(path).lower() if os.name == "nt" else str(path)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(_coverage_candidate_record(root, path, source))
    return candidates


def _coverage_candidate_record(root: Path, path: Path, source: str) -> dict[str, Any]:
    exists = path.exists()
    record: dict[str, Any] = {
        "path": str(path),
        "display_path": _display_path(root, path),
        "source": source,
        "exists": exists,
        "mtime_epoch": None,
        "mtime": "",
        "size_bytes": 0,
        "sha256": "",
        "freshness": "missing",
        "reason": "candidate_missing",
    }
    if not exists:
        return record
    stat = path.stat()
    record.update(
        {
            "mtime_epoch": stat.st_mtime,
            "mtime": _iso_from_epoch(stat.st_mtime),
            "size_bytes": stat.st_size,
            "sha256": _sha256_file(path),
        }
    )
    return record


def _classify_coverage_candidate(candidate: dict[str, Any], coverage_command: dict[str, Any]) -> None:
    if not candidate["exists"]:
        return
    if not coverage_command.get("configured"):
        candidate["freshness"] = "unknown_existing_report"
        candidate["reason"] = "no_coverage_command_run"
        return
    if coverage_command.get("timeout") or coverage_command.get("returncode") not in (0, None):
        candidate["freshness"] = "stale"
        candidate["reason"] = "coverage_command_failed_or_timed_out"
        return
    start_epoch = coverage_command.get("started_epoch")
    if start_epoch is None:
        candidate["freshness"] = "unknown"
        candidate["reason"] = "coverage_command_start_time_missing"
        return
    if float(candidate["mtime_epoch"]) >= float(start_epoch) - COVERAGE_FRESHNESS_TOLERANCE_SECONDS:
        candidate["freshness"] = "fresh"
        candidate["reason"] = "mtime_after_coverage_command_start"
    else:
        candidate["freshness"] = "stale"
        candidate["reason"] = "mtime_before_coverage_command_start"


def _copy_selected_coverage_report(root: Path, run_dir: Path, source: Path) -> Path | None:
    destination_dir = run_dir / "coverage"
    destination_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix or ".txt"
    destination = destination_dir / f"selected_coverage_report{suffix}"
    if source.resolve() == destination.resolve():
        return destination
    try:
        shutil.copy2(source, destination)
    except OSError:
        return None
    return destination


def _display_path(root: Path, path: Path) -> str:
    try:
        return relpath(path, root)
    except ValueError:
        return str(path)


def parse_coverage_report(path: Path, target: str = "") -> dict[str, Any]:
    if path.suffix.lower() == ".xml":
        return parse_gcovr_xml(path, target=target)
    return parse_gcovr_summary(path)


def parse_gcovr_xml(path: Path, target: str = "") -> dict[str, Any]:
    tree = ET.parse(path)
    root = tree.getroot()
    if target:
        target_norm = target.replace("\\", "/").lstrip("./")
        for element in root.iter():
            filename = element.attrib.get("filename")
            if not filename:
                continue
            filename_norm = filename.replace("\\", "/").lstrip("./")
            if filename_norm == target_norm or filename_norm.endswith("/" + target_norm):
                percent = _line_rate_percent(element)
                return {
                    "found": percent is not None,
                    "target_found": True,
                    "target": target,
                    "line_rate_percent": percent,
                }
        return {"found": False, "target_found": False, "target": target, "line_rate_percent": None}
    percent = _line_rate_percent(root)
    return {"found": True, "line_rate_percent": percent}


def _line_rate_percent(element: ET.Element) -> float | None:
    rate = element.attrib.get("line-rate")
    if rate is None:
        lines_valid = float(element.attrib.get("lines-valid", "0") or 0)
        lines_covered = float(element.attrib.get("lines-covered", "0") or 0)
        percent = 100.0 * lines_covered / lines_valid if lines_valid else None
    else:
        percent = float(rate) * 100.0
    return percent


def parse_gcovr_summary(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"^lines:\s*([0-9]+(?:\.[0-9]+)?)%", text, re.MULTILINE)
    if not match:
        match = re.search(r"^TOTAL\s+\d+\s+\d+\s+([0-9]+(?:\.[0-9]+)?)%", text, re.MULTILINE)
    percent = float(match.group(1)) if match else None
    return {"found": True, "line_rate_percent": percent}


def render_review_checklist(results: dict[str, Any]) -> str:
    lines = ["# gtestcov Review Checklist", ""]
    for label, command in results["commands"].items():
        status = "skipped" if command.get("skipped") or not command["configured"] else ("passed" if command["returncode"] == 0 else "failed")
        lines.append(f"- {label} command: {status}")
    if results.get("blocked_by_preflight"):
        lines.append("- preflight: blocked build/test/coverage")
    coverage = results["coverage"]
    if coverage["line_rate_percent"] is None:
        lines.append("- coverage: skipped or XML not found")
    else:
        lines.append(
            f"- coverage: {coverage['line_rate_percent']:.2f}% "
            f"(threshold {coverage['threshold_percent']:.2f}%)"
        )
    if results["audit"]["violations"]:
        lines.append("- generated-test audit: failed")
        for violation in results["audit"]["violations"]:
            lines.append(f"  - {violation['check']} in `{violation['path']}`")
    else:
        lines.append("- generated-test audit: passed")
    coverage_report = results.get("coverage_report") or {}
    if coverage_report:
        lines.append("")
        lines.append("## Coverage Report Freshness")
        lines.append(f"- selected: `{coverage_report.get('selected') or 'none'}`")
        lines.append(f"- freshness: `{coverage_report.get('freshness')}`")
        lines.append(f"- reason: `{coverage_report.get('reason')}`")
        if coverage_report.get("selected_copy"):
            lines.append(f"- archived copy: `{coverage_report['selected_copy']}`")
        stale = [
            candidate
            for candidate in coverage_report.get("candidates", [])
            if candidate.get("exists") and candidate.get("freshness") in {"stale", "unknown"}
        ]
        if stale:
            lines.append("- stale or unverified candidates ignored:")
            for candidate in stale:
                lines.append(f"  - `{candidate.get('display_path')}`: {candidate.get('reason')}")
        if coverage_report.get("freshness") == "unknown_existing_report":
            lines.append("- warning: no coverage command ran, so the existing report was parsed with unknown freshness.")
        if coverage_report.get("freshness") == "stale_or_unverified":
            lines.append("- warning: coverage is unavailable because no fresh report was produced by the coverage command.")
    if results.get("commands"):
        lines.append("")
        lines.append("## Profile Command Provenance")
        lines.append("- Review project_profile.yaml before running verify on a new project; configured commands execute with shell=true from the project root.")
        for label, command in results["commands"].items():
            provenance = command.get("provenance") or {}
            lines.append(
                f"- {label}: source=`{provenance.get('source', '')}`, "
                f"cwd=`{provenance.get('cwd', '')}`, "
                f"shell=`{str(provenance.get('shell', True)).lower()}`, "
                f"sha256=`{provenance.get('command_sha256', '')}`, "
                f"review=`{provenance.get('review_status', '')}`"
            )
    if not coverage.get("meets_threshold", True) and coverage["line_rate_percent"] is not None:
        lines.append("")
        lines.append("## Coverage Gap Summary")
        lines.append("- Coverage is below threshold. Generate targeted tests for uncovered branches before broad refactors.")
    next_round = results.get("next_round") or {}
    if next_round:
        lines.append("")
        lines.append("## Next Round")
        lines.append(f"- status: {next_round.get('status')}")
        if next_round.get("next_task_path"):
            lines.append(f"- next task: `{next_round['next_task_path']}`")
        if next_round.get("stagnation_report_path"):
            lines.append(f"- stagnation report: `{next_round['stagnation_report_path']}`")
    return "\n".join(lines) + "\n"
