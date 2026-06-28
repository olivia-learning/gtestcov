from __future__ import annotations

import os
import queue
import re
import shlex
import shutil
import subprocess
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from .fs import ensure_run_dir
from .models import CodraxEvidence, CodraxEvidenceConfig, ProjectProfile
from .profile import load_profile


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
) -> CodraxEvidence:
    return _execute_codrax_request(project_root.resolve(), cfg, request, enabled=enabled, run_dir=run_dir)


def generate_codrax_evidence(project_root: Path, target: str, run_id: str | None = None) -> tuple[CodraxEvidence, Path]:
    root = project_root.resolve()
    profile = load_profile(root)
    run_id, run_dir = ensure_run_dir(root, run_id)
    evidence = collect_codrax_evidence(root, target, profile, run_dir=run_dir)
    evidence_path = write_codrax_evidence(run_dir, evidence)
    return evidence, evidence_path


def codrax_check(project_root: Path, profile: ProjectProfile | None = None) -> dict[str, Any]:
    root = project_root.resolve()
    active_profile = profile or load_profile(root)
    cfg = active_profile.evidence.codrax
    probe_cfg = cfg.model_copy(update={"enabled": True})
    request = """Read-only CODRAX integration probe.

Please cite one real repository build, profile, source, or test file as file:line.
If no file:line can be cited, say so explicitly.
Do not edit files.
"""
    evidence = _execute_codrax_request(root, probe_cfg, request, enabled=True)
    discovery = discover_codrax_cli(cfg)
    return {
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
        "live_log_path": evidence.live_log_path,
        "live_log_truncated": evidence.live_log_truncated,
        "live_log_size_bytes": evidence.live_log_size_bytes,
        "dropped_log_bytes": evidence.dropped_log_bytes,
        "notes": evidence.notes,
        "stdout_excerpt": evidence.stdout_excerpt,
        "stderr_excerpt": evidence.stderr_excerpt,
    }


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
    if evidence.live_log_path:
        lines.append(f"- Live log: `{evidence.live_log_path}`")
        lines.append(f"- Live log size: `{evidence.live_log_size_bytes}` bytes")
        lines.append(f"- Live log truncated: `{str(evidence.live_log_truncated).lower()}`")
        if evidence.dropped_log_bytes:
            lines.append(f"- Dropped live log bytes: `{evidence.dropped_log_bytes}`")
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
) -> CodraxEvidence:
    evidence = CodraxEvidence(
        enabled=enabled,
        command=cfg.command,
        invocation=cfg.invocation,
        request=request,
        status="unavailable",
    )
    command_args = _split_command(cfg.command)
    if not command_args:
        evidence.notes.append("CODRAX command is empty.")
        return evidence

    resolved = _resolve_program(command_args[0])
    if not resolved:
        evidence.notes.append(f"CODRAX command not found on PATH: {command_args[0]}")
        return evidence

    command_plan = _build_codrax_command(command_args, project_root, request, cfg)
    evidence.invocation = command_plan["selected_invocation"]
    evidence.available = True
    if not command_plan["supported"]:
        evidence.status = "unsupported_protocol"
        evidence.notes.extend(command_plan["notes"])
        return evidence

    cmd = command_plan["argv"]
    live_log_path = run_dir / "codrax_live.log" if run_dir else None
    log_state: dict[str, Any] = {"truncated": False, "dropped_log_bytes": 0}
    if live_log_path:
        evidence.live_log_path = str(live_log_path)
        live_log_path.parent.mkdir(parents=True, exist_ok=True)
        if live_log_path.exists():
            live_log_path.unlink()
        _append_live_log(
            live_log_path,
            "gtestcov",
            _render_live_log_header(cmd, project_root, request),
            cfg,
            log_state,
        )
        _maybe_open_observer(live_log_path, cfg, evidence)

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
        _finalize_live_log(evidence, live_log_path, log_state)
        return evidence

    stdout_excerpt, stderr_excerpt, timeout_kind, parsed = _collect_streaming_process(proc, live_log_path, cfg, log_state)
    evidence.returncode = proc.returncode
    evidence.stdout_excerpt = stdout_excerpt.strip()
    evidence.stderr_excerpt = stderr_excerpt.strip()
    evidence.timeout_kind = timeout_kind
    _apply_parse_accumulator(evidence, parsed)
    _finalize_live_log(evidence, live_log_path, log_state)

    if timeout_kind:
        evidence.status = "timeout"
        if timeout_kind == "idle":
            evidence.notes.append(f"CODRAX produced no output for {cfg.idle_timeout_seconds} seconds.")
        else:
            evidence.notes.append(f"CODRAX exceeded max runtime of {cfg.max_runtime_seconds} seconds.")
    elif proc.returncode != 0:
        evidence.status = "error"
        evidence.notes.append("CODRAX returned a non-zero exit code.")
    elif cfg.require_file_line and not evidence.file_line_refs:
        evidence.status = "insufficient"
        evidence.notes.append("CODRAX output did not include required file:line evidence.")
    else:
        evidence.status = "ok"
    return evidence


