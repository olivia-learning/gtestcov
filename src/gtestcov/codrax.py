from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
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


def collect_codrax_evidence(project_root: Path, target: str, profile: ProjectProfile) -> CodraxEvidence:
    cfg = profile.evidence.codrax
    if not cfg.enabled:
        return CodraxEvidence(enabled=False, command=cfg.command, invocation=cfg.invocation, status="disabled")
    return execute_codrax_request(project_root.resolve(), cfg, build_codrax_request(target), enabled=True)


def execute_codrax_request(
    project_root: Path,
    cfg: CodraxEvidenceConfig,
    request: str,
    *,
    enabled: bool = True,
) -> CodraxEvidence:
    return _execute_codrax_request(project_root.resolve(), cfg, request, enabled=enabled)


def generate_codrax_evidence(project_root: Path, target: str, run_id: str | None = None) -> tuple[CodraxEvidence, Path]:
    root = project_root.resolve()
    profile = load_profile(root)
    run_id, run_dir = ensure_run_dir(root, run_id)
    evidence = collect_codrax_evidence(root, target, profile)
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
    refs: list[str] = []
    symbols: list[str] = []
    dependencies: list[str] = []
    harnesses: list[str] = []
    risks: list[str] = []
    notes: list[str] = []

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        refs.extend(_format_file_ref(match) for match in FILE_LINE_RE.finditer(line))
        symbols.extend(_normalize_symbol(match.group(0)) for match in SYMBOL_RE.finditer(line))
        if DEPENDENCY_HINT_RE.search(line):
            dependencies.append(_trim_line(line))
        if HARNESS_HINT_RE.search(line):
            harnesses.append(_trim_line(line))
        if RISK_HINT_RE.search(line):
            risks.append(_trim_line(line))
        if "not found" in line.lower() or "insufficient" in line.lower():
            notes.append(_trim_line(line))

    evidence.file_line_refs = _dedupe(refs)[:80]
    evidence.related_files = _dedupe([ref.rsplit(":", 1)[0] for ref in evidence.file_line_refs])[:80]
    evidence.symbols = _dedupe(symbols)[:80]
    evidence.dependencies = _dedupe(dependencies)[:40]
    evidence.harnesses = _dedupe(harnesses)[:40]
    evidence.risks = _dedupe(risks)[:40]
    evidence.notes = _dedupe([*evidence.notes, *notes])[:40]
    return evidence


def _execute_codrax_request(
    project_root: Path,
    cfg: CodraxEvidenceConfig,
    request: str,
    *,
    enabled: bool,
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
    try:
        completed = subprocess.run(
            cmd,
            cwd=project_root,
            text=True,
            capture_output=True,
            timeout=cfg.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        evidence.available = True
        evidence.status = "timeout"
        evidence.stdout_excerpt = _excerpt(exc.stdout or "", cfg.max_output_chars)
        evidence.stderr_excerpt = _excerpt(exc.stderr or "", cfg.max_output_chars)
        evidence.notes.append(f"CODRAX timed out after {cfg.timeout_seconds} seconds.")
        return evidence
    except OSError as exc:
        evidence.status = "error"
        evidence.notes.append(f"CODRAX failed to start: {exc}")
        return evidence

    evidence.returncode = completed.returncode
    evidence.stdout_excerpt = _excerpt(completed.stdout, cfg.max_output_chars)
    evidence.stderr_excerpt = _excerpt(completed.stderr, cfg.max_output_chars)
    parse_codrax_output(completed.stdout, evidence)

    if completed.returncode != 0:
        evidence.status = "error"
        evidence.notes.append("CODRAX returned a non-zero exit code.")
    elif cfg.require_file_line and not evidence.file_line_refs:
        evidence.status = "insufficient"
        evidence.notes.append("CODRAX output did not include required file:line evidence.")
    else:
        evidence.status = "ok"
    return evidence


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
