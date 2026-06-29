from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import yaml

from .memory import SCHEMA_VERSION
from .version import (
    INSTALL_MANIFEST,
    default_tool_home,
    detect_install_mode,
    get_version_info,
    git_available,
    git_status,
    load_install_manifest,
    package_root,
    resolve_package_version,
)


UPGRADE_STATE = "upgrade_state.json"
INSPECT_JSON = "old_version_detection_report.json"
INSPECT_MD = "old_version_detection_report.md"
CUSTOM_PATCH = "custom_changes.patch"
CUSTOM_FILES_DIR = "custom_files"
TOOL_STATE = "tool_state.json"
CURRENT_SLOT = "current_slot"

IGNORE_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    ".venv",
    "venv",
    "build",
    "dist",
    "*.egg-info",
}
MANIFEST_IGNORE = {
    INSTALL_MANIFEST,
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    ".venv",
    "venv",
    "build",
    "dist",
}


def install_doctor(
    project_root: Path | None = None,
    tool_root: Path | None = None,
    tool_home: Path | None = None,
) -> dict[str, Any]:
    root = (tool_root or package_root()).resolve()
    home = (tool_home or default_tool_home()).expanduser()
    current_slot = _read_text(home / CURRENT_SLOT)
    version = get_version_info(root)
    project = project_root.resolve() if project_root else None
    warnings = _install_doctor_warnings(version.as_dict())
    return {
        "status": "warning" if warnings else "ok",
        "version": version.as_dict(),
        "tool_home": str(home),
        "tool_slots_dir": str(home / "tool_slots"),
        "current_slot": current_slot,
        "current_slot_path": str(home / "tool_slots" / current_slot) if current_slot else "",
        "git_available": git_available(),
        "project_root": str(project) if project else "",
        "project_gtestcov_dir_exists": bool(project and (project / ".gtestcov").exists()),
        "opencode_command_exists": bool(project and (project / ".opencode" / "commands" / "gtest-cover.md").exists()),
        "doctor_warnings": warnings,
        "doctor_notes": [
            "Run `gtestcov upgrade inspect` before any upgrade.",
            "Run `gtestcov upgrade apply --approve-overwrite-tool-modifications` only after the old-version report is reviewed.",
        ],
    }


def upgrade_inspect(
    tool_root: Path,
    project_root: Path,
    target_ref: str,
    install_mode: str = "auto",
    upgrade_id: str | None = None,
    tool_home: Path | None = None,
) -> dict[str, Any]:
    root = tool_root.resolve()
    project = project_root.resolve()
    home = (tool_home or default_tool_home()).expanduser()
    uid = upgrade_id or _new_upgrade_id()
    reports = _report_dir(home, uid)
    reports.mkdir(parents=True, exist_ok=True)

    mode = detect_install_mode(root, install_mode)
    old_slot = _ensure_current_tool_slot(root, home)
    new_slot = _inactive_slot(home, old_slot)
    version = get_version_info(root, mode)
    tool_report = _inspect_tool(root, mode)
    project_report = _inspect_project(project, uid)

    report = {
        "upgrade_id": uid,
        "status": "inspected",
        "target_ref": target_ref,
        "tool_home": str(home),
        "tool_root": str(root),
        "project_root": str(project),
        "install_mode": mode,
        "old_tool_slot": old_slot,
        "old_tool_slot_path": str(home / "tool_slots" / old_slot),
        "new_tool_slot": new_slot,
        "new_tool_slot_path": str(home / "tool_slots" / new_slot),
        "version": version.as_dict(),
        "tool_report": tool_report,
        "project_report": project_report,
        "preservation_plan": _preservation_plan(),
        "approval_required": True,
        "approval_warning": (
            "Upgrade will replace tool-source edits. Old edits are saved as a patch "
            "or file snapshots; after upgrade, run "
            f"`gtestcov upgrade restore-custom --upgrade-id {uid}` if reviewed custom edits should be restored."
        ),
    }

    (reports / INSPECT_JSON).write_text(json.dumps(report, indent=2), encoding="utf-8")
    (reports / INSPECT_MD).write_text(_render_inspect_report(report), encoding="utf-8")
    _write_custom_snapshots(root, reports, tool_report)
    return {
        "upgrade_id": uid,
        "status": "inspected",
        "report_json": str(reports / INSPECT_JSON),
        "report_md": str(reports / INSPECT_MD),
        "old_tool_slot": old_slot,
        "new_tool_slot": new_slot,
        "approval_required": True,
    }


