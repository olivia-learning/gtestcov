from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .memory import SCHEMA_VERSION as MEMORY_SCHEMA_VERSION


FALLBACK_VERSION = "0.2.0"
INSTALL_MANIFEST = "gtestcov_install_manifest.json"


@dataclass(frozen=True)
class VersionInfo:
    version: str
    version_source: str
    install_path: str
    install_mode: str
    git_commit: str
    git_branch: str
    git_remote: str
    git_dirty: bool
    git_modified_count: int
    zip_manifest_version: str
    zip_manifest_source: str
    python_executable: str
    python_version: str
    platform: str
    memory_schema_version: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "version_source": self.version_source,
            "install_path": self.install_path,
            "install_mode": self.install_mode,
            "git_commit": self.git_commit,
            "git_branch": self.git_branch,
            "git_remote": self.git_remote,
            "git_dirty": self.git_dirty,
            "git_modified_count": self.git_modified_count,
            "zip_manifest_version": self.zip_manifest_version,
            "zip_manifest_source": self.zip_manifest_source,
            "python_executable": self.python_executable,
            "python_version": self.python_version,
            "platform": self.platform,
            "memory_schema_version": self.memory_schema_version,
        }


def package_version() -> str:
    return resolve_package_version(package_root())["version"]


def resolve_package_version(tool_root: Path | None = None) -> dict[str, str]:
    root = (tool_root or package_root()).resolve()
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            version = str(data.get("project", {}).get("version") or "")
            if version:
                return {"version": version, "source": "pyproject.toml"}
        except Exception:
            pass
    try:
        return {"version": importlib.metadata.version("gtestcov"), "source": "installed_package_metadata"}
    except importlib.metadata.PackageNotFoundError:
        return {"version": FALLBACK_VERSION, "source": "fallback"}


def package_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_tool_home() -> Path:
    override = os.environ.get("GTESTCOV_TOOL_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "gtestcov"


def detect_install_mode(tool_root: Path, requested: str | None = None) -> str:
    root = tool_root.resolve()
    if requested and requested != "auto":
        return requested
    if (root / INSTALL_MANIFEST).exists():
        return "zip"
    if (root / ".git").exists():
        return "git"
    return "unknown"


def load_install_manifest(tool_root: Path) -> dict[str, Any]:
    path = tool_root / INSTALL_MANIFEST
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"error": "manifest is not valid JSON", "path": str(path)}


def get_version_info(tool_root: Path | None = None, install_mode: str | None = None) -> VersionInfo:
    root = (tool_root or package_root()).resolve()
    manifest = load_install_manifest(root)
    mode = detect_install_mode(root, install_mode)
    version = resolve_package_version(root)
    git = git_identity(root) if mode == "git" or (root / ".git").exists() else {}
    status = git_status(root, include_diff=False) if mode == "git" or (root / ".git").exists() else {}
    raw_status = status.get("raw_status", []) if isinstance(status.get("raw_status", []), list) else []
    return VersionInfo(
        version=version["version"],
        version_source=version["source"],
        install_path=str(root),
        install_mode=mode,
        git_commit=str(status.get("commit") or git.get("commit", "")),
        git_branch=str(status.get("branch", "")),
        git_remote=str(git.get("remote", "")),
        git_dirty=bool(raw_status),
        git_modified_count=len(raw_status),
        zip_manifest_version=str(manifest.get("version", "")),
        zip_manifest_source=str(manifest.get("source", "")),
        python_executable=sys.executable,
        python_version=sys.version.replace("\n", " "),
        platform=platform.platform(),
        memory_schema_version=MEMORY_SCHEMA_VERSION,
    )


def git_available() -> bool:
    return shutil.which("git") is not None


def git_identity(tool_root: Path) -> dict[str, Any]:
    if not git_available():
        return {"available": False}
    commit = _git(["rev-parse", "HEAD"], tool_root)
    remote = _git(["remote", "get-url", "origin"], tool_root)
    return {
        "available": True,
        "commit": commit.get("stdout", "").strip() if commit.get("returncode") == 0 else "",
        "remote": remote.get("stdout", "").strip() if remote.get("returncode") == 0 else "",
    }


def git_status(tool_root: Path, include_diff: bool = True) -> dict[str, Any]:
    if not git_available():
        return {"available": False, "error": "git is not on PATH"}
    status = _git(["status", "--porcelain=v1", "--untracked-files=all"], tool_root)
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], tool_root)
    commit = _git(["rev-parse", "HEAD"], tool_root)
    diff = _git(["diff", "--binary"], tool_root) if include_diff else {"returncode": 0, "stdout": "", "stderr": ""}
    lines = status.get("stdout", "").splitlines() if status.get("returncode") == 0 else []
    return {
        "available": True,
        "returncode": status.get("returncode"),
        "branch": branch.get("stdout", "").strip() if branch.get("returncode") == 0 else "",
        "commit": commit.get("stdout", "").strip() if commit.get("returncode") == 0 else "",
        "modified_files": _parse_git_status(lines, {"M", "A", "D", "R", "C", "U"}),
        "untracked_files": _parse_git_status(lines, {"??"}),
        "raw_status": lines,
        "diff": diff.get("stdout", "") if diff.get("returncode") == 0 else "",
        "stderr": status.get("stderr", ""),
    }


def _git(args: list[str], cwd: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={cwd.resolve()}", *args],
            cwd=str(cwd),
            check=False,
            text=True,
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": 127, "stdout": "", "stderr": str(exc)}
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _parse_git_status(lines: list[str], statuses: set[str]) -> list[str]:
    result: list[str] = []
    for line in lines:
        if not line:
            continue
        status = line[:2].strip() or line[:2]
        path = line[3:] if len(line) > 3 else ""
        if status in statuses or any(ch in statuses for ch in status):
            result.append(path)
    return result
