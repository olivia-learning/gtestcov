from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .codrax import execute_codrax_request, render_codrax_evidence, write_codrax_evidence
from .coverage_goal import read_coverage_goal
from .fs import resolve_run_dir
from .memory import refresh_memory
from .models import CodraxEvidence
from .profile import load_profile


HISTORY_NAME = "coverage_history.json"


def plan_next_round(
    project_root: Path,
    run_id: str = "latest",
    *,
    max_stagnant_rounds: int | None = None,
    min_improvement: float | None = None,
    record_iteration: bool = False,
) -> dict[str, Any]:
    root = project_root.resolve()
    active_run_id, run_dir = resolve_run_dir(root, run_id)
    profile = load_profile(root)
    verify = _read_json(run_dir / "verify.json")
    coverage_goal = read_coverage_goal(run_dir)
    target = verify.get("coverage", {}).get("target") or coverage_goal.get("target", "")
    threshold = float(
        verify.get("coverage", {}).get("threshold_percent")
        or coverage_goal.get("line_coverage")
        or profile.targets.default_line_coverage
    )
    current = verify.get("coverage", {}).get("line_rate_percent")
    current_value = float(current) if current is not None else None
    met_target = bool(verify.get("coverage", {}).get("meets_threshold"))
    max_rounds = max_stagnant_rounds if max_stagnant_rounds is not None else profile.coverage.max_stagnant_rounds
    min_delta = min_improvement if min_improvement is not None else profile.coverage.min_iteration_improvement
    coverage_phase = classify_coverage_phase(profile, verify.get("coverage", {}), current_value, met_target)

    existing_history = _read_json(run_dir / HISTORY_NAME)
    if record_iteration or not existing_history.get("entries"):
        history = update_coverage_history(
            run_dir,
            target=target,
            threshold=threshold,
            current=current_value,
            met_target=met_target,
            max_stagnant_rounds=max_rounds,
            min_improvement=min_delta,
            coverage_phase=coverage_phase,
        )
    else:
        history = existing_history

    if met_target:
        _remove_if_exists(run_dir / "next_round_analysis.md")
        _remove_if_exists(run_dir / "next_task.md")
        result = {
            "run_id": active_run_id,
            "status": "met_target",
            "history": history,
            "next_round_analysis_path": "",
            "next_task_path": "",
            "stagnation_report_path": "",
        }
        refresh_memory(root, active_run_id)
        return result

    if history["stagnated"]:
        evidence = _collect_next_round_evidence(root, run_dir, active_run_id, target, verify, history)
        analysis_path = write_next_round_analysis(run_dir, active_run_id, target, verify, history, evidence)
        report_path = write_stagnation_report(run_dir, active_run_id, target, verify, history, evidence)
        _remove_if_exists(run_dir / "next_task.md")
        result = {
            "run_id": active_run_id,
            "status": "stagnated",
            "history": history,
            "next_round_analysis_path": str(analysis_path),
            "next_task_path": "",
            "stagnation_report_path": str(report_path),
            "codrax_evidence": evidence.model_dump(mode="json"),
        }
        refresh_memory(root, active_run_id)
        return result

    evidence = _collect_next_round_evidence(root, run_dir, active_run_id, target, verify, history)
    analysis_path = write_next_round_analysis(run_dir, active_run_id, target, verify, history, evidence)
    task_path = write_next_task(run_dir, active_run_id, target, verify, history, evidence)
    _remove_if_exists(run_dir / "stagnation_report.md")
    result = {
        "run_id": active_run_id,
        "status": "next_task_ready",
        "history": history,
        "next_round_analysis_path": str(analysis_path),
        "next_task_path": str(task_path),
        "stagnation_report_path": "",
        "codrax_evidence": evidence.model_dump(mode="json"),
    }
    refresh_memory(root, active_run_id)
    return result


def update_coverage_history(
    run_dir: Path,
    *,
    target: str,
    threshold: float,
    current: float | None,
    met_target: bool,
    max_stagnant_rounds: int,
    min_improvement: float,
    coverage_phase: str,
) -> dict[str, Any]:
    path = run_dir / HISTORY_NAME
    existing = _read_json(path)
    entries = list(existing.get("entries", []))
    previous_current = entries[-1].get("current_coverage") if entries else None
    if previous_current is None or current is None:
        improvement = None if not entries else 0.0
    else:
        improvement = float(current) - float(previous_current)
    is_stagnant_round = bool(entries) and not met_target and (improvement is None or improvement < min_improvement)
    previous_stagnant = int(existing.get("consecutive_stagnant_rounds", 0))
    consecutive = previous_stagnant + 1 if is_stagnant_round else 0
    entry = {
        "iteration_index": len(entries) + 1,
        "target": target,
        "threshold_percent": threshold,
        "current_coverage": current,
        "improvement": improvement,
        "coverage_phase": coverage_phase,
        "met_target": met_target,
        "is_stagnant_round": is_stagnant_round,
    }
    entries.append(entry)
    history = {
        "target": target,
        "threshold_percent": threshold,
        "min_iteration_improvement": min_improvement,
        "max_stagnant_rounds": max_stagnant_rounds,
        "coverage_phase": coverage_phase,
        "consecutive_stagnant_rounds": consecutive,
        "stagnated": consecutive >= max_stagnant_rounds,
        "met_target": met_target,
        "entries": entries,
    }
    path.write_text(json.dumps(history, indent=2), encoding="utf-8")
    return history