def upgrade_apply(
    upgrade_id: str,
    approve_overwrite_tool_modifications: bool,
    tool_home: Path | None = None,
    project_root: Path | None = None,
    tool_root: Path | None = None,
    source_tool_root: Path | None = None,
    source_zip: Path | None = None,
    install_mode: str | None = None,
    venv_path: Path | None = None,
    skip_venv_refresh: bool = False,
) -> dict[str, Any]:
    if not approve_overwrite_tool_modifications:
        return {
            "status": "refused",
            "upgrade_id": upgrade_id,
            "reason": "explicit approval is required before overwriting tool modifications",
        }

    home = (tool_home or default_tool_home()).expanduser()
    report = _load_inspect_report(home, upgrade_id)
    if not report:
        return {"status": "blocked", "upgrade_id": upgrade_id, "reason": "old-version detection report not found"}

    project = (project_root or Path(report.get("project_root", "."))).resolve()
    migration = _validate_project_migration(project)
    if not migration["ok"]:
        failure_path = project / ".gtestcov" / "upgrade_slots" / upgrade_id / "upgrade_migration_failed.md"
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_text(_render_migration_failure(migration), encoding="utf-8")
        return {
            "status": "blocked",
            "upgrade_id": upgrade_id,
            "reason": "project state migration validation failed",
            "migration_report": str(failure_path),
            "migration": migration,
        }

    old_slot = str(report.get("old_tool_slot") or _read_text(home / CURRENT_SLOT) or "A")
    new_slot = str(report.get("new_tool_slot") or _inactive_slot(home, old_slot))
    new_slot_path = home / "tool_slots" / new_slot
    _replace_slot_from_source(new_slot_path, source_tool_root, source_zip, tool_root or Path(report["tool_root"]))

    mode = install_mode or report.get("install_mode") or detect_install_mode(new_slot_path)
    if mode == "zip":
        manifest = build_install_manifest(new_slot_path, source=str(source_zip or source_tool_root or "local-copy"))
        write_install_manifest(new_slot_path, manifest)

    _snapshot_project_state(project, upgrade_id, "new")
    venv_refresh = _refresh_reused_venv(new_slot_path, home, venv_path, skip_venv_refresh)
    state = {
        "upgrade_id": upgrade_id,
        "status": "applied",
        "applied_at": _utc_now(),
        "tool_home": str(home),
        "old_tool_slot": old_slot,
        "new_tool_slot": new_slot,
        "active_tool_slot": new_slot,
        "old_tool_slot_path": str(home / "tool_slots" / old_slot),
        "new_tool_slot_path": str(new_slot_path),
        "old_version_report": str(_report_dir(home, upgrade_id) / INSPECT_MD),
        "project_root": str(project),
        "memory_schema_version": SCHEMA_VERSION,
        "install_mode": mode,
        "venv_refresh": venv_refresh,
    }
    project_gtestcov = project / ".gtestcov"
    project_gtestcov.mkdir(parents=True, exist_ok=True)
    (project_gtestcov / UPGRADE_STATE).write_text(json.dumps(state, indent=2), encoding="utf-8")
    _write_text(home / CURRENT_SLOT, new_slot)
    _write_json(home / TOOL_STATE, state)
    return {
        "status": "applied",
        "upgrade_id": upgrade_id,
        "active_tool_slot": new_slot,
        "active_tool_path": str(new_slot_path),
        "project_upgrade_state": str(project_gtestcov / UPGRADE_STATE),
        "venv_refresh": venv_refresh,
    }


