from __future__ import annotations

import os
import queue
import re
import signal
import shlex
import shutil
import subprocess
import threading
import time
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .fs import ensure_run_dir
from .models import CodraxEvidence, CodraxEvidenceConfig, ProjectProfile
from .profile import load_profile
from .run_status import CODRAX_STATUS, append_run_event, update_run_status, utc_now


FILE_LINE_RE = re.compile(
    r"(?<![\w/\\.-])"
    r"(?P<path>(?:[A-Za-z]:[\\/])?(?:[A-Za-z0-9_.@+~-]+[\\/])*"
    r"[A-Za-z0-9_.@+~-]+\."
    r"(?:c|cc|cpp|cxx|h|hh|hpp|hxx|cmake|txt|xml|yaml|yml|ini|md|json|sh|bash|py|ps1|bat|cmd|mk))"
    r":(?P<line>\d+)(?:-\d+)?",
    re.I,
)
SYMBOL_RE = re.compile(
    r"\b(?:[A-Za-z_][A-Za-z0-9_]*::[A-Za-z_][A-Za-z0-9_]*|"
    r"[A-Z][A-Z0-9]+_[A-Za-z0-9_]+|"
    r"[A-Za-z_][A-Za-z0-9_]+\(\))\b"
)
DEPENDENCY_HINT_RE = re.compile(r"\b(depend|api|include|external|osal|hal|nvm|driver|shim|mock|fake)\b", re.I)
HARNESS_HINT_RE = re.compile(r"\b(test|tester|harness|fixture|ut)\b", re.I)
RISK_HINT_RE = re.compile(r"\b(risk|hazard|unsafe|hardware|register|mmio|init|shutdown|osal|hal|nvm|thread|timer)\b", re.I)
CODRAX_FINAL_OUTPUT_DIR = "codrax_final_outputs"
CODRAX_FINAL_OUTPUT_INDEX = "index.json"
CODRAX_LATEST_FINAL_LOG = "codrax_final_log.md"


class _CodraxTerminatedBySignal(BaseException):
    def __init__(self, signum: int) -> None:
        super().__init__(f"CODRAX request interrupted by signal {signum}")
        self.signum = signum


def build_codrax_request(target: str) -> str:
    return f"""Read-only repository analysis for embedded C++ GoogleTest planning.

Target: {target}

Answer with concise bullets and cite real file:line evidence for every factual claim.
If a fact is not visible in the repository, write "not found" instead of guessing.
Do not propose production edits and do not generate tests.

Questions:
1. What is the target responsibility and main behavior?
2. Which direct dependencies, collaborator symbols, and external APIs does it use?
3. Are there existing tests, harnesses, fixtures, generated test bases, or support fakes to reuse?
4. What build/test entry points appear relevant?
5. What Init/Start/Stop/Shutdown or teardown requirements are visible?
6. What hardware, OSAL, HAL, NVM, message, queue, protocol, timer, thread, or async boundaries are visible?
7. What risks should a weak AI avoid when writing host-side gtest?
"""


def collect_codrax_evidence(
    project_root: Path,
    target: str,
    profile: ProjectProfile,
    run_dir: Path | None = None,
) -> CodraxEvidence:
    cfg = profile.evidence.codrax
    if not cfg.enabled:
        return CodraxEvidence(enabled=False, command=cfg.command, invocation=cfg.invocation, status="disabled")
    return execute_codrax_request(project_root.resolve(), cfg, build_codrax_request(target), enabled=True, run_dir=run_dir)


def execute_codrax_request(
    project_root: Path,
    cfg: CodraxEvidenceConfig,
    request: str,
    *,
    enabled: bool = True,
    run_dir: Path | None = None,
    operation_name: str = "codrax",
) -> CodraxEvidence:
    return _execute_codrax_request(
        project_root.resolve(),
        cfg,
        request,
        enabled=enabled,
        run_dir=run_dir,
        operation_name=operation_name,
    )


def generate_codrax_evidence(project_root: Path, target: str, run_id: str | None = None) -> tuple[CodraxEvidence, Path]:
    root = project_root.resolve()
    profile = load_profile(root)
    run_id, run_dir = ensure_run_dir(root, run_id)
    evidence = collect_codrax_evidence(root, target, profile, run_dir=run_dir)
    evidence_path = write_codrax_evidence(run_dir, evidence)
    return evidence, evidence_path


