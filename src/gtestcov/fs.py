from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".gtestcov",
    ".cache",
    "__pycache__",
    "build",
    "cmake-build-debug",
    "cmake-build-release",
    "node_modules",
}


CPP_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}


def iter_files(root: Path, suffixes: set[str] | None = None) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if suffixes is not None and path.suffix.lower() not in suffixes:
            continue
        files.append(path)
    return files


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1", errors="ignore")


def ensure_run_dir(project_root: Path, run_id: str | None = None) -> tuple[str, Path]:
    if run_id in (None, "", "new"):
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = project_root / ".gtestcov" / "runs" / run_id
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
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_id, run_dir