def restore_custom(upgrade_id: str, tool_home: Path | None = None) -> dict[str, Any]:
    home = (tool_home or default_tool_home()).expanduser()
    report = _load_inspect_report(home, upgrade_id)
    if not report:
        return {"status": "blocked", "upgrade_id": upgrade_id, "reason": "old-version detection report not found"}

    reports = _report_dir(home, upgrade_id)
    new_slot = Path(report.get("new_tool_slot_path") or "")
    if not new_slot.exists():
        return {"status": "blocked", "upgrade_id": upgrade_id, "reason": "new tool slot does not exist"}

    actions: list[str] = []
    conflicts: list[str] = []
    patch = reports / CUSTOM_PATCH
    if patch.exists() and patch.read_text(encoding="utf-8", errors="ignore").strip():
        if git_available() and (new_slot / ".git").exists():
            check = _run_git_apply(new_slot, patch, check=True)
            if check["returncode"] == 0:
                applied = _run_git_apply(new_slot, patch, check=False)
                if applied["returncode"] == 0:
                    actions.append(f"applied git patch: {patch}")
                else:
                    conflicts.append(applied["stderr"] or "git apply failed")
            else:
                conflicts.append(check["stderr"] or "git apply --check failed")
        else:
            conflicts.append("custom git patch exists, but git or the new slot .git directory is unavailable")

    custom_files = reports / CUSTOM_FILES_DIR
    if custom_files.exists():
        for source in sorted(custom_files.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(custom_files)
            relative_label = relative.as_posix()
            target = new_slot / relative
            if target.exists() and _sha256(target) != _sha256(source):
                conflicts.append(f"{relative_label}: new-version file differs; not overwritten")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            actions.append(f"restored custom file: {relative_label}")

    status = "restored" if actions and not conflicts else "conflicts" if conflicts else "nothing_to_restore"
    result = {
        "status": status,
        "upgrade_id": upgrade_id,
        "actions": actions,
        "conflicts": conflicts,
    }
    report_md = reports / "restore_custom_report.md"
    report_md.write_text(_render_restore_report(result), encoding="utf-8")
    result["report"] = str(report_md)
    return result


def rollback_list(project_root: Path, tool_home: Path | None = None) -> dict[str, Any]:
    project = project_root.resolve()
    slots_dir = project / ".gtestcov" / "upgrade_slots"
    upgrades: list[dict[str, Any]] = []
    if slots_dir.exists():
        for item in sorted(slots_dir.iterdir()):
            if item.is_dir():
                upgrades.append(
                    {
                        "upgrade_id": item.name,
                        "old_snapshot": str(item / "old" / ".gtestcov"),
                        "new_snapshot": str(item / "new" / ".gtestcov"),
                    }
                )
    home = (tool_home or default_tool_home()).expanduser()
    return {
        "project_root": str(project),
        "tool_home": str(home),
        "upgrade_count": len(upgrades),
        "upgrades": upgrades,
    }


def rollback_apply(
    upgrade_id: str,
    project_root: Path,
    approve: bool,
    tool_home: Path | None = None,
    venv_path: Path | None = None,
    skip_venv_refresh: bool = False,
) -> dict[str, Any]:
    if not approve:
        return {"status": "refused", "upgrade_id": upgrade_id, "reason": "rollback requires --approve"}

    project = project_root.resolve()
    home = (tool_home or default_tool_home()).expanduser()
    active = project / ".gtestcov"
    old_snapshot = active / "upgrade_slots" / upgrade_id / "old" / ".gtestcov"
    if not old_snapshot.exists():
        return {"status": "blocked", "upgrade_id": upgrade_id, "reason": "old project snapshot not found"}

    backup = active / "rollback_backups" / f"{upgrade_id}_before_rollback"
    _copy_gtestcov_state(active, backup)
    _restore_gtestcov_state(old_snapshot, active)

    state = _read_json(active / UPGRADE_STATE)
    report = _load_inspect_report(home, upgrade_id)
    old_slot = str(state.get("old_tool_slot") or report.get("old_tool_slot") or "")
    switched_tool = False
    venv_refresh: dict[str, Any] = {"status": "skipped", "reason": "old tool slot not available"}
    if old_slot and (home / "tool_slots" / old_slot).exists():
        _write_text(home / CURRENT_SLOT, old_slot)
        venv_refresh = _refresh_reused_venv(home / "tool_slots" / old_slot, home, venv_path, skip_venv_refresh)
        tool_state = {
            **(state if state else {}),
            "status": "rolled_back",
            "rolled_back_at": _utc_now(),
            "active_tool_slot": old_slot,
            "venv_refresh": venv_refresh,
        }
        _write_json(home / TOOL_STATE, tool_state)
        switched_tool = True

    return {
        "status": "rolled_back",
        "upgrade_id": upgrade_id,
        "project_root": str(project),
        "backup_of_new_state": str(backup),
        "active_tool_slot": old_slot,
        "switched_tool": switched_tool,
        "venv_refresh": venv_refresh,
    }


def build_install_manifest(tool_root: Path, source: str = "") -> dict[str, Any]:
    root = tool_root.resolve()
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _is_manifest_ignored(path, root):
            continue
        rel = path.relative_to(root).as_posix()
        files.append({"path": rel, "sha256": _sha256(path), "size": path.stat().st_size})
    return {
        "install_mode": "zip",
        "version": resolve_package_version(root)["version"],
        "source": source,
        "created_at": _utc_now(),
        "files": files,
    }


def write_install_manifest(tool_root: Path, manifest: dict[str, Any]) -> Path:
    path = tool_root / INSTALL_MANIFEST
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def inspect_zip_modifications(tool_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    root = tool_root.resolve()
    if not manifest:
        return {
            "manifest_present": False,
            "modified_files": [],
            "missing_files": [],
            "untracked_files": [],
            "notes": ["zip mode requested but gtestcov_install_manifest.json is missing"],
        }
    expected = {item["path"]: item for item in manifest.get("files", []) if "path" in item}
    modified: list[str] = []
    missing: list[str] = []
    for rel, item in expected.items():
        path = root / rel
        if not path.exists():
            missing.append(rel)
            continue
        if path.is_file() and _sha256(path) != item.get("sha256"):
            modified.append(rel)

    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not _is_manifest_ignored(path, root)
    }
    untracked = sorted(actual - set(expected))
    return {
        "manifest_present": True,
        "manifest_version": manifest.get("version", ""),
        "manifest_source": manifest.get("source", ""),
        "modified_files": sorted(modified),
        "missing_files": sorted(missing),
        "untracked_files": untracked,
    }


def _inspect_tool(tool_root: Path, mode: str) -> dict[str, Any]:
    if mode == "git":
        status = git_status(tool_root)
        return {
            "mode": "git",
            "git": {key: value for key, value in status.items() if key != "diff"},
            "modified_files": status.get("modified_files", []),
            "untracked_files": status.get("untracked_files", []),
            "custom_patch_available": bool(status.get("diff", "").strip()),
        }
    if mode == "zip":
        manifest = load_install_manifest(tool_root)
        result = inspect_zip_modifications(tool_root, manifest)
        return {"mode": "zip", **result}
    return {
        "mode": "unknown",
        "modified_files": [],
        "untracked_files": [],
        "notes": ["install mode is unknown; pass --install-mode zip or --install-mode git for stronger checks"],
    }


def _inspect_project(project_root: Path, upgrade_id: str) -> dict[str, Any]:
    gtestcov = project_root / ".gtestcov"
    snapshot = ""
    if gtestcov.exists():
        snapshot_path = gtestcov / "upgrade_slots" / upgrade_id / "old" / ".gtestcov"
        _copy_gtestcov_state(gtestcov, snapshot_path)
        snapshot = str(snapshot_path)
    profile = project_root / "project_profile.yaml"
    memory = gtestcov / "memory" / "project_memory.json"
    runs = gtestcov / "runs"
    return {
        "gtestcov_dir_exists": gtestcov.exists(),
        "old_state_snapshot": snapshot,
        "profile_exists": profile.exists(),
        "project_memory_exists": memory.exists(),
        "run_count": len([path for path in runs.iterdir() if path.is_dir()]) if runs.exists() else 0,
    }


def _write_custom_snapshots(tool_root: Path, reports: Path, tool_report: dict[str, Any]) -> None:
    if tool_report.get("mode") == "git":
        status = git_status(tool_root)
        diff = status.get("diff", "")
        if diff.strip():
            (reports / CUSTOM_PATCH).write_text(diff, encoding="utf-8")
        return

    if tool_report.get("mode") == "zip":
        files = [
            *tool_report.get("modified_files", []),
            *tool_report.get("untracked_files", []),
        ]
        target_root = reports / CUSTOM_FILES_DIR
        for rel in sorted(set(files)):
            source = tool_root / rel
            if source.is_file():
                dest = target_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)


