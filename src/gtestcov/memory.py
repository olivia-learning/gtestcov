from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .audit import allowed_write_paths, protected_gtestcov_state_paths
from .coverage_goal import read_coverage_goal
from .fs import resolve_run_dir
from .models import CodraxEvidence, ProjectProfile, relpath
from .profile import PROFILE_NAME, load_profile


RUN_HANDOFF_JSON = "handoff.json"
RUN_HANDOFF_MD = "handoff.md"
RESUME_PROMPT_MD = "resume_prompt.md"
PROJECT_MEMORY_JSON = "project_memory.json"
PROJECT_MEMORY_MD = "project_memory.md"
SCHEMA_VERSION = 1


def refresh_memory(project_root: Path, run_id: str = "latest") -> dict[str, Any]:
    root = project_root.resolve()
    active_run_id, run_dir = resolve_run_dir(root, run_id)
    profile = load_profile(root)
    run_memory = build_run_memory(root, run_dir, active_run_id, profile)

    handoff_json = run_dir / RUN_HANDOFF_JSON
    handoff_md = run_dir / RUN_HANDOFF_MD
    resume_md = run_dir / RESUME_PROMPT_MD
    handoff_json.write_text(json.dumps(run_memory, indent=2), encoding="utf-8")
    handoff_md.write_text(render_run_handoff(run_memory), encoding="utf-8")
    resume_md.write_text(render_resume_prompt(run_memory), encoding="utf-8")

    project_memory = build_project_memory(root, active_run_id, run_memory, profile)
    memory_dir = root / ".gtestcov" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    project_json = memory_dir / PROJECT_MEMORY_JSON
    project_md = memory_dir / PROJECT_MEMORY_MD
    project_json.write_text(json.dumps(project_memory, indent=2), encoding="utf-8")
    project_md.write_text(render_project_memory(project_memory), encoding="utf-8")

    return {
        "run_id": active_run_id,
        "status": run_memory["status"],
        "handoff_json": str(handoff_json),
        "handoff_md": str(handoff_md),
        "resume_prompt": str(resume_md),
        "project_memory_json": str(project_json),
        "project_memory_md": str(project_md),
    }


def show_memory(project_root: Path, run_id: str = "latest", output_format: str = "md") -> dict[str, Any]:
    root = project_root.resolve()
    active_run_id, run_dir = resolve_run_dir(root, run_id)
    selected_format = output_format.lower()
    if selected_format not in {"md", "json"}:
        raise ValueError("memory format must be 'md' or 'json'")

    path = run_dir / (RUN_HANDOFF_JSON if selected_format == "json" else RUN_HANDOFF_MD)
    if not path.exists():
        refresh_memory(root, active_run_id)
    content = path.read_text(encoding="utf-8")
    return {
        "run_id": active_run_id,
        "format": selected_format,
        "path": str(path),
        "content": json.loads(content) if selected_format == "json" else content,
    }


def build_run_memory(root: Path, run_dir: Path, run_id: str, profile: ProjectProfile) -> dict[str, Any]:
    coverage_goal = read_coverage_goal(run_dir)
    verify = _read_json(run_dir / "verify.json")
    preflight = _read_json(run_dir / "preflight_check.json")
    history = _read_json(run_dir / "coverage_history.json")
    codrax = _read_json(run_dir / "codrax_evidence.json")
    target = _target_from_artifacts(coverage_goal, verify, run_dir)
    status = _derive_status(run_dir, verify, preflight)
    next_action = _next_action(status, run_id)
    artifacts = _collect_artifacts(root, run_dir)
    read_first = _read_first_paths(run_id, artifacts)
    protected = protected_gtestcov_state_paths(run_id)
    open_questions = _open_questions(root, target, status, artifacts)

    return {
        "schema_version": SCHEMA_VERSION,
        "project_root": str(root),
        "scope": {
            "project_root": str(root),
            "project_root_policy": "--project-root is the gtestcov workspace boundary; .git is not required.",
            "external_path_policy": "Paths outside --project-root are references only unless the user confirms they are safe.",
        },
        "run_id": run_id,
        "target": target,
        "coverage_goal": coverage_goal,
        "status": status,
        "next_action": next_action,
        "context_reload": {
            "read_first": read_first,
            "resume_prompt": f".gtestcov/runs/{run_id}/{RESUME_PROMPT_MD}",
            "handoff": f".gtestcov/runs/{run_id}/{RUN_HANDOFF_MD}",
        },
        "write_boundaries": {
            "allowed_write_paths": allowed_write_paths(profile),
            "forbidden_write_paths": [item for item in [target, "production/business logic source"] if item],
            "protected_gtestcov_state": protected,
        },
        "profile_summary": _profile_summary(profile),
        "artifacts": artifacts,
        "preflight": _trim_preflight(preflight),
        "verify": _trim_verify(verify),
        "coverage_history": history,
        "codrax": _trim_codrax(codrax),
        "project_detail_policy": _project_detail_policy(),
        "open_questions": open_questions,
    }


