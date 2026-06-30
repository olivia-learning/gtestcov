from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from .codrax import FILE_LINE_RE, execute_codrax_request, render_codrax_evidence, write_codrax_evidence
from .evidence_pack import attach_cache, evidence_cache_status, load_codrax_payload, store_codrax_payload
from .evidence_paths import codrax_test_source_dirs
from .fs import ensure_run_dir, resolve_project_path
from .memory import refresh_memory
from .models import CodraxEvidence, ProjectProfile
from .profile import PROFILE_NAME, load_profile, profile_to_yaml
from .run_status import update_run_status


SCALAR_FIELDS = {
    "project_name",
    "build.system",
    "build.build_file",
    "build.build_command",
    "build.incremental_build_command",
    "build.test_command",
    "build.filtered_test_command",
    "build.coverage_command",
    "build.target_coverage_command",
    "build.coverage_xml",
    "dependency.manifest",
    "dependency.host_shim_dir",
    "test_support.fake_dir",
    "test_support.harness_dir",
    "test_support.guard_dir",
    "test_support.builder_dir",
    "test_support.dependency_shim_dir",
}

LIST_FIELDS = {
    "build.candidate_build_files",
    "dependency.manifest_candidates",
    "dependency.dependency_root",
    "dependency.exclude_from_coverage",
    "test_support.test_dirs",
    "test_support.test_build_config_paths",
    "style.preferred_macros",
    "style.forbidden_macros",
}

FLOAT_FIELDS = {
    "coverage.changed_line",
    "targets.default_line_coverage",
}

BOOL_FIELDS = {
    "evidence.codrax.direct_mode.enabled",
    "evidence.codrax.direct_mode.require_audit_log",
}

EXECUTABLE_COMMAND_FIELDS = {
    "build.build_command",
    "build.incremental_build_command",
    "build.test_command",
    "build.filtered_test_command",
    "build.coverage_command",
    "build.target_coverage_command",
}