def _replace_slot_from_source(
    new_slot_path: Path,
    source_tool_root: Path | None,
    source_zip: Path | None,
    fallback_tool_root: Path,
) -> None:
    _safe_remove_slot(new_slot_path)
    new_slot_path.parent.mkdir(parents=True, exist_ok=True)
    if source_zip:
        _safe_extract_zip(source_zip, new_slot_path)
        _flatten_single_zip_root(new_slot_path)
        return
    source = (source_tool_root or fallback_tool_root).resolve()
    shutil.copytree(source, new_slot_path, ignore=_ignore_tool_copy)


def _ensure_current_tool_slot(tool_root: Path, tool_home: Path) -> str:
    slots = tool_home / "tool_slots"
    slots.mkdir(parents=True, exist_ok=True)
    current_path = tool_home / CURRENT_SLOT
    current = _read_text(current_path)
    if current in {"A", "B"} and (slots / current).exists():
        return current
    current = "A"
    slot_path = slots / current
    if not slot_path.exists():
        shutil.copytree(tool_root, slot_path, ignore=_ignore_tool_copy)
    _write_text(current_path, current)
    return current


def _inactive_slot(tool_home: Path, current: str) -> str:
    return "B" if current == "A" else "A"


def _safe_remove_slot(path: Path) -> None:
    if not path.exists():
        return
    resolved = path.resolve()
    parent = path.parent.resolve()
    try:
        resolved.relative_to(parent)
    except ValueError as exc:
        raise ValueError(f"refusing to remove slot outside its parent: {path}") from exc
    shutil.rmtree(resolved)


