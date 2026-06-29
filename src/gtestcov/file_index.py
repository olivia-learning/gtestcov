from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from .fs import CPP_SUFFIXES, read_text, scan_files, scan_roots_from_profile
from .models import relpath
from .profile import load_profile
from .run_status import utc_now


FILE_INDEX_PATH = Path(".gtestcov") / "cache" / "file_index.json"


def index_build(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    profile = load_profile(root)
    previous = _read_index(root)
    scan = scan_files(
        root,
        scan_roots=scan_roots_from_profile(profile),
        exclude_dirs=profile.paths.exclude_dirs,
        max_files=profile.paths.max_files,
    )
    previous_files = previous.get("files", {}) if isinstance(previous.get("files"), dict) else {}
    files: dict[str, dict[str, Any]] = {}
    changed: list[str] = []
    unchanged: list[str] = []
    for path in scan["files"]:
        rel = relpath(path, root)
        stat = path.stat()
        record = {
            "path": rel,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "suffix": path.suffix.lower(),
            "is_cpp": path.suffix.lower() in CPP_SUFFIXES,
            "contains_gtest_or_gmock": _contains_gtest_or_gmock(path, stat.st_size, profile.paths.max_file_bytes),
            "is_build_config": _is_build_config(path, rel),
            "is_test_config": _is_test_config(path, rel),
        }
        files[rel] = record
        old = previous_files.get(rel)
        if old and old.get("size") == record["size"] and old.get("mtime_ns") == record["mtime_ns"]:
            unchanged.append(rel)
        else:
            changed.append(rel)
    removed = sorted(set(previous_files) - set(files))
    index = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "project_root": str(root),
        "scan_roots": scan.get("scan_roots", []),
        "exclude_dirs": scan.get("exclude_dirs", []),
        "max_files": scan.get("max_files"),
        "truncated": scan.get("truncated", False),
        "file_count": len(files),
        "changed_count": len(changed),
        "unchanged_count": len(unchanged),
        "removed_count": len(removed),
        "changed_files": changed[:200],
        "removed_files": removed[:200],
        "files": dict(sorted(files.items())),
    }
    path = _index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return {"status": "built", "index_path": str(path), **_status_from_index(root, index, reason="fresh_build")}


def index_refresh(project_root: Path) -> dict[str, Any]:
    return index_build(project_root)


def index_status(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    index = _read_index(root)
    if not index:
        return {
            "status": "missing",
            "index_path": str(_index_path(root)),
            "hit": False,
            "miss_reason": "file_index_missing",
        }
    return {"status": "ready", "index_path": str(_index_path(root)), **_status_from_index(root, index, reason="index_present")}


def load_file_index(project_root: Path) -> dict[str, Any]:
    return _read_index(project_root.resolve())


def _status_from_index(root: Path, index: dict[str, Any], *, reason: str) -> dict[str, Any]:
    project_matches = index.get("project_root") == str(root)
    if not project_matches:
        return {
            "hit": False,
            "miss_reason": "project_root_mismatch",
            "file_count": index.get("file_count", 0),
            "truncated": index.get("truncated", False),
            "generated_at": index.get("generated_at", ""),
        }
    return {
        "hit": True,
        "hit_reason": reason,
        "miss_reason": "",
        "file_count": index.get("file_count", 0),
        "changed_count": index.get("changed_count", 0),
        "unchanged_count": index.get("unchanged_count", 0),
        "removed_count": index.get("removed_count", 0),
        "truncated": index.get("truncated", False),
        "generated_at": index.get("generated_at", ""),
    }


def _index_path(root: Path) -> Path:
    return root / FILE_INDEX_PATH


def _read_index(root: Path) -> dict[str, Any]:
    path = _index_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _contains_gtest_or_gmock(path: Path, size: int, max_file_bytes: int) -> bool:
    if max_file_bytes and size > max_file_bytes:
        return False
    text = read_text(path)
    return bool(re.search(r"#\s*include\s*[<\"].*(gtest|gmock)", text) or re.search(r"\b(TEST|TEST_F|TEST_P|MOCK_METHOD)\s*\(", text))


def _is_build_config(path: Path, rel: str) -> bool:
    name = path.name
    suffix = path.suffix.lower()
    normalized = rel.replace("\\", "/").lower()
    return (
        name in {"CMakeLists.txt", "Makefile", "BUILD", "BUILD.bazel", "WORKSPACE"}
        or suffix in {".cmake", ".mk", ".bazel"}
        or normalized.endswith(("build.gradle", "build.gradle.kts"))
    )


def _is_test_config(path: Path, rel: str) -> bool:
    normalized = rel.replace("\\", "/").lower()
    return _is_build_config(path, rel) and ("test" in normalized or "gtest" in normalized or "googletest" in normalized)