def profile_sync(
    project_root: Path,
    target: str,
    run_id: str | None = None,
    line_coverage: float | None = None,
    build_file: str | None = None,
) -> dict[str, Any]:
    root = project_root.resolve()
    run_id, run_dir = ensure_run_dir(root, run_id)
    profile = load_profile(root)
    cfg = profile.evidence.codrax
    resolve_project_path(root, target)
    if build_file:
        resolve_project_path(root, build_file)
    update_run_status(
        run_dir,
        phase="profile_sync.start",
        command="profile-sync",
        target=target,
        current_operation="codrax_profile_sync",
        extra={"line_coverage": line_coverage, "build_file": build_file or ""},
    )
    profile_sync_request = build_profile_sync_request(target, build_file)
    evidence, cache = load_codrax_payload(
        root,
        target,
        "profile_sync",
        request_key=profile_sync_request,
    )
    if evidence is None:
        evidence = execute_codrax_request(
            root,
            cfg,
            profile_sync_request,
            enabled=cfg.enabled,
            run_dir=run_dir,
            operation_name="profile_sync",
        )
        cache = store_codrax_payload(
            root,
            target,
            "profile_sync",
            evidence,
            request_key=profile_sync_request,
            previous_cache=cache,
        )
        evidence = attach_cache(evidence, cache)
    update_run_status(
        run_dir,
        phase="profile_sync.codrax_done",
        command="profile-sync",
        target=target,
        current_operation="parse_profile_updates",
        notes=[f"CODRAX status: {evidence.status}"],
        extra={"codrax_status": evidence.status, "evidence_cache": evidence_cache_status(evidence)},
    )

    updates: dict[str, dict[str, Any]] = {}
    report_evidence = evidence
    if evidence.status == "ok":
        updates = parse_profile_updates(evidence)
        add_user_build_file_candidate_from_evidence(updates, evidence, build_file or "")
        add_test_support_dirs_from_evidence(updates, evidence)
    anchor_evidence: CodraxEvidence | None = None
    if build_file and not build_file_candidates_from_updates(updates):
        anchor_request = build_file_anchor_request(target, build_file)
        anchor_evidence, anchor_cache = load_codrax_payload(
            root,
            target,
            "profile_sync_build_file_anchor",
            request_key=anchor_request,
        )
        if anchor_evidence is None:
            anchor_evidence = execute_codrax_request(
                root,
                cfg,
                anchor_request,
                enabled=cfg.enabled,
                run_dir=run_dir,
                operation_name="profile_sync_build_file_anchor",
            )
            anchor_cache = store_codrax_payload(
                root,
                target,
                "profile_sync_build_file_anchor",
                anchor_evidence,
                request_key=anchor_request,
                previous_cache=anchor_cache,
            )
            anchor_evidence = attach_cache(anchor_evidence, anchor_cache)
        add_user_build_file_candidate_from_evidence(updates, anchor_evidence, build_file)
        if evidence.status != "ok" and build_file_candidates_from_updates(updates):
            report_evidence = anchor_evidence

    if evidence.status != "ok" and not build_file_candidates_from_updates(updates):
        write_codrax_evidence(run_dir, evidence)
        manual_path = run_dir / "manual_review_needed.md"
        manual_path.write_text(
            "# Manual Review Needed\n\n"
            f"CODRAX profile-sync status is `{evidence.status}`. "
            "Cannot safely infer build/test/coverage profile fields without cited evidence.\n",
            encoding="utf-8",
        )
        result = {
            "run_id": run_id,
            "status": evidence.status,
            "updated": False,
            "profile_path": str(root / PROFILE_NAME),
            "backup_path": "",
            "profile_evidence_path": "",
            "updates": {},
            "executable_command_candidates": {},
            "codrax_evidence": evidence.model_dump(mode="json"),
            "evidence_cache": evidence_cache_status(evidence),
            "notes": ["CODRAX evidence was not usable; profile was not updated."],
        }
        update_run_status(
            run_dir,
            phase="profile_sync.manual_review_needed",
            command="profile-sync",
            target=target,
            current_operation="memory_refresh",
            last_artifact=str(manual_path),
            notes=[f"CODRAX evidence was not usable: {evidence.status}"],
            extra={"evidence_cache": evidence_cache_status(evidence)},
        )
        refresh_memory(root, run_id)
        update_run_status(
            run_dir,
            phase="profile_sync.stopped",
            command="profile-sync",
            target=target,
            current_operation="done",
            last_artifact=str(manual_path),
        )
        return result

    if line_coverage is not None:
        updates["coverage.changed_line"] = {
            "value": float(line_coverage),
            "evidence": ["user input"],
            "source": "user",
        }
        updates["targets.default_line_coverage"] = {
            "value": float(line_coverage),
            "evidence": ["user input"],
            "source": "user",
        }
    comparison = compare_build_file_anchor(build_file or "", updates)
    if build_file:
        updates["build.build_file"] = {
            "value": _normalize_path(build_file),
            "evidence": ["user input"],
            "source": "user",
        }
    executable_command_candidates = extract_executable_command_candidates(updates)
    if comparison["status"] in {"mismatch", "no_codrax_candidates"}:
        status = "build_file_mismatch" if comparison["status"] == "mismatch" else "build_file_unverified"
        reason = (
            "CODRAX found build file candidates that do not include the user-provided build file anchor."
            if comparison["status"] == "mismatch"
            else "CODRAX did not return any build file candidates to compare with the user-provided build file anchor."
        )
        manual_path = run_dir / "manual_review_needed.md"
        manual_path.write_text(
            "# Manual Review Needed\n\n"
            f"{reason}\n"
            f"- User build file: `{comparison['user_build_file']}`\n"
            f"- CODRAX candidates: `{comparison['codrax_candidates']}`\n\n"
            "Please confirm the intended build/test configuration before generating tests.\n",
            encoding="utf-8",
        )
        write_codrax_evidence(run_dir, report_evidence)
        profile_evidence_path = write_profile_evidence(
            run_dir,
            target,
            report_evidence,
            updates,
            comparison,
            executable_command_candidates,
        )
        notes = [f"User build file anchor comparison status was {comparison['status']}; profile was not updated."]
        if executable_command_candidates:
            notes.append("CODRAX executable command candidates require user review and were not written to executable profile fields.")
        result = {
            "run_id": run_id,
            "status": status,
            "updated": False,
            "profile_path": str(root / PROFILE_NAME),
            "backup_path": "",
            "profile_evidence_path": str(profile_evidence_path),
            "updates": updates,
            "executable_command_candidates": executable_command_candidates,
            "build_file_comparison": comparison,
            "codrax_evidence": report_evidence.model_dump(mode="json"),
            "evidence_cache": evidence_cache_status(report_evidence),
            "notes": notes,
        }
        update_run_status(
            run_dir,
            phase="profile_sync.build_file_conflict",
            command="profile-sync",
            target=target,
            current_operation="memory_refresh",
            last_artifact=str(manual_path),
            notes=[reason],
            extra={"build_file_comparison": comparison, "evidence_cache": evidence_cache_status(report_evidence)},
        )
        refresh_memory(root, run_id)
        update_run_status(
            run_dir,
            phase="profile_sync.stopped",
            command="profile-sync",
            target=target,
            current_operation="done",
            last_artifact=str(profile_evidence_path),
        )
        return result

    update_run_status(
        run_dir,
        phase="profile_sync.update_profile",
        command="profile-sync",
        target=target,
        current_operation="write_project_profile",
        extra={"update_count": len(updates), "build_file_comparison": comparison},
    )
    updated_profile = apply_profile_updates(profile, updates)
    profile_path = root / PROFILE_NAME
    updated_profile_text = profile_to_yaml(updated_profile)
    current_profile_text = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
    profile_changed = current_profile_text != updated_profile_text
    backup_path = backup_profile(root, run_id, profile) if profile_changed else None
    if profile_changed:
        profile_path.write_text(updated_profile_text, encoding="utf-8")
    if profile_changed:
        refreshed_cache = store_codrax_payload(
            root,
            target,
            "profile_sync",
            report_evidence,
            request_key=profile_sync_request,
            previous_cache=report_evidence.cache,
        )
        report_evidence = attach_cache(report_evidence, refreshed_cache)
    write_codrax_evidence(run_dir, report_evidence)
    profile_evidence_path = write_profile_evidence(
        run_dir,
        target,
        report_evidence,
        updates,
        comparison,
        executable_command_candidates,
    )
    notes = []
    if executable_command_candidates:
        notes.append("CODRAX executable command candidates require user review and were not written to executable profile fields.")

    result = {
        "run_id": run_id,
        "status": "ok",
        "updated": profile_changed,
        "profile_path": str(profile_path),
        "backup_path": str(backup_path) if backup_path else "",
        "profile_evidence_path": str(profile_evidence_path),
        "updates": updates,
        "executable_command_candidates": executable_command_candidates,
        "build_file_comparison": comparison,
        "codrax_evidence": report_evidence.model_dump(mode="json"),
        "evidence_cache": evidence_cache_status(report_evidence),
        "notes": notes,
    }
    update_run_status(
        run_dir,
        phase="profile_sync.memory_refresh",
        command="profile-sync",
        target=target,
        current_operation="memory_refresh",
        last_artifact=str(profile_evidence_path),
        extra={"evidence_cache": evidence_cache_status(report_evidence)},
    )
    refresh_memory(root, run_id)
    update_run_status(
        run_dir,
        phase="profile_sync.done",
        command="profile-sync",
        target=target,
        current_operation="done",
        last_artifact=str(profile_evidence_path),
        extra={"evidence_cache": evidence_cache_status(report_evidence)},
    )
    return result


