from __future__ import annotations

import json
import os
import re
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
            label: _skipped_command(command, "blocked_by_preflight")
            for label, command, _timeout in _command_plan(profile, build_timeout, test_timeout, coverage_timeout)
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

    for label, command, timeout_seconds in _command_plan(profile, build_timeout, test_timeout, coverage_timeout):
        update_run_status(
            run_dir,
            phase=f"verify.{label}.running",
            command="verify",
            target=target,
            current_operation=f"run_{label}_command",
            extra={"configured": bool(command), "timeout_seconds": timeout_seconds},
        )
        results["commands"][label] = _run_command(command, root, label, timeout_seconds, run_dir=run_dir, target=target)
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
    coverage_path = _find_coverage_report(root, run_dir, profile.build.coverage_xml)
    if coverage_path:
        coverage = parse_coverage_report(coverage_path, target=target)
        coverage["path"] = relpath(coverage_path, root)
    else:
        coverage = {"found": False, "line_rate_percent": None}
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
) -> list[tuple[str, str, int]]:
    return [
        (
            "build",
            profile.build.incremental_build_command or profile.build.build_command,
            profile.build.build_timeout_seconds if build_timeout is None else build_timeout,
        ),
        (
            "test",
            profile.build.filtered_test_command or profile.build.test_command,
            profile.build.test_timeout_seconds if test_timeout is None else test_timeout,
        ),
        (
            "coverage",
            profile.build.target_coverage_command or profile.build.coverage_command,
            profile.build.coverage_timeout_seconds if coverage_timeout is None else coverage_timeout,
        ),
    ]


def _run_command(
    command: str,
    cwd: Path,
    label: str = "",
    timeout_seconds: int = 600,
    *,
    run_dir: Path | None = None,
    target: str = "",
) -> dict[str, Any]:
    stdout_path, stderr_path = _command_log_paths(run_dir, label)
    if not command:
        _write_empty_logs(stdout_path, stderr_path)
        return {
            "configured": False,
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
        diagnostics.append(f"{label or 'command'} command failed to start: {exc}")
        if stderr_path:
            stderr_path.write_text(str(exc), encoding="utf-8")
        return {
            "configured": True,
            "returncode": 127,
            "stdout": "",
            "stderr": str(exc),
            "diagnostics": diagnostics,
            "timeout": False,
            "timeout_seconds": timeout_seconds,
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
        )
        return {
            "configured": True,
            "returncode": 124,
            "stdout": stdout_tail,
            "stderr": stderr_tail,
            "diagnostics": diagnostics,
            "timeout": True,
            "timeout_seconds": timeout_seconds,
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
        "returncode": returncode,
        "stdout": stdout_tail,
        "stderr": stderr_tail,
        "diagnostics": diagnostics,
        "timeout": False,
        "timeout_seconds": timeout_seconds,
        "stdout_tail_path": str(stdout_path) if stdout_path else "",
        "stderr_tail_path": str(stderr_path) if stderr_path else "",
    }


def _skipped_command(command: str, reason: str) -> dict[str, Any]:
    return {
        "configured": bool(command),
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "diagnostics": [reason],
        "skipped": True,
        "timeout": False,
    }


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
    }
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    md_path.write_text(
        "\n".join(
            [
                f"# {label} Command Timeout",
                "",
                f"- Timeout seconds: `{timeout_seconds}`",
                f"- Command: `{command}`",
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


def _find_coverage_report(root: Path, run_dir: Path, configured: str) -> Path | None:
    candidates = []
    if configured:
        candidates.extend([root / configured, run_dir / configured])
    candidates.extend(
        [
            root / "coverage.xml",
            root / "coverage" / "coverage.xml",
            root / "coverage" / "summary.txt",
            run_dir / "coverage.xml",
            run_dir / "summary.txt",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


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
