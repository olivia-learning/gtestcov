from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .fs import resolve_project_path, validate_profile_paths
from .models import ProjectProfile


PROFILE_NAME = "project_profile.yaml"


def load_profile(project_root: Path) -> ProjectProfile:
    path = project_root / PROFILE_NAME
    if not path.exists():
        profile = ProjectProfile()
        validate_profile_paths(project_root, profile.paths)
        return profile
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profile = ProjectProfile.model_validate(data)
    validate_profile_paths(project_root, profile.paths)
    return profile


def profile_to_yaml(profile: ProjectProfile) -> str:
    return yaml.safe_dump(profile.model_dump(mode="json"), sort_keys=False, allow_unicode=False)


def write_default_profile(project_root: Path, overwrite: bool = False) -> Path:
    path = project_root / PROFILE_NAME
    if path.exists() and not overwrite:
        return path
    path.write_text(profile_to_yaml(ProjectProfile()), encoding="utf-8")
    return path


def write_init_profile(
    project_root: Path,
    *,
    overwrite: bool = False,
    source_roots: list[str] | None = None,
    test_roots: list[str] | None = None,
    build_roots: list[str] | None = None,
    exclude_dirs: list[str] | None = None,
    build_file: str | None = None,
    max_files: int | None = None,
    max_file_bytes: int | None = None,
) -> tuple[Path, ProjectProfile, bool]:
    path = project_root / PROFILE_NAME
    has_init_options = any(
        [
            source_roots,
            test_roots,
            build_roots,
            exclude_dirs,
            build_file is not None,
            max_files is not None,
            max_file_bytes is not None,
        ]
    )
    if path.exists() and not overwrite and not has_init_options:
        return path, ProjectProfile(), False

    profile = load_profile(project_root) if path.exists() and not overwrite else ProjectProfile()
    if source_roots is not None:
        profile.paths.source_roots = _normalize_project_paths(project_root, source_roots, "source root")
    if test_roots is not None:
        profile.paths.test_roots = _normalize_project_paths(project_root, test_roots, "test root")
    if build_roots is not None:
        profile.paths.build_roots = _normalize_project_paths(project_root, build_roots, "build root")
    if exclude_dirs is not None:
        profile.paths.exclude_dirs = _unique(
            [
                *profile.paths.exclude_dirs,
                *_normalize_project_paths(project_root, exclude_dirs, "exclude dir"),
            ]
        )
    if build_file is not None:
        normalized_build_file = _normalize_project_path(project_root, build_file, "build file")
        profile.build.build_file = normalized_build_file
        profile.build.candidate_build_files = _unique(
            [normalized_build_file, *profile.build.candidate_build_files]
        )
    if max_files is not None:
        profile.paths.max_files = max_files
    if max_file_bytes is not None:
        profile.paths.max_file_bytes = max_file_bytes

    validate_profile_paths(project_root, profile.paths)
    path.write_text(profile_to_yaml(profile), encoding="utf-8")
    return path, profile, True


def merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _normalize_project_paths(project_root: Path, values: list[str], label: str) -> list[str]:
    return _unique([_normalize_project_path(project_root, value, label) for value in values])


def _normalize_project_path(project_root: Path, value: str, label: str) -> str:
    raw = str(value).strip()
    if not raw:
        raise ValueError(f"{label} cannot be empty")
    try:
        resolved = resolve_project_path(project_root, raw)
    except ValueError as exc:
        raise ValueError(f"{label} must stay under project root: {value}") from exc
    relative = resolved.relative_to(project_root.resolve())
    return "." if not relative.parts else relative.as_posix()


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
