from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from .codrax import FILE_LINE_RE, execute_codrax_request, render_codrax_evidence, write_codrax_evidence
from .fs import ensure_run_dir
from .memory import refresh_memory
from .models import CodraxEvidence, ProjectProfile
from .profile import PROFILE_NAME, load_profile, profile_to_yaml


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
    evidence = execute_codrax_request(root, cfg, build_profile_sync_request(target, build_file), enabled=cfg.enabled, run_dir=run_dir)

    if evidence.status != "ok":
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
            "codrax_evidence": evidence.model_dump(mode="json"),
            "notes": ["CODRAX evidence was not usable; profile was not updated."],
        }
        refresh_memory(root, run_id)
        return result

    updates = parse_profile_updates(evidence)
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
        write_codrax_evidence(run_dir, evidence)
        profile_evidence_path = write_profile_evidence(run_dir, target, evidence, updates, comparison)
        result = {
            "run_id": run_id,
            "status": status,
            "updated": False,
            "profile_path": str(root / PROFILE_NAME),
            "backup_path": "",
            "profile_evidence_path": str(profile_evidence_path),
            "updates": updates,
            "build_file_comparison": comparison,
            "codrax_evidence": evidence.model_dump(mode="json"),
            "notes": [f"User build file anchor comparison status was {comparison['status']}; profile was not updated."],
        }
        refresh_memory(root, run_id)
        return result

    backup_path = backup_profile(root, run_id, profile)
    updated_profile = apply_profile_updates(profile, updates)
    profile_path = root / PROFILE_NAME
    profile_path.write_text(profile_to_yaml(updated_profile), encoding="utf-8")
    write_codrax_evidence(run_dir, evidence)
    profile_evidence_path = write_profile_evidence(run_dir, target, evidence, updates, comparison)

    result = {
        "run_id": run_id,
        "status": "ok",
        "updated": True,
        "profile_path": str(profile_path),
        "backup_path": str(backup_path),
        "profile_evidence_path": str(profile_evidence_path),
        "updates": updates,
        "build_file_comparison": comparison,
        "codrax_evidence": evidence.model_dump(mode="json"),
    }
    refresh_memory(root, run_id)
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


def backup_profile(project_root: Path, run_id: str, profile: ProjectProfile) -> Path:
    backup_dir = project_root / ".gtestcov" / "profile_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"project_profile.{run_id}.yaml"
    profile_path = project_root / PROFILE_NAME
    if profile_path.exists():
        shutil.copy2(profile_path, backup_path)
    else:
        backup_path.write_text(profile_to_yaml(profile), encoding="utf-8")
    return backup_path


def write_profile_evidence(
    run_dir: Path,
    target: str,
    evidence: CodraxEvidence,
    updates: dict[str, dict[str, Any]],
    build_file_comparison: dict[str, Any] | None = None,
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