def codrax_check(project_root: Path, profile: ProjectProfile | None = None, run_id: str | None = None) -> dict[str, Any]:
    root = project_root.resolve()
    active_profile = profile or load_profile(root)
    cfg = active_profile.evidence.codrax
    probe_cfg = cfg.model_copy(update={"enabled": True})
    active_run_id, run_dir = ensure_run_dir(root, run_id or "codrax-check")
    update_run_status(
        run_dir,
        phase="codrax_check.start",
        step="codrax-check",
        command="gtestcov codrax-check",
        current_operation="checking_codrax_cli",
    )
    request = """Read-only repository citation probe for gtestcov.

This is only a gtestcov check that the CODRAX CLI can read the repository and return a real file:line citation.
Do not search for CODRAX integration inside the repository. The cited file does not need to mention CODRAX.

Please cite one real repository build, profile, source, or test file as file:line.
Do not use gtestcov/CODRAX tool artifacts as the citation, including project_profile.yaml, .gtestcov, or .codrax files.
If no file:line can be cited, say so explicitly.
Do not edit files.
"""
    evidence = _execute_codrax_request(
        root,
        probe_cfg,
        request,
        enabled=True,
        run_dir=run_dir,
        operation_name="codrax_check",
    )
    discovery = discover_codrax_cli(cfg)
    result = {
        "run_id": active_run_id,
        "run_dir": str(run_dir),
        "profile_enabled": cfg.enabled,
        "command": cfg.command,
        "configured_invocation": cfg.invocation,
        "selected_invocation": evidence.invocation,
        "discovery": discovery,
        "available": evidence.available,
        "status": evidence.status,
        "returncode": evidence.returncode,
        "require_file_line": cfg.require_file_line,
        "file_line_refs": evidence.file_line_refs,
        "timeout_kind": evidence.timeout_kind,
        "status_path": evidence.status_path,
        "native_log_dir": evidence.native_log_dir,
        "native_log_files": evidence.native_log_files,
        "final_log_path": evidence.final_log_path,
        "final_log_truncated": evidence.final_log_truncated,
        "final_log_size_bytes": evidence.final_log_size_bytes,
        "notes": evidence.notes,
        "stdout_excerpt": evidence.stdout_excerpt,
        "stderr_excerpt": evidence.stderr_excerpt,
    }
    summary_path = run_dir / "codrax_check.json"
    summary_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    update_run_status(
        run_dir,
        phase="codrax_check.done",
        step="codrax-check",
        command="gtestcov codrax-check",
        current_operation="codrax_check_complete",
        last_artifact=str(summary_path),
        notes=[f"status={evidence.status}", f"returncode={evidence.returncode}"],
        extra={"codrax_status": evidence.status},
    )
    return result


