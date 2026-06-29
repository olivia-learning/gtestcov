from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .codrax import execute_codrax_request, render_codrax_evidence, write_codrax_evidence
from .fs import resolve_run_dir
from .memory import refresh_memory
from .profile import load_profile
from .run_status import update_run_status


def diagnose_failure(project_root: Path, run_id: str = "latest", target: str = "") -> dict[str, Any]:
    root = project_root.resolve()
    active_run_id, run_dir = resolve_run_dir(root, run_id)
    profile = load_profile(root)
    verify_path = run_dir / "verify.json"
    verify = json.loads(verify_path.read_text(encoding="utf-8")) if verify_path.exists() else {}
    request = build_failure_diagnosis_request(active_run_id, target, verify)
    update_run_status(
        run_dir,
        phase="diagnose_failure.start",
        command="diagnose-failure",
        target=target,
        current_operation="codrax_failure_diagnosis",
    )
    evidence = execute_codrax_request(
        root,
        profile.evidence.codrax,
        request,
        enabled=profile.evidence.codrax.enabled,
        run_dir=run_dir,
        operation_name="diagnose_failure",
    )
    update_run_status(
        run_dir,
        phase="diagnose_failure.codrax_done",
        command="diagnose-failure",
        target=target,
        current_operation="write_diagnosis_report",
        notes=[f"CODRAX status: {evidence.status}"],
        extra={"codrax_status": evidence.status},
    )
    write_codrax_evidence(run_dir, evidence)
    report_path = run_dir / "failure_diagnosis.md"
    report_path.write_text(render_failure_diagnosis(active_run_id, target, evidence), encoding="utf-8")
    json_path = run_dir / "failure_diagnosis.json"
    data = {
        "run_id": active_run_id,
        "target": target,
        "status": evidence.status,
        "diagnosis_path": str(report_path),
        "codrax_evidence": evidence.model_dump(mode="json"),
    }
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    update_run_status(
        run_dir,
        phase="diagnose_failure.memory_refresh",
        command="diagnose-failure",
        target=target,
        current_operation="memory_refresh",
        last_artifact=str(report_path),
    )
    refresh_memory(root, active_run_id)
    update_run_status(
        run_dir,
        phase="diagnose_failure.done",
        command="diagnose-failure",
        target=target,
        current_operation="done",
        last_artifact=str(report_path),
    )
    return {**data, "diagnosis_json": str(json_path)}


def build_failure_diagnosis_request(run_id: str, target: str, verify: dict[str, Any]) -> str:
    return f"""Read-only failure diagnosis for a gtest coverage iteration.

Run ID: {run_id}
Target file: {target or 'unknown'}

The weak AI may fix tests and test build configuration only. Do not recommend business logic edits unless the output should be source_change_request.md.

Verify summary:
{json.dumps(_trim_verify(verify), indent=2)}

Questions:
1. Which build/test/coverage failure is most likely blocking progress?
2. Which test file, fixture, test build config, or command should be changed?
3. Is any production/business source change required? If yes, say to write source_change_request.md.

Answer with concise bullets and cite file:line evidence for factual project claims.
"""


def render_failure_diagnosis(run_id: str, target: str, evidence) -> str:
    return f"""# CODRAX Failure Diagnosis

- Run ID: `{run_id}`
- Target: `{target or 'unknown'}`

## Diagnosis Evidence
{render_codrax_evidence(evidence, include_raw=True).rstrip()}

## Guardrails
- Fix tests or test build configuration first.
- Do not edit production/business logic source directly.
- If production code needs a seam or behavior change, write `source_change_request.md`.
"""


def _trim_verify(verify: dict[str, Any]) -> dict[str, Any]:
    if not verify:
        return {"verify_json": "not found"}
    result: dict[str, Any] = {
        "passed": verify.get("passed"),
        "coverage": verify.get("coverage", {}),
        "audit": verify.get("audit", {}),
        "commands": {},
    }
    for label, command in verify.get("commands", {}).items():
        result["commands"][label] = {
            "returncode": command.get("returncode"),
            "diagnostics": command.get("diagnostics", []),
            "stdout_tail": (command.get("stdout") or "")[-2000:],
            "stderr_tail": (command.get("stderr") or "")[-2000:],
        }
    return result