def build_profile_sync_request(target: str, build_file: str | None = None) -> str:
    fields = sorted([*SCALAR_FIELDS, *LIST_FIELDS, *FLOAT_FIELDS, *BOOL_FIELDS])
    field_list = "\n".join(f"- {field}" for field in fields)
    return f"""Read-only repository profile synchronization for gtest coverage generation.

Target file: {target}
User-provided build file anchor: {build_file or 'not provided'}

Find the safest existing project configuration for building, running a focused gtest, and producing target-file coverage.
Use small or incremental commands when repository evidence shows they are supported.
Do not invent commands. Do not edit files.
Also return every plausible build/test configuration file related to this target in build.candidate_build_files.

Return only lines in this form:
field.path: value  # file:line evidence

Allowed fields:
{field_list}

Rules:
- Every non-empty field value must cite repository file:line evidence.
- For list fields, return comma-separated paths or values.
- If a field is not visible, omit it.
- Prefer filtered test and target coverage commands when cited.
- Compare CODRAX-cited build files with the user-provided build file anchor when one is provided.
"""


def build_file_anchor_request(target: str, build_file: str) -> str:
    return f"""Read-only build-file anchor verification for gtestcov.

Target file: {target}
User-provided build file anchor: {build_file}

Only verify whether the user-provided build file is a real repository build or test configuration file for this target.
Do not infer or invent commands.
Do not use project_profile.yaml, .gtestcov, or .codrax as evidence.

If the build file is supported by repository evidence, return exactly this machine-readable line:
build.candidate_build_files: {build_file}  # {build_file}:<line>

The cited line must come from the user-provided build file and should show module registration, target source registration, test registration, or another build/test configuration statement.
If you cannot cite that build file as file:line, return exactly:
not found
"""