def _collect_streaming_process(
    proc: subprocess.Popen[str],
    live_log_path: Path | None,
    cfg: CodraxEvidenceConfig,
    log_state: dict[str, Any],
) -> tuple[str, str, str, dict[str, list[str]]]:
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

    while True:
        try:
            stream_name, text = output_queue.get(timeout=0.2)
            last_output = time.monotonic()
            if stream_name == "stdout":
                stdout_excerpt = _append_tail(stdout_excerpt, text, cfg.max_output_chars)
                _collect_codrax_line(text, parsed)
            else:
                stderr_excerpt = _append_tail(stderr_excerpt, text, cfg.max_output_chars)
            if live_log_path:
                _append_live_log(live_log_path, stream_name, text, cfg, log_state)
            continue
        except queue.Empty:
            pass

        if proc.poll() is not None:
            stdout_excerpt, stderr_excerpt = _drain_output_queue(
                output_queue,
                stdout_excerpt,
                stderr_excerpt,
                parsed,
                live_log_path,
                cfg,
                log_state,
            )
            break

        now = time.monotonic()
        if cfg.max_runtime_seconds > 0 and now - started >= cfg.max_runtime_seconds:
            timeout_kind = "max_runtime"
        elif cfg.idle_timeout_seconds > 0 and now - last_output >= cfg.idle_timeout_seconds:
            timeout_kind = "idle"
        if timeout_kind:
            _terminate_process(proc)
            stdout_excerpt, stderr_excerpt = _drain_output_queue(
                output_queue,
                stdout_excerpt,
                stderr_excerpt,
                parsed,
                live_log_path,
                cfg,
                log_state,
            )
            break

    for reader in readers:
        reader.join(timeout=0.5)
    return stdout_excerpt, stderr_excerpt, timeout_kind, parsed


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
    live_log_path: Path | None,
    cfg: CodraxEvidenceConfig,
    log_state: dict[str, Any],
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
        if live_log_path:
            _append_live_log(live_log_path, stream_name, text, cfg, log_state)


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


def _append_live_log(
    live_log_path: Path,
    stream_name: str,
    text: str,
    cfg: CodraxEvidenceConfig,
    log_state: dict[str, Any],
) -> None:
    live_log_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"[{stream_name}] "
    payload = prefix + text.replace("\r\n", "\n")
    with live_log_path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(payload)
        if not payload.endswith("\n"):
            handle.write("\n")
    _trim_live_log(live_log_path, cfg, log_state)


def _trim_live_log(live_log_path: Path, cfg: CodraxEvidenceConfig, log_state: dict[str, Any]) -> None:
    max_bytes = max(0, cfg.live_log_max_bytes)
    if max_bytes <= 0:
        return
    size = live_log_path.stat().st_size
    if size <= max_bytes:
        return
    marker = (
        "[gtestcov] live log truncated because it exceeded "
        f"{max_bytes} bytes; older CODRAX output was dropped.\n"
    )
    marker_bytes = marker.encode("utf-8")
    keep_budget = max(0, max_bytes - len(marker_bytes))
    keep_bytes = min(max(0, cfg.live_log_keep_tail_bytes), keep_budget)
    data = live_log_path.read_bytes()
    tail = data[-keep_bytes:] if keep_bytes else b""
    tail_text = tail.decode("utf-8", errors="replace")
    live_log_path.write_text(marker + tail_text, encoding="utf-8")
    dropped = max(0, size - keep_bytes)
    log_state["truncated"] = True
    log_state["dropped_log_bytes"] = int(log_state.get("dropped_log_bytes", 0)) + dropped


def _finalize_live_log(evidence: CodraxEvidence, live_log_path: Path | None, log_state: dict[str, Any]) -> None:
    if not live_log_path:
        return
    evidence.live_log_path = str(live_log_path)
    if live_log_path.exists():
        evidence.live_log_size_bytes = live_log_path.stat().st_size
    evidence.live_log_truncated = bool(log_state.get("truncated", False))
    evidence.dropped_log_bytes = int(log_state.get("dropped_log_bytes", 0))


def _render_live_log_header(cmd: list[str], project_root: Path, request: str) -> str:
    rendered_cmd = " ".join(shlex.quote(part) for part in cmd)
    return (
        "CODRAX live output captured by gtestcov.\n"
        f"Project root: {project_root}\n"
        f"Command: {rendered_cmd}\n"
        "Request:\n"
        f"{request}\n\n"
        "--- CODRAX output ---\n"
    )


def _maybe_open_observer(live_log_path: Path, cfg: CodraxEvidenceConfig, evidence: CodraxEvidence) -> None:
    if not cfg.open_observer:
        return
    try:
        if os.name == "nt":
            shell = shutil.which("powershell") or shutil.which("pwsh")
            if not shell:
                evidence.notes.append("CODRAX observer window requested, but PowerShell was not found.")
                return
            quoted = str(live_log_path).replace("'", "''")
            subprocess.Popen([shell, "-NoExit", "-Command", f"Get-Content -LiteralPath '{quoted}' -Wait"])
            return
        terminal = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal") or shutil.which("xterm")
        if terminal:
            subprocess.Popen([terminal, "-e", "tail", "-f", str(live_log_path)])
        else:
            evidence.notes.append("CODRAX observer window requested, but no supported terminal was found.")
    except OSError as exc:
        evidence.notes.append(f"CODRAX observer window failed to open: {exc}")


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
    help_probes = _run_help_probes(list(command_args), timeout_seconds, max_output_chars)
    if args_template:
        return {
            "selected_invocation": "args_template",
            "supported": True,
            "notes": ["Using evidence.codrax.args_template; help probing is informational only."],
            "help_probes": help_probes,
        }
    if configured_invocation != "auto":
        supported = configured_invocation in _known_invocations()
        return {
            "selected_invocation": configured_invocation,
            "supported": supported,
            "notes": [] if supported else [f"Unknown configured CODRAX invocation: {configured_invocation}"],
            "help_probes": help_probes,
        }

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
