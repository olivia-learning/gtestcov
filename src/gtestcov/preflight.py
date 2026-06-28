from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .audit import audit_codrax_direct_log, audit_generated_tests, audit_write_scope
from .codrax import FILE_LINE_RE, execute_codrax_request, render_codrax_evidence, write_codrax_evidence
from .coverage_goal import read_coverage_goal
from .fs import resolve_run_dir
from .memory import refresh_memory
from .models import CodraxEvidence
from .profile import load_profile


BLOCKER_RE = re.compile(r"\b(preflight[-_ ]?blocker|blocking issue|must not compile|will not compile)\b", re.I)


def preflight_check(
    project_root: Path,
    run_id: str = "latest",
    target: str = "",
    *,
    include_codrax: bool = True,
) -> dict[str, Any]:
    root = project_root.resolve()
    active_run_id, run_dir = resolve_run_dir(root, run_id)
    profile = load_profile(root)
    coverage_goal = read_coverage_goal(run_dir)
    target = target or coverage_goal.get("target", "")

    generated = audit_generated_tests(root, run_dir)
    direct_log_violations = audit_codrax_direct_log(run_dir, profile)
    write_scope_violations = audit_write_scope(root, run_dir, profile, target)
    violations = [
        *generated["violations"],
        *direct_log_violations,
        *write_scope_violations,
    ]

    evidence = CodraxEvidence(enabled=profile.evidence.codrax.enabled, command=profile.evidence.codrax.command, status="disabled")
    codrax_violations: list[dict[str, str]] = []
    if include_codrax and profile.evidence.codrax.enabled:
        request = build_preflight_request(root, run_dir, active_run_id, target, violations)
        evidence = execute_codrax_request(root, profile.evidence.codrax, request, enabled=True)
        write_codrax_evidence(run_dir, evidence)
        codrax_violations = extract_codrax_preflight_blockers(evidence)
        violations.extend(codrax_violations)

    result: dict[str, Any] = {
        "run_id": active_run_id,
        "target": target,
        "passed": not violations,
        "blocked": bool(violations),
        "audit": {
            "violations": violations,
            "generated_test_violations": generated["violations"],
            "direct_codrax_violations": direct_log_violations,
            "write_scope_violations": write_scope_violations,
            "codrax_preflight_violations": codrax_violations,
            "checked_dirs": generated["checked_dirs"],
        },
        "codrax_evidence": evidence.model_dump(mode="json"),
    }

    (run_dir / "preflight_check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (run_dir / "preflight_check.md").write_text(render_preflight_report(result), encoding="utf-8")
    fix_task = run_dir / "preflight_fix_task.md"
    if violations:
        fix_task.write_text(render_preflight_fix_task(result), encoding="utf-8")
        result["preflight_fix_task_path"] = str(fix_task)
    elif fix_task.exists():
        fix_task.unlink()
        result["preflight_fix_task_path"] = ""
    else:
        result["preflight_fix_task_path"] = ""
    refresh_memory(root, active_run_id)
    return result


def build_preflight_request(root: Path, run_dir: Path, run_id: str, target: str, violations: list[dict[str, str]]) -> str:
    modified = run_dir / "modified_files.txt"
    modified_text = modified.read_text(encoding="utf-8", errors="ignore") if modified.exists() else "(modified_files.txt not found)"
    return f"""Read-only preflight review before compiling generated gtest code.

Run ID: {run_id}
Target file: {target or 'unknown'}
Project root: {root}

The weak AI may modify tests, test support, and test build configuration only.
Do not suggest production/business logic source edits. If production code must change, say to write source_change_request.md.

Modified files recorded by weak AI:
{modified_text}

Local preflight violations already found:
{json.dumps(violations, indent=2)}

Questions:
1. Are there obvious test-side compile blockers, missing includes, invalid dependency names, wrong harness usage, or fake/mock misuse?
2. Are any generated-test assumptions contradicted by repository evidence?
3. Can the next step safely proceed to build/test/coverage?

For any issue that should block compilation, start the bullet with "preflight_blocker:" and cite file:line evidence.
If evidence is insufficient, recommend manual_review_needed.md rather than inventing dependencies.
"""


def extract_codrax_preflight_blockers(evidence: CodraxEvidence) -> list[dict[str, str]]:
    if evidence.status != "ok" or not evidence.stdout_excerpt:
        return []
    blockers: list[dict[str, str]] = []
    for line in evidence.stdout_excerpt.splitlines():
        if not BLOCKER_RE.search(line):
            continue
        refs = FILE_LINE_RE.findall(line)
        blockers.append(
            {
                "check": "codrax_preflight_blocker",
                "path": refs[0] if refs else "",
                "detail": line.strip()[:500],
            }
        )
    return blockers


def render_preflight_report(result: dict[str, Any]) -> str:
    lines = [
        "# Preflight Check",
        "",
        f"- Run ID: `{result['run_id']}`",
        f"- Target: `{result.get('target') or 'unknown'}`",
        f"- Status: `{'passed' if result['passed'] else 'blocked'}`",
        "",
        "## Violations",
    ]
    violations = result["audit"]["violations"]
    if not violations:
        lines.append("- none")
    else:
        for violation in violations:
            detail = f" - {violation.get('detail')}" if violation.get("detail") else ""
            lines.append(f"- `{violation['check']}` in `{violation.get('path', '')}`{detail}")
    lines.extend(["", "## CODRAX Evidence", render_codrax_evidence(CodraxEvidence.model_validate(result["codrax_evidence"]), include_raw=True).rstrip()])
    return "\n".join(lines) + "\n"


def render_preflight_fix_task(result: dict[str, Any]) -> str:
    lines = [
        "# Preflight Fix Task",
        "",
        f"Run ID: `{result['run_id']}`",
        f"Target: `{result.get('target') or 'unknown'}`",
        "",
        "Fix these issues before running build/test/coverage:",
    ]
    for violation in result["audit"]["violations"]:
        detail = f" - {violation.get('detail')}" if violation.get("detail") else ""
        lines.append(f"- `{violation['check']}` in `{violation.get('path', '')}`{detail}")
    lines.extend(
        [
            "",
            "Guardrails:",
            "- Edit only tests, test support, fakes, harnesses, builders, shims, or test build configuration.",
            "- Do not edit the target file or production/business logic source.",
            "- If the fix requires production source changes, write `source_change_request.md` and stop.",
            "- If evidence is insufficient, write `manual_review_needed.md` and stop.",
        ]
    )
    return "\n".join(lines) + "\n"