def parse_profile_updates(evidence: CodraxEvidence) -> dict[str, dict[str, Any]]:
    updates: dict[str, dict[str, Any]] = {}
    allowed = SCALAR_FIELDS | LIST_FIELDS | FLOAT_FIELDS | BOOL_FIELDS
    for raw_line in evidence.stdout_excerpt.splitlines():
        line = raw_line.strip().lstrip("-* ").strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key not in allowed:
            continue
        refs = _file_refs(line)
        if not refs:
            continue
        value = _strip_comment(value).strip().strip("`")
        if not value or value.lower() in {"not found", "none", "unknown"}:
            continue
        parsed = parse_field_value(key, value)
        if parsed in ("", [], None):
            continue
        updates[key] = {"value": parsed, "evidence": refs, "source": "codrax"}
    return updates


def add_user_build_file_candidate_from_evidence(
    updates: dict[str, dict[str, Any]],
    evidence: CodraxEvidence,
    build_file: str,
) -> None:
    user_build_file = _normalize_path(build_file)
    if not user_build_file:
        return
    matching_refs = [
        ref for ref in evidence.file_line_refs
        if _paths_match(ref.rsplit(":", 1)[0], user_build_file)
    ]
    if not matching_refs:
        return
    existing = updates.get("build.candidate_build_files", {}).get("value")
    candidates = existing if isinstance(existing, list) else []
    merged = _dedupe_paths([*candidates, user_build_file])
    updates["build.candidate_build_files"] = {
        "value": merged,
        "evidence": matching_refs,
        "source": "codrax_final_output_fallback",
    }


def add_test_support_dirs_from_evidence(updates: dict[str, dict[str, Any]], evidence: CodraxEvidence) -> None:
    refs_by_dir = codrax_test_source_dirs(evidence)
    if not refs_by_dir:
        return
    existing = updates.get("test_support.test_dirs", {}).get("value")
    current_dirs = existing if isinstance(existing, list) else []
    merged = _dedupe_paths([*current_dirs, *refs_by_dir.keys()])
    evidence_refs: list[str] = []
    for test_dir in merged:
        evidence_refs.extend(refs_by_dir.get(test_dir, []))
    updates["test_support.test_dirs"] = {
        "value": merged,
        "evidence": _dedupe_paths(evidence_refs),
        "source": "codrax_existing_tests_fallback",
    }


def parse_field_value(key: str, value: str) -> Any:
    if key in LIST_FIELDS:
        return _parse_list(value)
    if key in FLOAT_FIELDS:
        match = re.search(r"\d+(?:\.\d+)?", value)
        return float(match.group(0)) if match else None
    if key in BOOL_FIELDS:
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return value.strip().strip("'\"")