def write_codrax_evidence(run_dir: Path, evidence: CodraxEvidence) -> Path:
    json_path = run_dir / "codrax_evidence.json"
    md_path = run_dir / "codrax_evidence.md"
    json_path.write_text(evidence.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text("# CODRAX Evidence\n\n" + render_codrax_evidence(evidence, include_raw=True), encoding="utf-8")
    return json_path


def render_codrax_evidence(evidence: CodraxEvidence, include_raw: bool = False) -> str:
    lines = [
        f"- Status: `{evidence.status}`",
        f"- Enabled: `{str(evidence.enabled).lower()}`",
        f"- Available: `{str(evidence.available).lower()}`",
        f"- Command: `{evidence.command}`",
        f"- Invocation: `{evidence.invocation or 'not selected'}`",
    ]
    if evidence.returncode is not None:
        lines.append(f"- Return code: `{evidence.returncode}`")
    if evidence.timeout_kind:
        lines.append(f"- Timeout kind: `{evidence.timeout_kind}`")
    if evidence.status_path:
        lines.append(f"- CODRAX status: `{evidence.status_path}`")
    if evidence.native_log_dir:
        lines.append(f"- CODRAX native log dir: `{evidence.native_log_dir}`")
    if evidence.native_log_files:
        lines.append(f"- CODRAX native log files: `{len(evidence.native_log_files)}`")
    if evidence.final_log_path:
        lines.append(f"- Final diagnostic log: `{evidence.final_log_path}`")
    if evidence.notes:
        lines.append("")
        lines.append("### Notes")
        lines.extend(_bullets(evidence.notes))
    lines.append("")
    lines.append("### Related Files")
    lines.extend(_bullets(evidence.related_files or ["none"]))
    lines.append("")
    lines.append("### File:line References")
    lines.extend(_bullets(evidence.file_line_refs or ["none"]))
    lines.append("")
    lines.append("### Symbols")
    lines.extend(_bullets(evidence.symbols or ["none"]))
    lines.append("")
    lines.append("### Dependencies And Boundaries")
    lines.extend(_bullets(evidence.dependencies or ["none"]))
    lines.append("")
    lines.append("### Existing Tests Or Harnesses")
    lines.extend(_bullets(evidence.harnesses or ["none"]))
    lines.append("")
    lines.append("### Risks")
    lines.extend(_bullets(evidence.risks or ["none"]))
    if include_raw:
        if evidence.stdout_excerpt:
            lines.append("")
            lines.append("### Raw stdout excerpt")
            lines.append("```text")
            lines.append(evidence.stdout_excerpt)
            lines.append("```")
        if evidence.stderr_excerpt:
            lines.append("")
            lines.append("### Raw stderr excerpt")
            lines.append("```text")
            lines.append(evidence.stderr_excerpt)
            lines.append("```")
        if evidence.native_log_tail_excerpt:
            lines.append("")
            lines.append("### CODRAX native log tail")
            lines.append("```text")
            lines.append(evidence.native_log_tail_excerpt)
            lines.append("```")
    return "\n".join(lines).rstrip() + "\n"


def parse_codrax_output(output: str, evidence: CodraxEvidence) -> CodraxEvidence:
    parsed = _empty_parse_accumulator()
    for raw_line in output.splitlines():
        _collect_codrax_line(raw_line, parsed)
    return _apply_parse_accumulator(evidence, parsed)


def _empty_parse_accumulator() -> dict[str, list[str]]:
    return {
        "refs": [],
        "symbols": [],
        "dependencies": [],
        "harnesses": [],
        "risks": [],
        "notes": [],
    }


def _collect_codrax_line(raw_line: str, parsed: dict[str, list[str]]) -> None:
    line = raw_line.strip()
    if not line:
        return
    parsed["refs"].extend(_format_file_ref(match) for match in FILE_LINE_RE.finditer(line))
    parsed["symbols"].extend(_normalize_symbol(match.group(0)) for match in SYMBOL_RE.finditer(line))
    if DEPENDENCY_HINT_RE.search(line):
        parsed["dependencies"].append(_trim_line(line))
    if HARNESS_HINT_RE.search(line):
        parsed["harnesses"].append(_trim_line(line))
    if RISK_HINT_RE.search(line):
        parsed["risks"].append(_trim_line(line))
    if "not found" in line.lower() or "insufficient" in line.lower():
        parsed["notes"].append(_trim_line(line))


def _apply_parse_accumulator(evidence: CodraxEvidence, parsed: dict[str, list[str]]) -> CodraxEvidence:
    evidence.file_line_refs = _dedupe(parsed["refs"])[:80]
    evidence.related_files = _dedupe([ref.rsplit(":", 1)[0] for ref in evidence.file_line_refs])[:80]
    evidence.symbols = _dedupe(parsed["symbols"])[:80]
    evidence.dependencies = _dedupe(parsed["dependencies"])[:40]
    evidence.harnesses = _dedupe(parsed["harnesses"])[:40]
    evidence.risks = _dedupe(parsed["risks"])[:40]
    evidence.notes = _dedupe([*evidence.notes, *parsed["notes"]])[:40]
    return evidence


def _execute_codrax_request(
    project_root: Path,
    cfg: CodraxEvidenceConfig,
    request: str,
    *,
    enabled: bool,
    run_dir: Path | None = None,
    operation_name: str = "codrax",
) -> CodraxEvidence:
    evidence = CodraxEvidence(
        enabled=enabled,
        command=cfg.command,
        invocation=cfg.invocation,
        request=request,
        status="unavailable",
    )
    if not enabled:
        evidence.status = "disabled"
        evidence.notes.append("CODRAX is disabled for this request.")
        if run_dir:
            evidence.status_path = str(run_dir / CODRAX_STATUS)
        _write_final_log(run_dir, evidence, [], {"tail_excerpt": ""}, cfg, operation_name)
        _write_codrax_status(
            run_dir,
            {
                "status": "disabled",
                "phase": "skipped",
                "operation": operation_name,
                "notes": evidence.notes,
            },
        )
        return evidence
    command_args = _split_command(cfg.command)
    if run_dir:
        evidence.status_path = str(run_dir / CODRAX_STATUS)
    if not command_args:
        evidence.status = "command_not_found"
        evidence.notes.append("CODRAX command is empty.")
        _write_final_log(run_dir, evidence, [], {"tail_excerpt": ""}, cfg, operation_name)
        _write_codrax_status(run_dir, {"status": "command_not_found", "operation": operation_name, "notes": evidence.notes})
        return evidence

    resolved = _resolve_program(command_args[0])
    if not resolved:
        evidence.status = "command_not_found"
        evidence.notes.append(f"CODRAX command not found on PATH: {command_args[0]}")
        _write_final_log(run_dir, evidence, command_args, {"tail_excerpt": ""}, cfg, operation_name)
        _write_codrax_status(run_dir, {"status": "command_not_found", "operation": operation_name, "notes": evidence.notes})
        return evidence

    command_plan = _build_codrax_command(command_args, project_root, request, cfg)
    evidence.invocation = command_plan["selected_invocation"]
    evidence.available = True
    if not command_plan["supported"]:
        evidence.status = "unsupported_protocol"
        evidence.notes.extend(command_plan["notes"])
        _write_final_log(run_dir, evidence, command_args, {"tail_excerpt": ""}, cfg, operation_name)
        _write_codrax_status(run_dir, {"status": "unsupported_protocol", "operation": operation_name, "notes": evidence.notes})
        return evidence

    native_log_dir = _native_log_dir(run_dir, operation_name) if run_dir else None
    cmd, native_log_dir = _with_native_log_dir(command_plan["argv"], native_log_dir)
    status_path = run_dir / CODRAX_STATUS if run_dir else None
    if status_path:
        evidence.status_path = str(status_path)
    if native_log_dir:
        native_log_dir.mkdir(parents=True, exist_ok=True)
        evidence.native_log_dir = str(native_log_dir)
    status_state: dict[str, Any] = {
        "operation": operation_name,
        "status": "starting",
        "phase": "starting",
        "command": _redacted_command(cmd),
        "native_log_dir": str(native_log_dir) if native_log_dir else "",
        "status_path": str(status_path) if status_path else "",
        "idle_timeout_seconds": cfg.idle_timeout_seconds,
        "max_runtime_seconds": cfg.max_runtime_seconds,
        "started_at": utc_now(),
    }
    _write_codrax_status(run_dir, status_state)

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        evidence.status = "error"
        evidence.notes.append(f"CODRAX failed to start: {exc}")
        _write_final_log(run_dir, evidence, cmd, {"tail_excerpt": ""}, cfg, operation_name)
        _write_codrax_status(
            run_dir,
            {**status_state, "status": "error", "phase": "start_failed", "notes": evidence.notes},
        )
        return evidence

    _write_codrax_status(run_dir, {**status_state, "status": "running", "phase": "running", "pid": proc.pid})
    previous_signal_handlers = _install_codrax_signal_handlers(proc)
    try:
        stdout_excerpt, stderr_excerpt, timeout_kind, parsed, native_state = _collect_streaming_process(
            proc,
            native_log_dir,
            cfg,
            run_dir,
            status_state,
        )
    except _CodraxTerminatedBySignal as exc:
        native_state = _native_log_snapshot(native_log_dir, cfg) if native_log_dir else {"files": [], "tail_excerpt": ""}
        evidence.returncode = proc.returncode
        evidence.timeout_kind = "signal"
        evidence.status = "terminated_by_signal"
        evidence.native_log_files = native_state.get("files", [])
        evidence.native_log_tail_excerpt = native_state.get("tail_excerpt", "")
        _finalize_native_logs(evidence, native_log_dir, cfg)
        evidence.notes.append(f"gtestcov received signal {exc.signum}; CODRAX child process was terminated and final diagnostics were recorded.")
        _write_final_log(run_dir, evidence, cmd, native_state, cfg, operation_name)
        if run_dir:
            append_run_event(
                run_dir,
                "codrax.finished",
                step=operation_name,
                current_operation="codrax_interrupted_final_output_recorded",
                artifact=evidence.final_log_path,
                notes=[f"status={evidence.status}", f"signal={exc.signum}"],
            )
            update_run_status(
                run_dir,
                phase="codrax.interrupted",
                command=operation_name,
                current_operation="terminated_by_signal",
                last_artifact=evidence.final_log_path,
                notes=[f"CODRAX request interrupted by signal {exc.signum}."],
                extra={"codrax_status": evidence.status, "timeout_kind": evidence.timeout_kind},
            )
        _write_codrax_status(
            run_dir,
            {
                **status_state,
                "status": evidence.status,
                "phase": "interrupted",
                "pid": proc.pid,
                "returncode": evidence.returncode,
                "timeout_kind": evidence.timeout_kind,
                "native_log_files": evidence.native_log_files,
                "native_log_tail_excerpt": evidence.native_log_tail_excerpt,
                "codrax_reported_stage": _infer_codrax_stage(evidence.native_log_tail_excerpt),
                "native_log_last_line": _last_nonempty_line(evidence.native_log_tail_excerpt),
                "final_log_path": evidence.final_log_path,
                "notes": evidence.notes,
            },
        )
        raise SystemExit(128 + exc.signum)
    finally:
        _restore_signal_handlers(previous_signal_handlers)
    evidence.returncode = proc.returncode
    evidence.stdout_excerpt = stdout_excerpt.strip()
    evidence.stderr_excerpt = stderr_excerpt.strip()
    evidence.timeout_kind = timeout_kind
    evidence.native_log_files = native_state.get("files", [])
    evidence.native_log_tail_excerpt = native_state.get("tail_excerpt", "")
    _apply_parse_accumulator(evidence, parsed)
    _finalize_native_logs(evidence, native_log_dir, cfg)

    if timeout_kind:
        evidence.status = f"{timeout_kind}_timeout"
        if timeout_kind == "idle":
            evidence.notes.append(f"CODRAX produced no output for {cfg.idle_timeout_seconds} seconds.")
        else:
            evidence.notes.append(f"CODRAX exceeded max runtime of {cfg.max_runtime_seconds} seconds.")
    elif proc.returncode != 0:
        evidence.status = _classify_codrax_error(evidence.stderr_excerpt)
        evidence.notes.append("CODRAX returned a non-zero exit code.")
    elif cfg.require_file_line and not evidence.file_line_refs:
        evidence.status = "insufficient"
        evidence.notes.append("CODRAX output did not include required file:line evidence.")
    else:
        evidence.status = "ok"
    _write_final_log(run_dir, evidence, cmd, native_state, cfg, operation_name)
    if run_dir:
        append_run_event(
            run_dir,
            "codrax.finished",
            step=operation_name,
            current_operation="codrax_final_output_recorded",
            artifact=evidence.final_log_path,
            notes=[f"status={evidence.status}", f"returncode={evidence.returncode}"],
        )
    _write_codrax_status(
        run_dir,
        {
            **status_state,
            "status": evidence.status,
            "phase": "done",
            "pid": proc.pid,
            "returncode": evidence.returncode,
            "timeout_kind": evidence.timeout_kind,
            "native_log_files": evidence.native_log_files,
            "native_log_tail_excerpt": evidence.native_log_tail_excerpt,
            "codrax_reported_stage": _infer_codrax_stage(evidence.native_log_tail_excerpt),
            "native_log_last_line": _last_nonempty_line(evidence.native_log_tail_excerpt),
            "final_log_path": evidence.final_log_path,
            "notes": evidence.notes,
        },
    )
    return evidence


def _collect_streaming_process(
    proc: subprocess.Popen[str],
    native_log_dir: Path | None,
    cfg: CodraxEvidenceConfig,
    run_dir: Path | None,
    status_state: dict[str, Any],
) -> tuple[str, str, str, dict[str, list[str]], dict[str, Any]]:
    output_queue: queue.Queue[tuple[str, str]] = queue.Queue()
    readers = [
        threading.Thread(target=_read_stream, args=("stdout", proc.stdout, output_queue), daemon=True),
        threading.Thread(target=_read_stream, args=("stderr", proc.stderr, output_queue), daemon=True),
    ]
    for reader in readers:
        reader.start()

    started = time.monotonic()
    last_output = started
    stdout_excerpt = ""
    stderr_excerpt = ""
    timeout_kind = ""
    parsed = _empty_parse_accumulator()
    native_state: dict[str, Any] = {"files": [], "total_size": 0, "tail_excerpt": ""}
    last_activity = started
    last_status_update = 0.0
    last_native_total_size = 0

    while True:
        try:
            stream_name, text = output_queue.get(timeout=0.2)
            last_output = time.monotonic()
            last_activity = last_output
            if stream_name == "stdout":
                stdout_excerpt = _append_tail(stdout_excerpt, text, cfg.max_output_chars)
                _collect_codrax_line(text, parsed)
            else:
                stderr_excerpt = _append_tail(stderr_excerpt, text, cfg.max_output_chars)
            _maybe_update_codrax_status(
                run_dir,
                status_state,
                "running",
                proc.pid,
                started,
                last_activity,
                native_state,
                stdout_excerpt,
                stderr_excerpt,
                force=False,
            )
            continue
        except queue.Empty:
            pass

        now = time.monotonic()
        if native_log_dir and now - last_status_update >= max(0.2, cfg.status_update_interval_seconds):
            native_state = _native_log_snapshot(native_log_dir, cfg)
            if int(native_state.get("total_size", 0)) > last_native_total_size:
                last_native_total_size = int(native_state.get("total_size", 0))
                last_activity = now
            _maybe_update_codrax_status(
                run_dir,
                status_state,
                "running" if native_state.get("files") else "native_log_waiting",
                proc.pid,
                started,
                last_activity,
                native_state,
                stdout_excerpt,
                stderr_excerpt,
                force=True,
            )
            last_status_update = now

        if proc.poll() is not None:
            stdout_excerpt, stderr_excerpt = _drain_output_queue(
                output_queue,
                stdout_excerpt,
                stderr_excerpt,
                parsed,
                cfg,
            )
            if native_log_dir:
                native_state = _native_log_snapshot(native_log_dir, cfg)
            break

        if cfg.max_runtime_seconds > 0 and now - started >= cfg.max_runtime_seconds:
            timeout_kind = "max_runtime"
        elif cfg.idle_timeout_seconds > 0 and now - last_activity >= cfg.idle_timeout_seconds:
            timeout_kind = "idle"
        if timeout_kind:
            _terminate_process(proc)
            stdout_excerpt, stderr_excerpt = _drain_output_queue(
                output_queue,
                stdout_excerpt,
                stderr_excerpt,
                parsed,
                cfg,
            )
            if native_log_dir:
                native_state = _native_log_snapshot(native_log_dir, cfg)
            break

    for reader in readers:
        reader.join(timeout=0.5)
    return stdout_excerpt, stderr_excerpt, timeout_kind, parsed, native_state


def _read_stream(
    stream_name: str,
    pipe,
    output_queue: queue.Queue[tuple[str, str]],
) -> None:
    if pipe is None:
        return
    try:
        for line in pipe:
            output_queue.put((stream_name, line))
    finally:
        pipe.close()


def _drain_output_queue(
    output_queue: queue.Queue[tuple[str, str]],
    stdout_excerpt: str,
    stderr_excerpt: str,
    parsed: dict[str, list[str]],
    cfg: CodraxEvidenceConfig,
) -> tuple[str, str]:
    while True:
        try:
            stream_name, text = output_queue.get_nowait()
        except queue.Empty:
            return stdout_excerpt, stderr_excerpt
        if stream_name == "stdout":
            stdout_excerpt = _append_tail(stdout_excerpt, text, cfg.max_output_chars)
            _collect_codrax_line(text, parsed)
        else:
            stderr_excerpt = _append_tail(stderr_excerpt, text, cfg.max_output_chars)


def _native_log_dir(run_dir: Path | None, operation_name: str) -> Path | None:
    if not run_dir:
        return None
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", operation_name).strip("_") or "codrax"
    return run_dir / "codrax_native_logs" / safe_name


def _with_native_log_dir(cmd: list[str], native_log_dir: Path | None) -> tuple[list[str], Path | None]:
    if not native_log_dir:
        return cmd, None
    if "--log-dir" in cmd:
        index = cmd.index("--log-dir")
        if index + 1 < len(cmd):
            return cmd, Path(cmd[index + 1])
        return cmd, native_log_dir
    for item in cmd:
        if item.startswith("--log-dir="):
            return cmd, Path(item.split("=", 1)[1])
    return [*cmd, "--log-dir", str(native_log_dir)], native_log_dir


def _write_codrax_status(run_dir: Path | None, status: dict[str, Any]) -> None:
    if not run_dir:
        return
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / CODRAX_STATUS
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = path.read_text(encoding="utf-8")
            existing = json.loads(loaded)
            if not isinstance(existing, dict):
                existing = {}
        except Exception:
            existing = {}
    now = utc_now()
    started_at = status.get("started_at") or existing.get("started_at") or now
    merged = {
        **existing,
        **status,
        "started_at": started_at,
        "updated_at": now,
    }
    merged["elapsed_seconds"] = _elapsed_seconds(started_at, now)
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")


def _maybe_update_codrax_status(
    run_dir: Path | None,
    status_state: dict[str, Any],
    phase: str,
    pid: int | None,
    started: float,
    last_activity: float,
    native_state: dict[str, Any],
    stdout_excerpt: str,
    stderr_excerpt: str,
    *,
    force: bool,
) -> None:
    if not run_dir:
        return
    now = time.monotonic()
    if not force and now - float(status_state.get("_last_status_write", 0.0)) < 0.5:
        return
    status_state["_last_status_write"] = now
    _write_codrax_status(
        run_dir,
        {
            **{key: value for key, value in status_state.items() if not key.startswith("_")},
            "status": phase,
            "phase": phase,
            "pid": pid,
            "elapsed_seconds": round(max(0.0, now - started), 3),
            "seconds_since_last_output": round(max(0.0, now - last_activity), 3),
            "native_log_files": native_state.get("files", []),
            "native_log_tail_excerpt": native_state.get("tail_excerpt", ""),
            "codrax_reported_stage": _infer_codrax_stage(str(native_state.get("tail_excerpt", ""))),
            "native_log_last_line": _last_nonempty_line(str(native_state.get("tail_excerpt", ""))),
            "stdout_tail_excerpt": stdout_excerpt[-1000:],
            "stderr_tail_excerpt": stderr_excerpt[-1000:],
        },
    )


def _native_log_snapshot(native_log_dir: Path, cfg: CodraxEvidenceConfig) -> dict[str, Any]:
    if not native_log_dir.exists():
        return {"files": [], "total_size": 0, "tail_excerpt": ""}
    files = sorted([path for path in native_log_dir.rglob("*") if path.is_file()], key=lambda path: path.stat().st_mtime)
    total_size = sum(path.stat().st_size for path in files)
    tail_excerpt = ""
    if files:
        tail_excerpt = _read_file_tail(files[-1], cfg.native_log_tail_bytes)
    return {
        "files": [str(path) for path in files],
        "total_size": total_size,
        "tail_excerpt": tail_excerpt,
    }


def _read_file_tail(path: Path, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(-max_bytes, os.SEEK_END)
            data = handle.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def _finalize_native_logs(evidence: CodraxEvidence, native_log_dir: Path | None, cfg: CodraxEvidenceConfig) -> None:
    if not native_log_dir:
        return
    evidence.native_log_dir = str(native_log_dir)
    state = _native_log_snapshot(native_log_dir, cfg)
    evidence.native_log_files = state.get("files", [])
    evidence.native_log_tail_excerpt = state.get("tail_excerpt", "")


def _write_final_log(
    run_dir: Path | None,
    evidence: CodraxEvidence,
    cmd: list[str],
    native_state: dict[str, Any],
    cfg: CodraxEvidenceConfig,
    operation_name: str,
) -> None:
    if not run_dir:
        return
    path = _next_final_output_log_path(run_dir, operation_name)
    latest_path = run_dir / CODRAX_LATEST_FINAL_LOG
    final_stdout_label = "## CODRAX final stdout excerpt"
    final_stderr_label = "## CODRAX final stderr excerpt"
    native_tail = str(native_state.get("tail_excerpt", ""))
    text = "\n".join(
        [
            "# CODRAX Final Diagnostic Log",
            "",
            f"- Operation: `{operation_name}`",
            f"- Status: `{evidence.status}`",
            f"- Timeout kind: `{evidence.timeout_kind or 'none'}`",
            f"- Return code: `{evidence.returncode}`",
            f"- Command: `{_redacted_command(cmd)}`",
            f"- Native log dir: `{evidence.native_log_dir or 'none'}`",
            f"- Native log files: `{len(evidence.native_log_files)}`",
            f"- CODRAX reported stage: `{_infer_codrax_stage(native_tail) or 'unknown'}`",
            f"- Native log last line: `{_last_nonempty_line(native_tail) or 'none'}`",
            "",
            final_stdout_label,
            "```text",
            evidence.stdout_excerpt,
            "```",
            "",
            final_stderr_label,
            "```text",
            evidence.stderr_excerpt,
            "```",
            "",
            "## CODRAX native log tail",
            "```text",
            native_tail,
            "```",
        ]
    ).rstrip() + "\n"
    encoded = text.encode("utf-8")
    if cfg.final_log_max_bytes > 0 and len(encoded) > cfg.final_log_max_bytes:
        marker = "[gtestcov] final CODRAX diagnostic log truncated; keeping newest bytes.\n"
        keep = max(0, cfg.final_log_max_bytes - len(marker.encode("utf-8")))
        tail = encoded[-keep:] if keep else b""
        text = marker + tail.decode("utf-8", errors="replace")
        evidence.final_log_truncated = True
    path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")
    evidence.final_log_path = str(path)
    evidence.final_log_size_bytes = path.stat().st_size
    _append_final_output_index(run_dir, evidence, operation_name, path, latest_path)


def _next_final_output_log_path(run_dir: Path, operation_name: str) -> Path:
    directory = run_dir / CODRAX_FINAL_OUTPUT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    sequence = len(list(directory.glob("*.md"))) + 1
    safe_operation = re.sub(r"[^A-Za-z0-9_.-]+", "_", operation_name).strip("_") or "codrax"
    return directory / f"{sequence:04d}_{safe_operation}.md"


def _append_final_output_index(
    run_dir: Path,
    evidence: CodraxEvidence,
    operation_name: str,
    path: Path,
    latest_path: Path,
) -> None:
    directory = run_dir / CODRAX_FINAL_OUTPUT_DIR
    index_path = directory / CODRAX_FINAL_OUTPUT_INDEX
    existing: list[dict[str, Any]] = []
    if index_path.exists():
        try:
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = loaded
        except (OSError, json.JSONDecodeError):
            existing = []
    existing.append(
        {
            "ts": utc_now(),
            "operation": operation_name,
            "status": evidence.status,
            "returncode": evidence.returncode,
            "timeout_kind": evidence.timeout_kind,
            "final_log_path": str(path),
            "latest_final_log_path": str(latest_path),
            "stdout_excerpt_chars": len(evidence.stdout_excerpt),
            "stderr_excerpt_chars": len(evidence.stderr_excerpt),
            "native_log_dir": evidence.native_log_dir,
            "native_log_file_count": len(evidence.native_log_files),
            "file_line_ref_count": len(evidence.file_line_refs),
            "final_log_truncated": evidence.final_log_truncated,
            "final_log_size_bytes": evidence.final_log_size_bytes,
        }
    )
    index_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def _infer_codrax_stage(text: str) -> str:
    lowered_lines = [line.lower() for line in text.splitlines() if line.strip()]
    stage_patterns = [
        ("finalize", ("finalize", "finalizing")),
        ("extract", ("extract", "extraction")),
        ("explore", ("explore", "exploration")),
        ("analyze", ("analyze", "analysis", "analyzer", "prescan")),
        ("repo_map", ("repo_map", "tree-sitter", "multi-repo", "single-repo")),
        ("llm", ("[llm]", "provider", "adapter")),
        ("error", (" error ", "panic", "failed")),
    ]
    for line in reversed(lowered_lines):
        padded = f" {line} "
        for stage, tokens in stage_patterns:
            if any(token in padded for token in tokens):
                return stage
    return ""


def _last_nonempty_line(text: str, limit: int = 300) -> str:
    for line in reversed(text.splitlines()):
        normalized = " ".join(line.split())
        if normalized:
            if len(normalized) <= limit:
                return normalized
            return normalized[: limit - 3] + "..."
    return ""


def _classify_codrax_error(stderr: str) -> str:
    lowered = stderr.lower()
    if "llm.default.provider is required" in lowered or "provider config" in lowered and "not found" in lowered:
        return "provider_not_configured"
    return "error"


def _redacted_command(cmd: list[str]) -> str:
    rendered: list[str] = []
    skip_next = False
    for index, item in enumerate(cmd):
        if skip_next:
            skip_next = False
            continue
        lowered = item.lower()
        if lowered in {"--api-key", "--token", "--password"} and index + 1 < len(cmd):
            rendered.extend([item, "<redacted>"])
            skip_next = True
            continue
        if any(lowered.startswith(prefix) for prefix in ("--api-key=", "--token=", "--password=")):
            rendered.append(item.split("=", 1)[0] + "=<redacted>")
            continue
        rendered.append(item)
    return " ".join(shlex.quote(part) for part in rendered)


def _elapsed_seconds(started_at: str, ended_at: str) -> float:
    try:
        from datetime import datetime

        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(ended_at)
    except ValueError:
        return 0.0
    return round(max(0.0, (end - start).total_seconds()), 3)


def _install_codrax_signal_handlers(proc: subprocess.Popen[str]) -> dict[int, Any]:
    previous: dict[int, Any] = {}
    if threading.current_thread() is not threading.main_thread():
        return previous

    def _handle_signal(signum: int, _frame) -> None:
        _terminate_process(proc)
        raise _CodraxTerminatedBySignal(signum)

    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, _handle_signal)
        except (OSError, ValueError):
            previous.pop(signum, None)
    return previous


def _restore_signal_handlers(previous: dict[int, Any]) -> None:
    if threading.current_thread() is not threading.main_thread():
        return
    for signum, handler in previous.items():
        try:
            signal.signal(signum, handler)
        except (OSError, ValueError):
            pass


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    except OSError:
        proc.kill()
        proc.wait(timeout=5)


def discover_codrax_cli(cfg: CodraxEvidenceConfig) -> dict[str, Any]:
    command_args = _split_command(cfg.command)
    if not command_args:
        return {
            "command": cfg.command,
            "resolved_program": "",
            "selected_invocation": "",
            "supported": False,
            "notes": ["CODRAX command is empty."],
            "help_probes": [],
        }
    resolved = _resolve_program(command_args[0])
    if not resolved:
        return {
            "command": cfg.command,
            "resolved_program": "",
            "selected_invocation": "",
            "supported": False,
            "notes": [f"CODRAX command not found on PATH: {command_args[0]}"],
            "help_probes": [],
        }
    discovery = _discover_codrax_cli_cached(
        tuple(command_args),
        cfg.invocation,
        tuple(cfg.args_template),
        cfg.probe_timeout_seconds,
        cfg.max_output_chars,
    )
    return {**discovery, "command": cfg.command, "resolved_program": resolved}


def _build_codrax_command(
    command_args: list[str],
    project_root: Path,
    request: str,
    cfg: CodraxEvidenceConfig,
) -> dict[str, Any]:
    discovery = discover_codrax_cli(cfg)
    selected = discovery["selected_invocation"]
    if not discovery["supported"]:
        return {
            "argv": [],
            "selected_invocation": selected,
            "supported": False,
            "notes": discovery["notes"],
        }
    if selected == "args_template":
        tail = [_render_template_arg(arg, project_root, request) for arg in cfg.args_template]
    else:
        tail = _invocation_tail(selected, project_root, request)
    return {
        "argv": [*command_args, *tail],
        "selected_invocation": selected,
        "supported": True,
        "notes": [],
    }


@lru_cache(maxsize=64)
def _discover_codrax_cli_cached(
    command_args: tuple[str, ...],
    configured_invocation: str,
    args_template: tuple[str, ...],
    timeout_seconds: int,
    max_output_chars: int,
) -> dict[str, Any]:
    if args_template:
        return {
            "selected_invocation": "args_template",
            "supported": True,
            "notes": ["Using evidence.codrax.args_template; skipped local help probing."],
            "help_probes": [],
        }
    if configured_invocation != "auto":
        supported = configured_invocation in _known_invocations()
        return {
            "selected_invocation": configured_invocation,
            "supported": supported,
            "notes": ["Using configured CODRAX invocation; skipped local help probing."] if supported else [f"Unknown configured CODRAX invocation: {configured_invocation}"],
            "help_probes": [],
        }

    help_probes = _run_help_probes(list(command_args), timeout_seconds, max_output_chars)
    help_text = "\n".join(
        f"{probe.get('stdout_excerpt', '')}\n{probe.get('stderr_excerpt', '')}" for probe in help_probes
    )
    selected = _infer_invocation_from_help(help_text)
    if selected:
        return {
            "selected_invocation": selected,
            "supported": True,
            "notes": [f"Auto-detected CODRAX invocation from local help: {selected}"],
            "help_probes": help_probes,
        }
    return {
        "selected_invocation": "",
        "supported": False,
        "notes": [
            "Could not infer CODRAX CLI protocol from local help output.",
            "Set evidence.codrax.args_template, for example: ['ask', '--path', '{repo}', '--prompt', '{request}'].",
        ],
        "help_probes": help_probes,
    }


def _run_help_probes(command_args: list[str], timeout_seconds: int, max_output_chars: int) -> list[dict[str, Any]]:
    probes: list[dict[str, Any]] = []
    for tail in (["--help"], ["help"], ["--version"]):
        argv = [*command_args, *tail]
        try:
            completed = subprocess.run(
                argv,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            probes.append(
                {
                    "argv_tail": tail,
                    "returncode": completed.returncode,
                    "stdout_excerpt": _excerpt(completed.stdout, max_output_chars),
                    "stderr_excerpt": _excerpt(completed.stderr, max_output_chars),
                }
            )
        except subprocess.TimeoutExpired as exc:
            probes.append(
                {
                    "argv_tail": tail,
                    "returncode": None,
                    "stdout_excerpt": _excerpt(exc.stdout or "", max_output_chars),
                    "stderr_excerpt": _excerpt(exc.stderr or "", max_output_chars),
                    "diagnostic": f"probe timed out after {timeout_seconds} seconds",
                }
            )
        except OSError as exc:
            probes.append(
                {
                    "argv_tail": tail,
                    "returncode": None,
                    "stdout_excerpt": "",
                    "stderr_excerpt": str(exc),
                    "diagnostic": "probe failed to start",
                }
            )
    return probes


def _infer_invocation_from_help(help_text: str) -> str:
    text = help_text.lower()
    mentions_ask_usage = bool(re.search(r"\b(?:usage:|commands?:|subcommands?:).*?\bask\b", text, re.S))
    if mentions_ask_usage and "--repo" in text and "--request" in text:
        return "ask_repo_request_flags"
    if mentions_ask_usage and "--path" in text and "--prompt" in text:
        return "ask_path_prompt_flags"
    if "--repo" in text and "--request" in text:
        return "repo_request_flags"
    if "--repo" in text and "--prompt" in text:
        return "repo_prompt_flags"
    if "--repo" in text and "--query" in text:
        return "repo_query_flags"
    if "--path" in text and "--prompt" in text:
        return "path_prompt_flags"
    return ""


def _known_invocations() -> set[str]:
    return {
        "repo_request_flags",
        "repo_prompt_flags",
        "repo_query_flags",
        "path_prompt_flags",
        "ask_repo_request_flags",
        "ask_path_prompt_flags",
    }


def _invocation_tail(invocation: str, project_root: Path, request: str) -> list[str]:
    repo = str(project_root)
    if invocation == "repo_request_flags":
        return ["--repo", repo, "--request", request]
    if invocation == "repo_prompt_flags":
        return ["--repo", repo, "--prompt", request]
    if invocation == "repo_query_flags":
        return ["--repo", repo, "--query", request]
    if invocation == "path_prompt_flags":
        return ["--path", repo, "--prompt", request]
    if invocation == "ask_repo_request_flags":
        return ["ask", "--repo", repo, "--request", request]
    if invocation == "ask_path_prompt_flags":
        return ["ask", "--path", repo, "--prompt", request]
    return []


def _render_template_arg(arg: str, project_root: Path, request: str) -> str:
    return arg.replace("{repo}", str(project_root)).replace("{project_root}", str(project_root)).replace("{request}", request)


def _split_command(command: str) -> list[str]:
    if not command.strip():
        return []
    return [part.strip("\"'") for part in shlex.split(command, posix=(os.name != "nt"))]


def _resolve_program(program: str) -> str | None:
    expanded = Path(program).expanduser()
    if expanded.exists():
        return str(expanded)
    return shutil.which(program)


def _format_file_ref(match: re.Match[str]) -> str:
    path = match.group("path").replace("\\", "/")
    return f"{path}:{match.group('line')}"


def _normalize_symbol(symbol: str) -> str:
    return symbol[:-2] if symbol.endswith("()") else symbol


def _trim_line(line: str, limit: int = 240) -> str:
    normalized = " ".join(line.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _excerpt(text: str | bytes | None, max_chars: int) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if len(text) <= max_chars:
        return text.strip()
    return text[:max_chars].rstrip() + "\n...[truncated]"


def _append_tail(current: str, text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    combined = current + text
    if len(combined) <= max_chars:
        return combined
    return combined[-max_chars:]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _bullets(values: list[str]) -> list[str]:
    return [f"- {value}" for value in values]
