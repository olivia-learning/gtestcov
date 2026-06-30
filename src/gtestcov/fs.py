from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any


SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".gtestcov",
    ".cache",
    ".repo",
    "__pycache__",
    "build",
    "cmake-build-debug",
    "cmake-build-release",
    "node_modules",
    "out",
    "third_party",
    "vendor",
    "prebuilts",
}


CPP_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def iter_files(
    root: Path,
    suffixes: set[str] | None = None,
    *,
    scan_roots: list[str] | None = None,
    exclude_dirs: list[str] | None = None,
    max_files: int | None = None,
) -> list[Path]:
    return scan_files(
        root,
        suffixes,
        scan_roots=scan_roots,
        exclude_dirs=exclude_dirs,
        max_files=max_files,
    )["files"]


def scan_files(
    root: Path,
    suffixes: set[str] | None = None,
    *,
    scan_roots: list[str] | None = None,
    exclude_dirs: list[str] | None = None,
    max_files: int | None = None,
    progress_every: int = 500,
) -> dict[str, Any]:
    files: list[Path] = []
    seen: set[Path] = set()
    scanned = 0
    skipped_excluded = 0
    progress: list[dict[str, Any]] = []
    truncated = False
    root = root.resolve()
    active_roots = scan_roots or ["."]
    active_excludes = set(SKIP_DIRS)
    active_excludes.update(exclude_dirs or [])
    for raw_scan_root in active_roots:
        base = resolve_project_path(root, raw_scan_root)
        if not base.exists():
            continue
        candidates = [base] if base.is_file() else base.rglob("*")
        for path in candidates:
            resolved = path.resolve()
            try:
                rel_parts = resolved.relative_to(root).parts
            except ValueError:
                continue
            scanned += 1
            if progress_every > 0 and scanned % progress_every == 0:
                progress.append({"scanned": scanned, "matched": len(files), "scan_root": raw_scan_root})
            if any(part in active_excludes for part in rel_parts):
                skipped_excluded += 1
                continue
            if not resolved.is_file():
                continue
            if suffixes is not None and resolved.suffix.lower() not in suffixes:
                continue
            if resolved in seen:
                continue
            files.append(resolved)
            seen.add(resolved)
            if max_files and len(files) >= max_files:
                truncated = True
                return {
                    "files": files,
                    "scanned": scanned,
                    "matched": len(files),
                    "truncated": truncated,
                    "max_files": max_files,
                    "scan_roots": active_roots,
                    "exclude_dirs": sorted(active_excludes),
                    "skipped_excluded": skipped_excluded,
                    "progress": progress,
                }
    return {
        "files": files,
        "scanned": scanned,
        "matched": len(files),
        "truncated": truncated,
        "max_files": max_files,
        "scan_roots": active_roots,
        "exclude_dirs": sorted(active_excludes),
        "skipped_excluded": skipped_excluded,
        "progress": progress,
    }


def scan_roots_from_profile(profile) -> list[str]:
    roots: list[str] = []
    for value in [*profile.paths.source_roots, *profile.paths.test_roots, *profile.paths.build_roots]:
        if value not in roots:
            roots.append(value)
    return roots or ["."]


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1", errors="ignore")


def sanitize_run_id(run_id: str | None = None) -> str:
    if run_id in (None, "", "new"):
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw = str(run_id).strip()
    if not raw or raw == "new":
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if (
        "/" in raw
        or "\\" in raw
        or ":" in raw
        or ".." in raw
        or Path(raw).is_absolute()
        or not RUN_ID_RE.fullmatch(raw)
        or not raw.strip("._-")
    ):
        raise ValueError(f"unsafe run_id: {run_id}")
    return raw[:80]


def resolve_project_path(project_root: Path, value: str | Path) -> Path:
    root = project_root.resolve()
    candidate = Path(value)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes project root: {value}") from exc
    return resolved


def validate_profile_paths(project_root: Path, paths_config) -> None:
    for field_name in ("source_roots", "test_roots", "build_roots"):
        for value in getattr(paths_config, field_name, []) or []:
            resolve_project_path(project_root, value)


def ensure_run_dir(project_root: Path, run_id: str | None = None) -> tuple[str, Path]:
    run_id = sanitize_run_id(run_id)
    runs_root = (project_root / ".gtestcov" / "runs").resolve()
    run_dir = (runs_root / run_id).resolve()
    run_dir.relative_to(runs_root)
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_id, run_dir


def resolve_run_dir(project_root: Path, run_id: str) -> tuple[str, Path]:
    runs_root = project_root / ".gtestcov" / "runs"
    if run_id == "latest":
        candidates = sorted([p for p in runs_root.glob("*") if p.is_dir()])
        if not candidates:
            run_id, run_dir = ensure_run_dir(project_root)
            return run_id, run_dir
        latest = candidates[-1]
        return latest.name, latest
    run_id = sanitize_run_id(run_id)
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_id, run_dir