def compare_build_file_anchor(build_file: str, updates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    user_build_file = _normalize_path(build_file)
    candidates = build_file_candidates_from_updates(updates)
    if not user_build_file:
        return {
            "status": "not_provided",
            "user_build_file": "",
            "codrax_candidates": candidates,
            "matched": False,
        }
    if not candidates:
        return {
            "status": "no_codrax_candidates",
            "user_build_file": user_build_file,
            "codrax_candidates": [],
            "matched": False,
        }
    matched = any(_paths_match(user_build_file, candidate) for candidate in candidates)
    return {
        "status": "matched" if matched else "mismatch",
        "user_build_file": user_build_file,
        "codrax_candidates": candidates,
        "matched": matched,
    }


def build_file_candidates_from_updates(updates: dict[str, dict[str, Any]]) -> list[str]:
    values: list[str] = []
    build_file = updates.get("build.build_file", {}).get("value")
    if isinstance(build_file, str) and build_file:
        values.append(build_file)
    candidate_files = updates.get("build.candidate_build_files", {}).get("value")
    if isinstance(candidate_files, list):
        values.extend(candidate_files)
    for key in ("build.build_command", "build.incremental_build_command", "build.test_command", "build.filtered_test_command", "build.coverage_command", "build.target_coverage_command"):
        for ref in updates.get(key, {}).get("evidence", []):
            if isinstance(ref, str):
                values.append(ref.rsplit(":", 1)[0])
    return _dedupe_paths(values)


def apply_profile_updates(profile: ProjectProfile, updates: dict[str, dict[str, Any]]) -> ProjectProfile:
    updated = profile.model_copy(deep=True)
    for key, item in updates.items():
        _set_nested(updated, key, item["value"])
    return updated


def extract_executable_command_candidates(updates: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for key in sorted(EXECUTABLE_COMMAND_FIELDS):
        item = updates.pop(key, None)
        if item is None:
            continue
        candidates[key] = {
            **item,
            "requires_user_review": True,
            "review_status": "not_written_to_profile",
            "risk_note": (
                "CODRAX returned an executable shell command candidate. "
                "Round05 keeps it as review evidence and does not write it to project_profile.yaml automatically."
            ),
        }
    return candidates


def backup_profile(project_root: Path, run_id: str, profile: ProjectProfile) -> Path:
    backup_dir = project_root / ".gtestcov" / "profile_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"project_profile.{run_id}.yaml"
    profile_path = project_root / PROFILE_NAME
    if profile_path.exists():
        try:
            shutil.copy2(profile_path, backup_path)
        except OSError:
            shutil.copyfile(profile_path, backup_path)
    else:
        backup_path.write_text(profile_to_yaml(profile), encoding="utf-8")
    return backup_path


def write_profile_evidence(
    run_dir: Path,
    target: str,
    evidence: CodraxEvidence,
    updates: dict[str, dict[str, Any]],
    build_file_comparison: dict[str, Any] | None = None,
    executable_command_candidates: dict[str, dict[str, Any]] | None = None,
) -> Path:
    path = run_dir / "profile_evidence.md"
    lines = [
        "# Profile Evidence",
        "",
        f"- Target: `{target}`",
        f"- CODRAX status: `{evidence.status}`",
        "",
        "## Updated Fields",
    ]
    if not updates:
        lines.append("- none")
    for key, item in sorted(updates.items()):
        lines.append(f"- `{key}` = `{item['value']}`; evidence={item['evidence']}")
    command_candidates = executable_command_candidates or {}
    lines.extend(["", "## Executable Command Candidates Requiring Review"])
    if not command_candidates:
        lines.append("- none")
    for key, item in sorted(command_candidates.items()):
        lines.append(
            f"- `{key}` = `{item['value']}`; evidence={item['evidence']}; "
            f"review_status=`{item.get('review_status')}`"
        )
    if command_candidates:
        lines.append("")
        lines.append(
            "These CODRAX-returned shell command candidates were not written to executable "
            "project_profile.yaml fields. Review and copy them manually only after confirming they are safe for this project."
        )
    comparison = build_file_comparison or {}
    if comparison:
        lines.extend(
            [
                "",
                "## Build File Anchor Comparison",
                f"- Status: `{comparison.get('status')}`",
                f"- User build file: `{comparison.get('user_build_file') or 'not provided'}`",
                f"- CODRAX candidates: `{comparison.get('codrax_candidates') or []}`",
            ]
        )
    lines.extend(["", "## CODRAX Evidence", render_codrax_evidence(evidence).rstrip(), ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _set_nested(profile: ProjectProfile, key: str, value: Any) -> None:
    parts = key.split(".")
    obj: Any = profile
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)


def _parse_list(value: str) -> list[str]:
    normalized = value.strip()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    parts = [part.strip().strip("'\"`") for part in normalized.split(",")]
    return [part for part in parts if part]


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip().lstrip("./")


def _dedupe_paths(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_path(value)
        if not normalized or normalized in seen:
            continue
        result.append(normalized)
        seen.add(normalized)
    return result


def _paths_match(left: str, right: str) -> bool:
    left_norm = _normalize_path(left)
    right_norm = _normalize_path(right)
    return left_norm == right_norm


def _strip_comment(value: str) -> str:
    without_refs = FILE_LINE_RE.sub("", value)
    return without_refs.split("#", 1)[0].strip()


def _file_refs(line: str) -> list[str]:
    refs: list[str] = []
    for match in FILE_LINE_RE.finditer(line):
        path = match.group("path").replace("\\", "/")
        refs.append(f"{path}:{match.group('line')}")
    return refs
