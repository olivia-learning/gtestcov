from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .codrax import FILE_LINE_RE, execute_codrax_request, write_codrax_evidence
from .evidence_pack import attach_cache, evidence_cache_status, load_codrax_payload, store_codrax_payload
from .fs import ensure_run_dir
from .models import CodraxEvidence, ProjectProfile, ProjectUnderstanding, UnderstandingFinding
from .profile import load_profile
from .run_status import update_run_status


@dataclass(frozen=True)
class CodebaseQuestion:
    question_id: str
    prompt: str


DEFAULT_UNDERSTANDING_QUESTIONS = (
    CodebaseQuestion(
        "target_behavior",
        "Explain the target's responsibility, public behavior, and observable outcomes.",
    ),
    CodebaseQuestion(
        "dependencies",
        "Find direct dependencies, collaborator symbols, external APIs, generated code, and ownership boundaries.",
    ),
    CodebaseQuestion(
        "test_surfaces",
        "Find existing tests, fixtures, harnesses, support fakes, mocks, generated test bases, and reusable patterns.",
    ),
    CodebaseQuestion(
        "build_entrypoints",
        "Find relevant build targets, test targets, and commands or files that register the target for tests.",
    ),
    CodebaseQuestion(
        "lifecycle",
        "Find initialization, startup, shutdown, teardown, reset, threading, timing, or async requirements.",
    ),
    CodebaseQuestion(
        "boundaries_and_risks",
        "Find hardware, OS, persistence, message, queue, protocol, driver, or unsafe boundaries and risks a weak AI must avoid.",
    ),
    CodebaseQuestion(
        "framework_concepts",
        "Identify repository-specific framework concepts that affect how tests should be written.",
    ),
)


def collect_project_understanding(
    project_root: Path,
    target: str,
    profile: ProjectProfile,
    questions: tuple[CodebaseQuestion, ...] = DEFAULT_UNDERSTANDING_QUESTIONS,
    run_dir: Path | None = None,
) -> ProjectUnderstanding:
    cfg = profile.evidence.codrax
    if not cfg.enabled:
        return ProjectUnderstanding(
            enabled=False,
            status="disabled",
            target=target,
            codrax_evidence=CodraxEvidence(enabled=False, command=cfg.command, invocation=cfg.invocation, status="disabled"),
            notes=["CODRAX project understanding is disabled; project-specific assumptions are not available."],
        )

    request = build_understanding_request(target, questions)
    evidence, cache = load_codrax_payload(
        project_root,
        target,
        "project_understanding",
        request_key=request,
    )
    if evidence is None:
        evidence = ask_codrax_for_code_fact(project_root, profile, request, run_dir=run_dir)
        cache = store_codrax_payload(
            project_root,
            target,
            "project_understanding",
            evidence,
            request_key=request,
            previous_cache=cache,
        )
        evidence = attach_cache(evidence, cache)
    understanding = parse_understanding(evidence, target, [question.question_id for question in questions])
    if evidence.cache.get("hit"):
        understanding.notes.append("Loaded CODRAX project understanding from evidence_pack cache.")
    if evidence.status == "ok" and not understanding.findings:
        understanding.status = "insufficient"
        understanding.notes.append("CODRAX returned file:line evidence but no structured understanding lines could be extracted.")
    return understanding


def ask_codrax_for_code_fact(
    project_root: Path,
    profile: ProjectProfile,
    question: str,
    run_dir: Path | None = None,
    operation_name: str = "project_understanding",
) -> CodraxEvidence:
    cfg = profile.evidence.codrax
    return execute_codrax_request(
        project_root.resolve(),
        cfg,
        question,
        enabled=cfg.enabled,
        run_dir=run_dir,
        operation_name=operation_name,
    )


def generate_project_understanding(
    project_root: Path,
    target: str,
    run_id: str | None = None,
) -> tuple[ProjectUnderstanding, Path]:
    root = project_root.resolve()
    profile = load_profile(root)
    run_id, run_dir = ensure_run_dir(root, run_id)
    update_run_status(
        run_dir,
        phase="evidence.start",
        step="evidence",
        command="gtestcov evidence",
        target=target,
        current_operation="codrax_project_understanding",
    )
    try:
        understanding = collect_project_understanding(root, target, profile, run_dir=run_dir)
        path = write_project_understanding(run_dir, understanding)
        if understanding.codrax_evidence.enabled:
            write_codrax_evidence(run_dir, understanding.codrax_evidence)
        update_run_status(
            run_dir,
            phase="evidence.done",
            step="evidence",
            command="gtestcov evidence",
            target=target,
            current_operation="done",
            last_artifact=str(path),
            notes=[f"status={understanding.status}"],
            extra={
                "codrax_status": understanding.codrax_evidence.status,
                "evidence_cache": evidence_cache_status(understanding.codrax_evidence),
            },
        )
        return understanding, path
    except Exception as exc:
        update_run_status(
            run_dir,
            phase="evidence.failed",
            step="evidence",
            command="gtestcov evidence",
            target=target,
            current_operation="failed",
            notes=[str(exc)],
        )
        raise