def build_project_memory(
    root: Path,
    run_id: str,
    run_memory: dict[str, Any],
    profile: ProjectProfile,
) -> dict[str, Any]:
    memory_dir = root / ".gtestcov" / "memory"
    existing = _read_json(memory_dir / PROJECT_MEMORY_JSON)
    facts = list(existing.get("verified_facts", []))
    facts.extend(_profile_facts(profile))
    facts.extend(_profile_evidence_facts(root, run_id))
    facts.extend(_codrax_project_facts(root, run_id))
    deduped = _dedupe_facts(facts)
    open_questions = _dedupe_strings(
        [
            *existing.get("open_questions", []),
            *run_memory.get("open_questions", []),
        ]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "project_root": str(root),
        "scope": {
            "project_root": str(root),
            "requires_git": False,
            "policy": "Project memory is scoped only to --project-root, which may be a .repo workspace, monorepo subtree, source archive, or other user-selected workspace.",
        },
        "latest_run_id": run_id,
        "project_detail_policy": _project_detail_policy(),
        "verified_facts": deduped,
        "open_questions": open_questions,
    }


def render_run_handoff(memory: dict[str, Any]) -> str:
    lines = [
        "# gtestcov Run Handoff",
        "",
        f"- Run ID: `{memory['run_id']}`",
        f"- Project root: `{memory['project_root']}`",
        f"- Target: `{memory.get('target') or 'unknown'}`",
        f"- Status: `{memory['status']}`",
        f"- Next action: {memory['next_action']}",
        "",
        "## Context Reload Required",
        "When the weak AI starts, resumes, or loses compressed context, read these first:",
        _bullets(memory["context_reload"]["read_first"] or ["none"]),
        "",
        "## Goal",
    ]
    coverage_goal = memory.get("coverage_goal") or {}
    if coverage_goal:
        lines.extend(
            [
                f"- Target file: `{coverage_goal.get('target', '')}`",
                f"- Target line coverage: `{coverage_goal.get('line_coverage', '')}%`",
                f"- Metric: `{coverage_goal.get('metric', '')}`",
            ]
        )
    else:
        lines.append("- No coverage goal recorded yet.")
    lines.extend(
        [
            "",
            "## Write Boundaries",
            "Allowed write paths:",
            _bullets(memory["write_boundaries"]["allowed_write_paths"] or ["none"]),
            "",
            "Never edit directly:",
            _bullets(memory["write_boundaries"]["forbidden_write_paths"] or ["production/business logic source"]),
            "",
            "Tool-generated state is read-only for the weak AI:",
            _bullets(memory["write_boundaries"]["protected_gtestcov_state"]),
            "",
            "## Verification State",
            _render_verify_state(memory),
            "",
            "## Artifacts",
            _bullets([f"{key}: `{value}`" for key, value in memory.get("artifacts", {}).items()] or ["none"]),
            "",
            "## Project Detail Policy",
            memory["project_detail_policy"],
            "",
            "## Open Questions / Blockers",
            _bullets(memory.get("open_questions") or ["none"]),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_resume_prompt(memory: dict[str, Any]) -> str:
    return f"""# gtestcov Resume Prompt

Read `.gtestcov/runs/{memory['run_id']}/{RUN_HANDOFF_MD}` before doing any work.

- Run ID: `{memory['run_id']}`
- Target: `{memory.get('target') or 'unknown'}`
- Status: `{memory['status']}`
- Next action: {memory['next_action']}

Do not infer project-specific facts from naming habits, open-source project impressions, or memory.
Use only user input, `project_profile.yaml`, CODRAX `file:line` evidence, or gtestcov artifacts.
Do not edit tool-generated memory/state files.
"""


def render_project_memory(memory: dict[str, Any]) -> str:
    lines = [
        "# gtestcov Project Memory",
        "",
        f"- Project root: `{memory['project_root']}`",
        f"- Latest run ID: `{memory.get('latest_run_id', '')}`",
        f"- Requires `.git`: `{str(memory['scope']['requires_git']).lower()}`",
        "",
        "## Scope",
        memory["scope"]["policy"],
        "",
        "## Project Detail Policy",
        memory["project_detail_policy"],
        "",
        "## Verified Facts",
    ]
    facts = memory.get("verified_facts") or []
    if not facts:
        lines.append("- none")
    for fact in facts:
        sources = ", ".join(f"`{source}`" for source in fact.get("sources", [])) or "`unknown`"
        evidence = ", ".join(f"`{item}`" for item in fact.get("evidence", []))
        suffix = f"; evidence: {evidence}" if evidence else ""
        lines.append(f"- `{fact['category']}.{fact['key']}` = `{fact['value']}`; sources: {sources}{suffix}")
    lines.extend(["", "## Open Questions", _bullets(memory.get("open_questions") or ["none"])])
    return "\n".join(lines).rstrip() + "\n"


def _profile_summary(profile: ProjectProfile) -> dict[str, Any]:
    return {
        "project_name": profile.project_name,
        "language": profile.language,
        "test_framework": profile.test_framework,
        "build": {
            "system": profile.build.system,
            "build_file": profile.build.build_file,
            "candidate_build_files": profile.build.candidate_build_files,
            "build_command": profile.build.build_command,
            "incremental_build_command": profile.build.incremental_build_command,
            "test_command": profile.build.test_command,
            "filtered_test_command": profile.build.filtered_test_command,
            "coverage_command": profile.build.coverage_command,
            "target_coverage_command": profile.build.target_coverage_command,
            "coverage_xml": profile.build.coverage_xml,
        },
        "test_support": profile.test_support.model_dump(mode="json"),
    }


def _profile_facts(profile: ProjectProfile) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    values = {
        "build.system": profile.build.system if profile.build.system != "unknown" else "",
        "build.build_file": profile.build.build_file,
        "build.build_command": profile.build.build_command,
        "build.incremental_build_command": profile.build.incremental_build_command,
        "build.test_command": profile.build.test_command,
        "build.filtered_test_command": profile.build.filtered_test_command,
        "build.coverage_command": profile.build.coverage_command,
        "build.target_coverage_command": profile.build.target_coverage_command,
        "build.coverage_xml": profile.build.coverage_xml if profile.build.coverage_xml != "coverage.xml" else "",
        "dependency.manifest": profile.dependency.manifest,
        "dependency.host_shim_dir": profile.dependency.host_shim_dir,
        "test_support.fake_dir": profile.test_support.fake_dir,
        "test_support.harness_dir": profile.test_support.harness_dir,
        "test_support.guard_dir": profile.test_support.guard_dir,
        "test_support.builder_dir": profile.test_support.builder_dir,
        "test_support.dependency_shim_dir": profile.test_support.dependency_shim_dir,
    }
    for key, value in values.items():
        if value:
            facts.append(_fact(key, value, [PROFILE_NAME]))
    list_values = {
        "build.candidate_build_files": profile.build.candidate_build_files,
        "dependency.manifest_candidates": profile.dependency.manifest_candidates,
        "dependency.dependency_root": profile.dependency.dependency_root,
        "dependency.exclude_from_coverage": profile.dependency.exclude_from_coverage,
        "test_support.test_dirs": profile.test_support.test_dirs,
        "test_support.test_build_config_paths": profile.test_support.test_build_config_paths,
    }
    for key, values_list in list_values.items():
        for value in values_list:
            if value:
                facts.append(_fact(key, value, [PROFILE_NAME]))
    return facts


def _profile_evidence_facts(root: Path, run_id: str) -> list[dict[str, Any]]:
    path = root / ".gtestcov" / "runs" / run_id / "profile_evidence.md"
    if not path.exists():
        return []
    facts: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = re.search(r"- `([^`]+)` = `([^`]+)`; evidence=(.+)$", line)
        if not match:
            continue
        key, value, evidence_text = match.groups()
        evidence = re.findall(r"'([^']+)'", evidence_text)
        if evidence:
            facts.append(_fact(key, value, [relpath(path, root)], evidence))
    return facts


def _codrax_project_facts(root: Path, run_id: str) -> list[dict[str, Any]]:
    path = root / ".gtestcov" / "runs" / run_id / "codrax_evidence.json"
    data = _read_json(path)
    if not data:
        return []
    evidence = data.get("file_line_refs", [])
    sources = [relpath(path, root)]
    facts: list[dict[str, Any]] = []
    for harness in data.get("harnesses", []):
        facts.append(_fact("codrax.harness_or_test_support", harness, sources, evidence))
    for risk in data.get("risks", []):
        facts.append(_fact("codrax.risk", risk, sources, evidence))
    return facts


def _fact(key: str, value: Any, sources: list[str], evidence: list[str] | None = None) -> dict[str, Any]:
    category, _, item_key = key.partition(".")
    return {
        "category": category,
        "key": item_key or key,
        "value": value,
        "sources": [source for source in sources if source],
        "evidence": evidence or [],
    }


def _dedupe_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for fact in facts:
        if not fact.get("sources"):
            continue
        key = (str(fact.get("category")), str(fact.get("key")), str(fact.get("value")))
        if key in seen:
            continue
        result.append(fact)
        seen.add(key)
    return result


def _derive_status(run_dir: Path, verify: dict[str, Any], preflight: dict[str, Any]) -> str:
    if (run_dir / "stagnation_report.md").exists():
        return "stagnated"
    if (run_dir / "manual_review_needed.md").exists():
        return "manual_review_needed"
    if (run_dir / "source_change_request.md").exists():
        return "source_change_request_needed"
    if preflight and not preflight.get("passed", False):
        return "blocked_by_preflight"
    if verify:
        if verify.get("passed"):
            return "verified_passed"
        coverage = verify.get("coverage", {})
        if coverage.get("line_rate_percent") is not None and not coverage.get("meets_threshold"):
            return "coverage_below_target"
        return "verify_failed"
    if (run_dir / "task.md").exists():
        return "task_ready"
    if (run_dir / "profile_evidence.md").exists():
        return "profile_synced"
    return "initialized"


def _next_action(status: str, run_id: str) -> str:
    actions = {
        "stagnated": "Stop automatic editing and ask the user to review stagnation_report.md.",
        "manual_review_needed": "Stop and ask the user to review manual_review_needed.md.",
        "source_change_request_needed": "Stop test generation and ask the user to review source_change_request.md.",
        "blocked_by_preflight": "Read preflight_fix_task.md, fix only test-side issues, then rerun gtestcov check.",
        "verified_passed": "No further test generation is required for this run.",
        "coverage_below_target": "Read next_task.md and continue one controlled test-side iteration.",
        "verify_failed": "Run diagnose-failure or inspect verify.json, then fix only test-side or test-build configuration issues.",
        "task_ready": "Read task.md and opencode_permission_warmup.md before editing tests.",
        "profile_synced": "Run or read the task package before asking the weak AI to edit tests.",
    }
    return actions.get(status, f"Continue from .gtestcov/runs/{run_id} artifacts.")


def _target_from_artifacts(coverage_goal: dict[str, Any], verify: dict[str, Any], run_dir: Path) -> str:
    if coverage_goal.get("target"):
        return str(coverage_goal["target"])
    coverage = verify.get("coverage", {}) if verify else {}
    if coverage.get("target"):
        return str(coverage["target"])
    task = run_dir / "task.md"
    if task.exists():
        match = re.search(r"^Target:\s*`([^`]+)`", task.read_text(encoding="utf-8", errors="ignore"), re.MULTILINE)
        if match:
            return match.group(1)
    return ""


def _collect_artifacts(root: Path, run_dir: Path) -> dict[str, str]:
    names = [
        "coverage_goal.json",
        "profile_evidence.md",
        "codrax_evidence.md",
        "project_understanding.md",
        "decision_report.md",
        "test_obligations.md",
        "opencode_permission_warmup.md",
        "task.md",
        "modified_files.txt",
        "codrax_direct_log.md",
        "preflight_check.md",
        "preflight_fix_task.md",
        "verify.json",
        "review_checklist.md",
        "coverage_history.json",
        "next_round_analysis.md",
        "next_task.md",
        "stagnation_report.md",
        "failure_diagnosis.md",
        "manual_review_needed.md",
        "source_change_request.md",
    ]
    artifacts: dict[str, str] = {}
    for name in names:
        path = run_dir / name
        if path.exists():
            artifacts[name] = relpath(path, root)
    return artifacts


def _read_first_paths(run_id: str, artifacts: dict[str, str]) -> list[str]:
    preferred = [
        f".gtestcov/runs/{run_id}/{RESUME_PROMPT_MD}",
        f".gtestcov/runs/{run_id}/{RUN_HANDOFF_MD}",
        ".gtestcov/memory/project_memory.md",
        artifacts.get("task.md", ""),
        artifacts.get("decision_report.md", ""),
        artifacts.get("test_obligations.md", ""),
        artifacts.get("profile_evidence.md", ""),
        artifacts.get("preflight_fix_task.md", ""),
        artifacts.get("next_task.md", ""),
        artifacts.get("stagnation_report.md", ""),
        artifacts.get("failure_diagnosis.md", ""),
        artifacts.get("manual_review_needed.md", ""),
        artifacts.get("source_change_request.md", ""),
    ]
    return _dedupe_strings([item for item in preferred if item])


def _open_questions(root: Path, target: str, status: str, artifacts: dict[str, str]) -> list[str]:
    questions: list[str] = []
    if not target:
        questions.append("No target file has been recorded for this run.")
    if status == "manual_review_needed":
        questions.append("Manual review is required before weak AI test generation can continue.")
    if status == "source_change_request_needed":
        questions.append("A source seam or production change request exists; weak AI must not edit production logic.")
    if "profile_evidence.md" not in artifacts:
        questions.append("Project-specific build/test/coverage profile evidence has not been recorded for this run.")
    if target and _is_external_to_root(root, target):
        questions.append("Target path appears outside --project-root and needs user confirmation.")
    return questions


def _is_external_to_root(root: Path, value: str) -> bool:
    path = Path(value)
    if not path.is_absolute():
        return False
    try:
        path.resolve().relative_to(root)
        return False
    except ValueError:
        return True


def _trim_preflight(preflight: dict[str, Any]) -> dict[str, Any]:
    if not preflight:
        return {}
    return {
        "passed": preflight.get("passed"),
        "blocked": preflight.get("blocked"),
        "violations": preflight.get("audit", {}).get("violations", []),
    }


def _trim_verify(verify: dict[str, Any]) -> dict[str, Any]:
    if not verify:
        return {}
    return {
        "passed": verify.get("passed"),
        "blocked_by_preflight": verify.get("blocked_by_preflight", False),
        "coverage": verify.get("coverage", {}),
        "audit_violations": verify.get("audit", {}).get("violations", []),
        "command_status": {
            label: {
                "configured": command.get("configured"),
                "returncode": command.get("returncode"),
                "diagnostics": command.get("diagnostics", []),
                "skipped": command.get("skipped", False),
            }
            for label, command in verify.get("commands", {}).items()
        },
        "next_round": verify.get("next_round", {}),
    }


def _trim_codrax(data: dict[str, Any]) -> dict[str, Any]:
    if not data:
        return {}
    try:
        evidence = CodraxEvidence.model_validate(data)
    except Exception:
        return data
    return {
        "status": evidence.status,
        "enabled": evidence.enabled,
        "available": evidence.available,
        "file_line_refs": evidence.file_line_refs,
        "related_files": evidence.related_files,
        "notes": evidence.notes,
    }


def _render_verify_state(memory: dict[str, Any]) -> str:
    verify = memory.get("verify") or {}
    if not verify:
        return "- No verify result recorded yet."
    coverage = verify.get("coverage", {})
    lines = [
        f"- Verify passed: `{verify.get('passed')}`",
        f"- Blocked by preflight: `{verify.get('blocked_by_preflight')}`",
    ]
    if coverage:
        lines.append(f"- Coverage found: `{coverage.get('found')}`")
        lines.append(f"- Target line coverage: `{coverage.get('line_rate_percent')}`")
        lines.append(f"- Meets threshold: `{coverage.get('meets_threshold')}`")
    return "\n".join(lines)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _project_detail_policy() -> str:
    return (
        "Generic logic may only rely on stable C/C++ and GTest/GMock rules, coverage parsing, "
        "CLI/MCP flow, and gtestcov's own constraints. Project-specific facts such as build "
        "entry points, test directories, fake/harness/support paths, module boundaries, platform "
        "APIs, and workspace-root meaning must come from user input, project_profile.yaml, CODRAX "
        "file:line evidence, or traceable gtestcov artifacts. If evidence is missing or conflicting, "
        "stop and write manual_review_needed.md instead of guessing."
    )


def _bullets(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values)
