from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .evidence_types import EvidenceHit, EvidenceQuery
from .file_index import index_build, index_status, load_file_index
from .fs import read_text, resolve_project_path
from .models import ProjectProfile
from .profile import load_profile


ZOEKT_INDEX_DIR = Path(".gtestcov") / "cache" / "zoekt"
ZOEKT_INDEX_COMMANDS = ("zoekt-index",)
ZOEKT_QUERY_COMMANDS = ("zoekt-grep", "zoekt")
SEARCH_INTEGRATION_LEVEL = "optional_poc_fallback"


class ZoektSearchBackend:
    name = "zoekt"

    def collect(self, project_root: Path, query: EvidenceQuery, profile: ProjectProfile) -> list[EvidenceHit]:
        search_text = query.symbols[0] if query.symbols else query.target
        if not search_text:
            return []
        result = _search_with_profile(project_root.resolve(), profile, search_text, query.limit, regex=False)
        return [EvidenceHit(**hit) for hit in result["hits"]]


def search_doctor(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    load_profile(root)
    tools = _find_zoekt_tools()
    fallback = index_status(root)
    zoekt_available = bool(tools["index_command"] and tools["query_command"])
    notes = []
    if not zoekt_available:
        notes.append("Zoekt is unavailable; search commands will use local file_index fallback.")
    notes.append("Zoekt support is an optional PoC; local file_index remains the safe fallback and default flow is unchanged.")
    return {
        "status": "ok",
        "project_root": str(root),
        "integration_level": SEARCH_INTEGRATION_LEVEL,
        "external_backend_required": False,
        "zoekt": {
            "available": zoekt_available,
            "diagnosis": "available" if zoekt_available else "unavailable",
            "integration_status": "optional_poc_detected" if zoekt_available else "optional_poc_unavailable",
            "index_command": tools["index_command"] or "",
            "query_command": tools["query_command"] or "",
            "index_dir": str(root / ZOEKT_INDEX_DIR),
        },
        "fallback": {
            "available": True,
            "backend": "local_index",
            "index_status": fallback["status"],
            "index_path": fallback["index_path"],
            "hit": fallback.get("hit", False),
        },
        "default_flow_changed": False,
        "notes": notes,
    }


def search_index(project_root: Path, *, timeout_seconds: int = 60) -> dict[str, Any]:
    root = project_root.resolve()
    load_profile(root)
    tools = _find_zoekt_tools()
    local_index = index_build(root)
    zoekt = _run_zoekt_index(root, tools["index_command"], timeout_seconds=timeout_seconds)
    status = "indexed" if zoekt["status"] == "ok" else "fallback_indexed"
    return {
        "status": status,
        "project_root": str(root),
        "integration_level": SEARCH_INTEGRATION_LEVEL,
        "external_backend_required": False,
        "local_index": local_index,
        "zoekt": zoekt,
        "fallback_available": True,
        "default_flow_changed": False,
        "notes": ["Zoekt indexing is optional PoC; local file_index is always built as fallback."],
    }


def search_query(project_root: Path, query: str, *, limit: int = 80, regex: bool = False) -> dict[str, Any]:
    root = project_root.resolve()
    profile = load_profile(root)
    return _search_with_profile(root, profile, query, limit, regex=regex)


def _search_with_profile(root: Path, profile: ProjectProfile, query: str, limit: int, *, regex: bool) -> dict[str, Any]:
    query = query.strip()
    if not query:
        return {
            "status": "invalid_query",
            "project_root": str(root),
            "query": query,
            "regex": regex,
            "integration_level": SEARCH_INTEGRATION_LEVEL,
            "external_backend_required": False,
            "backend": "",
            "fallback_used": False,
            "hits": [],
            "error": "query must not be empty",
        }
    tools = _find_zoekt_tools()
    zoekt = _run_zoekt_query(root, tools["query_command"], query, limit=limit, regex=regex)
    if zoekt["status"] == "ok":
        return {
            "status": "ok",
            "project_root": str(root),
            "query": query,
            "regex": regex,
            "integration_level": SEARCH_INTEGRATION_LEVEL,
            "external_backend_required": False,
            "backend": "zoekt",
            "fallback_used": False,
            "hits": [hit.model_dump(mode="json") for hit in zoekt["hits"]],
            "zoekt": _public_zoekt_query_result(zoekt),
            "default_flow_changed": False,
            "notes": ["Zoekt result is optional PoC evidence; it does not make Zoekt a required default dependency."],
        }

    index = load_file_index(root)
    built_index = False
    if not _index_matches_root(root, index):
        index_build(root)
        index = load_file_index(root)
        built_index = True
    hits, stats = _local_text_search(root, profile, index, query, limit=limit, regex=regex)
    return {
        "status": "ok",
        "project_root": str(root),
        "query": query,
        "regex": regex,
        "integration_level": SEARCH_INTEGRATION_LEVEL,
        "external_backend_required": False,
        "backend": "local_index",
        "fallback_used": True,
        "fallback_reason": zoekt["status"],
        "hits": [hit.model_dump(mode="json") for hit in hits],
        "local_index": {
            "built": built_index,
            "index_path": str(root / ".gtestcov" / "cache" / "file_index.json"),
            "file_count": index.get("file_count", 0),
            "truncated": index.get("truncated", False),
        },
        "zoekt": _public_zoekt_query_result(zoekt),
        "stats": stats,
        "default_flow_changed": False,
        "notes": ["Using local_index fallback; Zoekt is optional PoC evidence only."],
    }


def _find_zoekt_tools() -> dict[str, str | None]:
    return {
        "index_command": _first_available(ZOEKT_INDEX_COMMANDS),
        "query_command": _first_available(ZOEKT_QUERY_COMMANDS),
    }


def _first_available(candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _run_zoekt_index(root: Path, command: str | None, *, timeout_seconds: int) -> dict[str, Any]:
    if not command:
        return {
            "available": False,
            "status": "unavailable",
            "reason": "zoekt-index not found",
            "index_dir": str(root / ZOEKT_INDEX_DIR),
        }
    index_dir = root / ZOEKT_INDEX_DIR
    index_dir.mkdir(parents=True, exist_ok=True)
    cmd = [command, "-index_dir", str(index_dir), str(root)]
    try:
        completed = subprocess.run(
            cmd,
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": True,
            "status": "error",
            "reason": str(exc),
            "command": cmd,
            "index_dir": str(index_dir),
        }
    return {
        "available": True,
        "status": "ok" if completed.returncode == 0 else "error",
        "returncode": completed.returncode,
        "command": cmd,
        "index_dir": str(index_dir),
        "stdout_tail": completed.stdout[-1000:],
        "stderr_tail": completed.stderr[-1000:],
    }


def _run_zoekt_query(root: Path, command: str | None, query: str, *, limit: int, regex: bool) -> dict[str, Any]:
    if not command:
        return {"available": False, "status": "unavailable", "reason": "zoekt query command not found", "hits": []}
    index_dir = root / ZOEKT_INDEX_DIR
    if not index_dir.exists():
        return {"available": True, "status": "index_missing", "reason": "Zoekt index directory is missing", "hits": []}
    cmd = [command, "-index_dir", str(index_dir), query]
    if regex:
        cmd = [command, "-index_dir", str(index_dir), "-regex", query]
    try:
        completed = subprocess.run(cmd, cwd=root, text=True, capture_output=True, check=False, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": True, "status": "error", "reason": str(exc), "command": cmd, "hits": []}
    if completed.returncode not in (0, 1):
        return {
            "available": True,
            "status": "error",
            "returncode": completed.returncode,
            "command": cmd,
            "stderr_tail": completed.stderr[-1000:],
            "hits": [],
        }
    return {
        "available": True,
        "status": "ok",
        "returncode": completed.returncode,
        "command": cmd,
        "hits": _parse_zoekt_grep_output(completed.stdout, root, query, limit=limit),
        "stderr_tail": completed.stderr[-1000:],
    }


def _parse_zoekt_grep_output(output: str, root: Path, query: str, *, limit: int) -> list[EvidenceHit]:
    hits: list[EvidenceHit] = []
    for raw_line in output.splitlines():
        path, line, excerpt = _parse_grep_line(raw_line)
        if not path:
            continue
        hits.append(
            EvidenceHit(
                backend="zoekt",
                kind="text_search",
                path=_normalize_search_path(root, path),
                line=line,
                symbol=query if _looks_like_symbol(query) else "",
                excerpt=excerpt.strip()[:300],
                confidence="candidate",
                reason="zoekt search result",
            )
        )
        if len(hits) >= limit:
            break
    return hits


def _parse_grep_line(line: str) -> tuple[str, int | None, str]:
    parts = line.rsplit(":", 2)
    if len(parts) == 3 and parts[1].isdigit():
        return parts[0], int(parts[1]), parts[2]
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0], int(parts[1]), ""
    return line.strip(), None, ""


def _local_text_search(
    root: Path,
    profile: ProjectProfile,
    index: dict[str, Any],
    query: str,
    *,
    limit: int,
    regex: bool,
) -> tuple[list[EvidenceHit], dict[str, Any]]:
    files = index.get("files") if isinstance(index.get("files"), dict) else {}
    max_file_bytes = profile.paths.max_file_bytes
    searched_files = 0
    skipped_large_files = 0
    pattern = _compile_pattern(query, regex)
    if isinstance(pattern, str):
        return [], {"searched_files": 0, "skipped_large_files": 0, "error": pattern}
    hits: list[EvidenceHit] = []
    for rel, record in sorted(files.items()):
        rel_text = str(rel).replace("\\", "/")
        size = int(record.get("size", 0) or 0) if isinstance(record, dict) else 0
        if max_file_bytes and size > max_file_bytes:
            skipped_large_files += 1
            continue
        path = resolve_project_path(root, rel_text)
        if not path.exists() or not path.is_file():
            continue
        searched_files += 1
        text = read_text(path)
        for line_no, line in enumerate(text.splitlines(), start=1):
            if _line_matches(line, query, pattern, regex):
                hits.append(
                    EvidenceHit(
                        backend="local_index",
                        kind="text_search",
                        path=rel_text,
                        line=line_no,
                        symbol=query if _looks_like_symbol(query) else "",
                        excerpt=line.strip()[:300],
                        confidence="candidate",
                        reason="local file_index fallback search",
                    )
                )
                if len(hits) >= limit:
                    return hits, {"searched_files": searched_files, "skipped_large_files": skipped_large_files}
    return hits, {"searched_files": searched_files, "skipped_large_files": skipped_large_files}


def _compile_pattern(query: str, regex: bool) -> re.Pattern[str] | str | None:
    if not regex:
        return None
    try:
        return re.compile(query)
    except re.error as exc:
        return f"invalid_regex: {exc}"


def _line_matches(line: str, query: str, pattern: re.Pattern[str] | None, regex: bool) -> bool:
    if regex:
        return bool(pattern and pattern.search(line))
    return query in line


def _index_matches_root(root: Path, index: dict[str, Any]) -> bool:
    return bool(index and index.get("project_root") == str(root) and isinstance(index.get("files"), dict))


def _normalize_search_path(root: Path, value: str) -> str:
    path = Path(value)
    try:
        resolved = (path if path.is_absolute() else root / path).resolve()
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return value.replace("\\", "/")


def _looks_like_symbol(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:~]*", value))


def _public_zoekt_query_result(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "hits"}