def build_understanding_request(target: str, questions: tuple[CodebaseQuestion, ...]) -> str:
    rendered_questions = "\n".join(f"- [{question.question_id}] {question.prompt}" for question in questions)
    return f"""Read-only repository understanding request for embedded C++ GoogleTest planning.

Target: {target}

Use the repository as the source of truth. Cite real file:line evidence for every factual claim.
If a fact is not visible, write "not found" for that item. Do not generate tests and do not propose production edits.

Questions:
{rendered_questions}

Return concise bullets. Prefix each bullet with the matching [question_id] when possible.
"""


def parse_understanding(evidence: CodraxEvidence, target: str, question_ids: list[str]) -> ProjectUnderstanding:
    understanding = ProjectUnderstanding(
        enabled=evidence.enabled,
        status=evidence.status,
        target=target,
        question_ids=question_ids,
        codrax_evidence=evidence,
    )
    if evidence.status != "ok":
        understanding.notes.extend(evidence.notes)
        return understanding

    findings: list[UnderstandingFinding] = []
    for raw_line in evidence.stdout_excerpt.splitlines():
        line = _clean_bullet(raw_line)
        if not line:
            continue
        refs = [_format_file_ref(match) for match in FILE_LINE_RE.finditer(line)]
        if not refs and "not found" not in line.lower() and "insufficient" not in line.lower():
            continue
        finding = UnderstandingFinding(
            kind=_classify_finding(line),
            summary=line,
            evidence=refs,
            confidence="cited" if refs else "not_found",
            notes=["No file:line evidence was cited."] if not refs else [],
        )
        findings.append(finding)

    understanding.findings = _dedupe_findings(findings)
    return understanding


def render_project_understanding(understanding: ProjectUnderstanding) -> str:
    lines = [
        f"- Status: `{understanding.status}`",
        f"- Enabled: `{str(understanding.enabled).lower()}`",
        f"- Source: `{understanding.source}`",
        f"- Target: `{understanding.target or 'not set'}`",
    ]
    if understanding.question_ids:
        lines.append(f"- Question IDs: {understanding.question_ids}")
    if understanding.notes:
        lines.append("")
        lines.append("### Notes")
        lines.extend(_bullets(understanding.notes))
    lines.append("")
    lines.append("### Findings")
    if not understanding.findings:
        lines.append("- none")
        return "\n".join(lines).rstrip() + "\n"
    for finding in understanding.findings:
        lines.append(f"- `{finding.kind}` {finding.summary}")
        if finding.evidence:
            lines.append(f"  Evidence: {finding.evidence}")
        if finding.notes:
            lines.append(f"  Notes: {finding.notes}")
    return "\n".join(lines).rstrip() + "\n"


def write_project_understanding(run_dir: Path, understanding: ProjectUnderstanding) -> Path:
    json_path = run_dir / "project_understanding.json"
    md_path = run_dir / "project_understanding.md"
    json_path.write_text(understanding.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(
        "# CODRAX Project Understanding\n\n" + render_project_understanding(understanding),
        encoding="utf-8",
    )
    return json_path


def finding_text(understanding: ProjectUnderstanding) -> str:
    return "\n".join(finding.summary for finding in understanding.findings)


def finding_refs(understanding: ProjectUnderstanding, tokens: list[str]) -> list[str]:
    lowered = [token.lower() for token in tokens]
    refs: list[str] = []
    for finding in understanding.findings:
        haystack = f"{finding.summary} {' '.join(finding.evidence)}".lower()
        if any(token in haystack for token in lowered):
            refs.extend(finding.evidence)
    return _dedupe(refs)


def _classify_finding(line: str) -> str:
    text = line.lower()
    if _has_any(text, ["test", "fixture", "harness", "mock", "fake", "generated test", "support"]):
        return "test_surface"
    if _has_any(text, ["build", "cmake", "make", "target", "entry point", "registration"]):
        return "build_entrypoint"
    if _has_any(text, ["depend", "include", "api", "external", "collaborator", "driver", "boundary"]):
        return "dependency_boundary"
    if _has_any(text, ["init", "start", "stop", "shutdown", "teardown", "reset", "thread", "timer", "async"]):
        return "lifecycle"
    if _has_any(text, ["risk", "avoid", "unsafe", "hardware", "register", "mmio", "persistence"]):
        return "risk"
    if _has_any(text, ["framework", "platform", "generated", "concept"]):
        return "framework_concept"
    return "target_behavior"


def _clean_bullet(line: str) -> str:
    return re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()


def _format_file_ref(match: re.Match[str]) -> str:
    path = match.group("path").replace("\\", "/")
    return f"{path}:{match.group('line')}"


def _has_any(text: str, tokens: list[str]) -> bool:
    return any(token in text for token in tokens)


def _dedupe_findings(findings: list[UnderstandingFinding]) -> list[UnderstandingFinding]:
    seen: set[tuple[str, str]] = set()
    result: list[UnderstandingFinding] = []
    for finding in findings:
        key = (finding.kind, finding.summary)
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result[:80]


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _bullets(values: list[str]) -> list[str]:
    return [f"- {value}" for value in values]
