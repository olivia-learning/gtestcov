from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .audit import allowed_write_paths, is_allowed_path, norm_rel
from .models import AnalysisReport, ProjectProfile


WARMUP_JSON = "opencode_permission_warmup.json"
WARMUP_MD = "opencode_permission_warmup.md"


def write_permission_warmup(
    project_root: Path,
    run_dir: Path,
    analysis: AnalysisReport,
    profile: ProjectProfile,
) -> tuple[dict[str, Any], Path, Path]:
    manifest = build_permission_warmup(project_root, run_dir, analysis, profile)
    json_path = run_dir / WARMUP_JSON
    md_path = run_dir / WARMUP_MD
    json_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    md_path.write_text(render_permission_warmup(manifest), encoding="utf-8")
    return manifest, json_path, md_path


def build_permission_warmup(
    project_root: Path,
    run_dir: Path,
    analysis: AnalysisReport,
    profile: ProjectProfile,
) -> dict[str, Any]:
    allowed = allowed_write_paths(profile)
    read_files = _dedupe(
        project_root,
        [
            f".gtestcov/runs/{analysis.run_id}/resume_prompt.md",
            f".gtestcov/runs/{analysis.run_id}/handoff.md",
            ".gtestcov/memory/project_memory.md",
            analysis.target,
            analysis.decision_report_path,
            profile.build.build_file,
            *profile.build.candidate_build_files,
            *_existing_run_artifacts(project_root, run_dir),
        ]
    )
    planned_write_files = _dedupe(
        project_root,
        [
            path
            for path in [*analysis.planned_files, *profile.test_support.test_build_config_paths]
            if path and is_allowed_path(norm_rel(path), allowed)
        ]
    )
    return {
        "run_id": analysis.run_id,
        "target": analysis.target,
        "purpose": "Warm up OpenCode read/edit permission prompts near the start of the run.",
        "read_files": read_files,
        "allowed_write_paths": allowed,
        "planned_write_files": planned_write_files,
        "forbidden_write_paths": [analysis.target, "production/business logic source"],
        "instructions": [
            "At the start, ask OpenCode to read/open the read_files.",
            "If context was refreshed or compressed, read resume_prompt.md and handoff.md again before editing.",
            "Request edit permission only for planned_write_files and allowed_write_paths that are needed.",
            "Do not request edit permission for the target file or production/business logic source.",
            "Do not edit tool-generated gtestcov memory/state files such as handoff.*, project_memory.*, verify.json, or coverage_history.json.",
            "If a needed path is missing from allowed_write_paths, write manual_review_needed.md instead of editing it.",
        ],
    }


def render_permission_warmup(manifest: dict[str, Any]) -> str:
    return f"""# OpenCode Permission Warmup

- Run ID: `{manifest['run_id']}`
- Target: `{manifest['target']}`

Use this at the start of the OpenCode run to concentrate file permission prompts.

## Read/Open First
{_bullets(manifest['read_files'] or ['none'])}

## Request Edit Permission Only If Needed
{_bullets(manifest['planned_write_files'] or manifest['allowed_write_paths'] or ['none'])}

## Never Request Edit Permission For
{_bullets(manifest['forbidden_write_paths'])}

## Instructions
{_bullets(manifest['instructions'])}
"""


def _existing_run_artifacts(project_root: Path, run_dir: Path) -> list[str]:
    names = [
        "codrax_evidence.md",
        "project_understanding.md",
        "test_obligations.md",
        "profile_evidence.md",
        "coverage_goal.json",
    ]
    result: list[str] = []
    for name in names:
        path = run_dir / name
        if path.exists():
            result.append(path.relative_to(project_root).as_posix())
    return result


def _dedupe(project_root: Path, values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _manifest_path(project_root, value)
        if not normalized or normalized in seen:
            continue
        result.append(normalized)
        seen.add(normalized)
    return result


def _manifest_path(project_root: Path, value: str) -> str:
    if not value:
        return ""
    normalized = norm_rel(value)
    path = Path(value)
    if path.is_absolute():
        try:
            normalized = path.resolve().relative_to(project_root.resolve()).as_posix()
        except ValueError:
            return ""
    return normalized


def _bullets(values: list[str]) -> str:
    return "\n".join(f"- `{value}`" for value in values)