def classify_coverage_phase(profile, coverage: dict[str, Any], current: float | None, met_target: bool) -> str:
    if met_target:
        return "met_target"
    if current is None or coverage.get("target_found") is False:
        return "coverage_mapping_blocked"
    if current < profile.coverage.bootstrap_threshold:
        return "bootstrap"
    if current < profile.coverage.characterization_threshold:
        return "characterization"
    if current < profile.coverage.branch_expansion_threshold:
        return "branch_expansion"
    return "precision_closure"


def phase_guidance(phase: str) -> str:
    guidance = {
        "coverage_mapping_blocked": (
            "- Treat this as a coverage/build mapping problem, not a missing-test problem.\n"
            "- Check whether the target file is linked into the focused test binary, excluded from coverage, or filtered out.\n"
            "- Prefer test build configuration or coverage command fixes supported by file:line evidence."
        ),
        "bootstrap": (
            "- Establish the smallest credible test path into the target file.\n"
            "- Prefer constructor/init/shutdown and one simple public entry point over deep branch chasing.\n"
            "- Add only the minimum fakes or harness code needed to make the target observable."
        ),
        "characterization": (
            "- Expand coverage through observable current behavior.\n"
            "- Reuse existing fixtures, fakes, and harnesses; avoid inventing dependency contracts.\n"
            "- Cover main state flows, return codes, dependency interactions, and error handling visible from tests."
        ),
        "branch_expansion": (
            "- Add targeted cases for meaningful branches and boundaries.\n"
            "- Prioritize edge values, failure paths, lifecycle transitions, and message/protocol boundaries with evidence."
        ),
        "precision_closure": (
            "- Close specific remaining coverage gaps with file:line evidence.\n"
            "- Do not add broad tests unless they hit identified uncovered lines or branches.\n"
            "- If a remaining path requires a production seam, write source_change_request.md."
        ),
    }
    return guidance.get(phase, "- Use CODRAX evidence to choose the next safe test-only step.")


def phase_task_bullets(phase: str) -> str:
    tasks = {
        "coverage_mapping_blocked": [
            "Use CODRAX evidence to fix the test build or coverage mapping before adding more behavior tests.",
            "Confirm the target file is compiled, linked, included in coverage, and matched by the target coverage filter.",
            "Modify only test build configuration or coverage command configuration when needed.",
        ],
        "bootstrap": [
            "Add a minimal smoke/initialization test that actually executes the target file.",
            "Create or reuse only the smallest required fixture, fake, harness, builder, or shim.",
            "Prefer one observable public entry point before attempting deeper branch coverage.",
        ],
        "characterization": [
            "Add tests that characterize existing observable behavior supported by CODRAX file:line evidence.",
            "Cover main state flows, return codes, dependency calls, and error handling without inventing contracts.",
        ],
        "branch_expansion": [
            "Add focused tests for uncovered branches, boundary values, failure paths, and lifecycle/message transitions.",
            "Reuse existing fixtures and fakes before creating new support code.",
        ],
        "precision_closure": [
            "Add narrow tests for the specific remaining uncovered lines or branches identified by evidence.",
            "Write source_change_request.md instead of editing production code if a remaining path has no test seam.",
        ],
    }
    selected = tasks.get(phase, ["Add only test-side changes supported by CODRAX file:line evidence."])
    return "\n".join(f"- {item}" for item in selected)


def build_next_round_request(run_id: str, target: str, verify: dict[str, Any], history: dict[str, Any]) -> str:
    phase = history.get("coverage_phase", "unknown")
    return f"""Read-only next-round coverage planning for embedded C++ gtest.

Run ID: {run_id}
Target file: {target or 'unknown'}
Coverage phase: {phase}

The target file line coverage has not reached the goal. Use repository evidence to suggest the next safe test-only step.
The weak AI may modify tests, fixtures, fakes, harnesses, and test build configuration only.
Do not recommend production/business logic edits unless the output should be source_change_request.md.

Coverage history:
{json.dumps(history, indent=2)}

Verify summary:
{json.dumps(_trim_verify(verify), indent=2)}

Context files in this run may include decision_report.md, test_obligations.md, modified_files.txt, and review_checklist.md.

Questions:
1. Which target behavior, branch, state, error path, or lifecycle path likely remains uncovered?
2. Which existing test, fixture, fake, harness, or test build config should be reused or extended?
3. What exact next test scenarios should the weak AI add in the next iteration?
4. Is a source seam required? If yes, say to write source_change_request.md.

Phase-specific guidance:
{phase_guidance(phase)}

Answer with concise bullets and cite file:line evidence for factual project claims.
"""