def _copy_gtestcov_state(source: Path, dest: Path) -> None:
    if not source.exists():
        return
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest, ignore=_ignore_gtestcov_state_copy)


def _snapshot_project_state(project_root: Path, upgrade_id: str, side: str) -> None:
    gtestcov = project_root / ".gtestcov"
    if not gtestcov.exists():
        return
    dest = gtestcov / "upgrade_slots" / upgrade_id / side / ".gtestcov"
    _copy_gtestcov_state(gtestcov, dest)


def _restore_gtestcov_state(snapshot: Path, active: Path) -> None:
    active.mkdir(parents=True, exist_ok=True)
    for item in list(active.iterdir()):
        if item.name in {"upgrade_slots", "rollback_backups"}:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    for item in snapshot.iterdir():
        dest = active / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)


def _validate_project_migration(project_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": True, "issues": []}
    profile = project_root / "project_profile.yaml"
    if profile.exists():
        try:
            yaml.safe_load(profile.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            result["ok"] = False
            result["issues"].append(f"project_profile.yaml is not valid YAML: {exc}")
    memory = project_root / ".gtestcov" / "memory" / "project_memory.json"
    if memory.exists():
        try:
            data = json.loads(memory.read_text(encoding="utf-8"))
            if "schema_version" in data and not isinstance(data["schema_version"], int):
                result["ok"] = False
                result["issues"].append("project_memory.json schema_version is not an integer")
        except Exception as exc:
            result["ok"] = False
            result["issues"].append(f"project_memory.json is not valid JSON: {exc}")
    return result


def _preservation_plan() -> dict[str, list[str]]:
    return {
        "preserve_verbatim": [
            "old tool slot",
            "user tool custom patch or custom file snapshots",
            ".gtestcov/runs/**",
            "logs and reports",
            "CODRAX evidence",
            "manual_review_needed.md",
            "source_change_request.md",
            "codrax_direct_log.md",
        ],
        "migrate_format": [
            "project_profile.yaml",
            ".gtestcov/memory/project_memory.json",
            ".gtestcov/upgrade_state.json",
        ],
        "rebuild": [
            "handoff.md/json",
            "resume_prompt.md",
            "opencode_permission_warmup.*",
            "regeneratable task package sections",
        ],
    }


def _render_inspect_report(report: dict[str, Any]) -> str:
    tool_report = report["tool_report"]
    project_report = report["project_report"]
    plan = report["preservation_plan"]
    version = report["version"]
    dirty = _tool_dirty(tool_report, version)
    lines = [
        "# gtestcov Old Version Detection Report",
        "",
        f"- Upgrade ID: `{report['upgrade_id']}`",
        f"- Current version: `{version['version']}`",
        f"- Version source: `{version.get('version_source', 'unknown')}`",
        f"- Install mode: `{report['install_mode']}`",
        f"- Git branch: `{version.get('git_branch') or 'none'}`",
        f"- Git dirty: `{str(version.get('git_dirty', False)).lower()}`",
        f"- Git modified count: `{version.get('git_modified_count', 0)}`",
        f"- Local dirty state: `{str(dirty).lower()}`",
        f"- Tool root: `{report['tool_root']}`",
        f"- Project root: `{report['project_root']}`",
        f"- Target ref: `{report['target_ref']}`",
        f"- Old tool slot: `{report['old_tool_slot_path']}`",
        f"- New tool slot: `{report['new_tool_slot_path']}`",
        "",
        "## Approval Warning",
        report["approval_warning"],
        "",
        "## Tool Changes Detected",
        f"- Modified files: {len(tool_report.get('modified_files', []))}",
        _bullets(tool_report.get("modified_files", []) or ["none"]),
        f"- Untracked files: {len(tool_report.get('untracked_files', []))}",
        _bullets(tool_report.get("untracked_files", []) or ["none"]),
        f"- Missing files: {len(tool_report.get('missing_files', []))}",
        _bullets(tool_report.get("missing_files", []) or ["none"]),
        "",
        "## Project State",
        f"- `.gtestcov` exists: `{project_report.get('gtestcov_dir_exists')}`",
        f"- Old project snapshot: `{project_report.get('old_state_snapshot') or 'none'}`",
        f"- `project_profile.yaml` exists: `{project_report.get('profile_exists')}`",
        f"- Project memory exists: `{project_report.get('project_memory_exists')}`",
        f"- Run count: `{project_report.get('run_count')}`",
        "",
        "## Preservation And Migration Classification",
        "Preserve without modification:",
        _bullets(plan["preserve_verbatim"]),
        "",
        "Migrate format when needed:",
        _bullets(plan["migrate_format"]),
        "",
        "Regenerate when needed:",
        _bullets(plan["rebuild"]),
        "",
        "## Required Next Step",
        (
            "Review this report. If you accept that the upgrade will replace tool-source edits, "
            f"run `gtestcov upgrade apply --upgrade-id {report['upgrade_id']} "
            "--approve-overwrite-tool-modifications`."
        ),
    ]
    return "\n".join(lines).rstrip() + "\n"


def _install_doctor_warnings(version: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if version.get("install_mode") == "zip":
        manifest_version = str(version.get("zip_manifest_version") or "")
        runtime_version = str(version.get("version") or "")
        if manifest_version and runtime_version and manifest_version != runtime_version:
            warnings.append(
                "zip manifest version "
                f"{manifest_version} does not match runtime tool version {runtime_version}; "
                "run upgrade inspect before replacing this installation."
            )
    return warnings


def _tool_dirty(tool_report: dict[str, Any], version: dict[str, Any]) -> bool:
    return bool(
        version.get("git_dirty")
        or tool_report.get("modified_files")
        or tool_report.get("untracked_files")
        or tool_report.get("missing_files")
    )


def _render_migration_failure(migration: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# gtestcov Upgrade Migration Failed",
            "",
            "The active project state was not overwritten because migration validation found issues.",
            "",
            "## Issues",
            _bullets(migration.get("issues", []) or ["unknown migration issue"]),
        ]
    ).rstrip() + "\n"


def _render_restore_report(result: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# gtestcov Restore Custom Modifications Report",
            "",
            f"- Upgrade ID: `{result['upgrade_id']}`",
            f"- Status: `{result['status']}`",
            "",
            "## Actions",
            _bullets(result.get("actions", []) or ["none"]),
            "",
            "## Conflicts",
            _bullets(result.get("conflicts", []) or ["none"]),
        ]
    ).rstrip() + "\n"


def _load_inspect_report(tool_home: Path, upgrade_id: str) -> dict[str, Any]:
    path = _report_dir(tool_home, upgrade_id) / INSPECT_JSON
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _report_dir(tool_home: Path, upgrade_id: str) -> Path:
    return tool_home / "upgrade_reports" / upgrade_id


def _new_upgrade_id() -> str:
    return "upgrade-" + _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_manifest_ignored(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    for part in rel_parts:
        if part in MANIFEST_IGNORE or part.endswith(".egg-info"):
            return True
    return path.name == INSTALL_MANIFEST


def _ignore_tool_copy(_dir: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in IGNORE_DIRS or name.endswith(".egg-info") or name == INSTALL_MANIFEST:
            ignored.add(name)
    return ignored


def _ignore_gtestcov_state_copy(_dir: str, names: list[str]) -> set[str]:
    return {name for name in names if name in {"upgrade_slots", "rollback_backups"}}


def _flatten_single_zip_root(root: Path) -> None:
    children = [item for item in root.iterdir()]
    if len(children) != 1 or not children[0].is_dir():
        return
    child = children[0]
    temp = root.parent / f"{root.name}_ziproot_tmp"
    child.rename(temp)
    for item in list(root.iterdir()):
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    for item in temp.iterdir():
        shutil.move(str(item), str(root / item.name))
    temp.rmdir()


def _safe_extract_zip(source_zip: Path, dest: Path) -> None:
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(source_zip) as archive:
        for member in archive.infolist():
            target = (dest / member.filename).resolve()
            try:
                target.relative_to(dest_resolved)
            except ValueError as exc:
                raise ValueError(f"refusing to extract zip member outside destination: {member.filename}") from exc
        archive.extractall(dest)


def _refresh_reused_venv(
    active_tool_root: Path,
    tool_home: Path,
    venv_path: Path | None,
    skip: bool,
) -> dict[str, Any]:
    if skip:
        return {"status": "skipped", "reason": "skip_venv_refresh requested"}

    detected = _detect_reused_venv_python(tool_home, venv_path)
    if not detected.get("python"):
        return {
            "status": "needs_ai_action",
            "reason": detected.get("reason", "no reusable virtual environment detected"),
            "command": f"python -m pip install --no-deps -e {active_tool_root}",
            "note": "AI should run this inside the existing gtestcov virtual environment; the user should not do it manually.",
        }

    command = [detected["python"], "-m", "pip", "install", "--no-deps", "-e", str(active_tool_root)]
    try:
        completed = subprocess.run(command, check=False, text=True, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "failed",
            "python": detected["python"],
            "venv": detected.get("venv", ""),
            "command": command,
            "error": str(exc),
        }

    return {
        "status": "refreshed" if completed.returncode == 0 else "failed",
        "python": detected["python"],
        "venv": detected.get("venv", ""),
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _detect_reused_venv_python(tool_home: Path, venv_path: Path | None) -> dict[str, str]:
    candidates: list[Path] = []
    if venv_path:
        candidates.extend(_python_candidates_from_venv(venv_path.expanduser()))

    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        candidates.append(Path(sys.executable))

    virtual_env = os.environ.get("VIRTUAL_ENV", "")
    if virtual_env:
        candidates.extend(_python_candidates_from_venv(Path(virtual_env).expanduser()))

    candidates.extend(_python_candidates_from_venv(tool_home / ".venv"))
    candidates.extend(_python_candidates_from_venv(tool_home / ".venv-gtestcov"))

    for candidate in candidates:
        if candidate.exists():
            return {"python": str(candidate), "venv": str(candidate.parent.parent if candidate.parent.name in {"bin", "Scripts"} else candidate.parent)}

    return {
        "python": "",
        "reason": (
            "no virtual environment was passed with --venv, the current process is not running inside a venv, "
            "and no reusable venv exists under the gtestcov tool home"
        ),
    }


def _python_candidates_from_venv(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return [
        path / "bin" / "python",
        path / "bin" / "python3",
        path / "Scripts" / "python.exe",
        path / "Scripts" / "python",
    ]


def _run_git_apply(cwd: Path, patch: Path, check: bool) -> dict[str, Any]:
    args = ["git", "-c", f"safe.directory={cwd.resolve()}", "apply"]
    if check:
        args.append("--check")
    args.append(str(patch))
    completed = subprocess.run(args, cwd=str(cwd), check=False, text=True, capture_output=True, timeout=30)
    return {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _bullets(values: list[str]) -> str:
    return "\n".join(f"- `{value}`" for value in values)
