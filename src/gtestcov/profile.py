from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .fs import validate_profile_paths
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


def merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result