def write_next_round_analysis(
    run_dir: Path,
    run_id: str,
    target: str,
    verify: dict[str, Any],
    history: dict[str, Any],
    evidence: CodraxEvidence,
) -> Path:
    path = run_dir / "next_round_analysis.md"
    path.write_text(
        f"""# Next Round Coverage Analysis

- Run ID: `{run_id}`
- Target: `{target or 'unknown'}`
- Current coverage: `{verify.get('coverage', {}).get('line_rate_percent')}`
- Goal: `{verify.get('coverage', {}).get('threshold_percent')}`
- Coverage phase: `{history.get('coverage_phase')}`
- Consecutive stagnant rounds: `{history.get('consecutive_stagnant_rounds')}`
- Stagnated: `{history.get('stagnated')}`

## Phase Guidance
{phase_guidance(history.get('coverage_phase', 'unknown'))}

## CODRAX Evidence
{render_codrax_evidence(evidence, include_raw=True).rstrip()}

## Guardrails
- Add or adjust tests, fixtures, fakes, harnesses, or test build configuration only.
- Do not edit production/business logic source directly.
- If a production seam is required, write `source_change_request.md`.
""",
        encoding="utf-8",
    )
    return path


def write_next_task(
    run_dir: Path,
    run_id: str,
    target: str,
    verify: dict[str, Any],
    history: dict[str, Any],
    evidence: CodraxEvidence,
) -> Path:
    path = run_dir / "next_task.md"
    path.write_text(
        f"""# Next Round Task

Run ID: `{run_id}`
Target: `{target or 'unknown'}`

## Coverage Gap
- Current target line coverage: `{verify.get('coverage', {}).get('line_rate_percent')}`
- Required target line coverage: `{verify.get('coverage', {}).get('threshold_percent')}`
- Coverage phase: `{history.get('coverage_phase')}`
- Last iteration improvement: `{history.get('entries', [{}])[-1].get('improvement')}`

## Required Next Step
- Read `next_round_analysis.md`.
{phase_task_bullets(history.get('coverage_phase', 'unknown'))}
- Record modified paths in `modified_files.txt`.
- Before each added or modified `TEST`, `TEST_F`, or `TEST_P`, add a comment block with `Test Case`, `Value`, `Steps`, `Inputs`, and `Expected Outputs`.
- In the `Value` field, say what real behavior the case exercises and why it matters; only label it `coverage-only, low business value` when that is genuinely the main purpose.
- Run `gtestcov verify --run-id {run_id}` again.

## Stop Conditions
- If evidence is insufficient, write `manual_review_needed.md`.
- If a production source seam is required, write `source_change_request.md`.
- Do not edit `{target}` or production/business logic source directly.

## CODRAX Evidence Summary
{render_codrax_evidence(evidence).rstrip()}
""",
        encoding="utf-8",
    )
    return path


def write_stagnation_report(
    run_dir: Path,
    run_id: str,
    target: str,
    verify: dict[str, Any],
    history: dict[str, Any],
    evidence: CodraxEvidence,
) -> Path:
    path = run_dir / "stagnation_report.md"
    rows = "\n".join(
        f"- Iteration {entry['iteration_index']}: coverage={entry['current_coverage']}, improvement={entry['improvement']}, stagnant={entry['is_stagnant_round']}"
        for entry in history.get("entries", [])
    )
    path.write_text(
        f"""# Coverage Stagnation Report

- Run ID: `{run_id}`
- Target: `{target or 'unknown'}`
- Current coverage: `{verify.get('coverage', {}).get('line_rate_percent')}`
- Goal: `{verify.get('coverage', {}).get('threshold_percent')}`
- Coverage phase: `{history.get('coverage_phase')}`
- Minimum required improvement: `{history.get('min_iteration_improvement')}` percentage points
- Consecutive stagnant rounds: `{history.get('consecutive_stagnant_rounds')}`

## Iteration History
{rows or '- none'}

## CODRAX Evidence
{render_codrax_evidence(evidence, include_raw=True).rstrip()}

## Recommendation
- Stop automatic iteration and ask the user to review this report.
- If CODRAX indicates a production seam is required, use `source_change_request.md` instead of editing production code.
""",
        encoding="utf-8",
    )
    return path


def _collect_next_round_evidence(
    project_root: Path,
    run_dir: Path,
    run_id: str,
    target: str,
    verify: dict[str, Any],
    history: dict[str, Any],
) -> CodraxEvidence:
    profile = load_profile(project_root)
    request = build_next_round_request(run_id, target, verify, history)
    evidence = execute_codrax_request(project_root, profile.evidence.codrax, request, enabled=profile.evidence.codrax.enabled)
    write_codrax_evidence(run_dir, evidence)
    return evidence


def _trim_verify(verify: dict[str, Any]) -> dict[str, Any]:
    return {
        "passed": verify.get("passed"),
        "coverage": verify.get("coverage", {}),
        "audit": verify.get("audit", {}),
        "commands": {
            label: {
                "returncode": command.get("returncode"),
                "diagnostics": command.get("diagnostics", []),
                "stdout_tail": (command.get("stdout") or "")[-1200:],
                "stderr_tail": (command.get("stderr") or "")[-1200:],
            }
            for label, command in verify.get("commands", {}).items()
        },
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _remove_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()
