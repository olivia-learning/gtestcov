from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from .evidence_backend import BulkSymbolScanBackend
from .evidence_types import EvidenceHit, EvidenceQuery
from .file_index import index_build, load_file_index
from .fs import read_text, resolve_project_path
from .models import ProjectProfile, relpath
from .profile import load_profile


SEMANTIC_BACKENDS = ("serena", "clangd", "ccls")
SEMANTIC_INTEGRATION_LEVEL = "optional_poc_fallback"
CONTROL_KEYWORDS = {"if", "for", "while", "switch", "catch", "return"}
CLASS_RE = re.compile(r"^\s*(?P<kind>class|struct|enum(?:\s+class)?)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b")
FUNCTION_RE = re.compile(
    r"^\s*(?:[A-Za-z_][A-Za-z0-9_:<>,~*&\s]+\s+)"
    r"(?P<name>[A-Za-z_~][A-Za-z0-9_:~]*)\s*\([^;]*\)\s*(?:const\s*)?(?:\{|$)"
)


class SemanticOverviewBackend:
    name = "local_semantic_overview"

    def collect(self, project_root: Path, query: EvidenceQuery, profile: ProjectProfile) -> list[EvidenceHit]:
        if not query.target:
            return []
        result = _overview_with_profile(project_root.resolve(), profile, query.target, query.limit, backend="auto")
        return [EvidenceHit(**hit) for hit in result["hits"]]


def semantic_doctor(project_root: Path, *, backend: str = "auto") -> dict[str, Any]:
    root = project_root.resolve()
    profile = load_profile(root)
    tools = _find_semantic_tools()
    compile_commands = _compile_commands_status(root, profile)
    selection = _select_backend(tools, backend)
    notes = []
    if not compile_commands["available"]:
        notes.append("compile_commands.json not found; semantic backend will emit only local candidate evidence.")
    if not selection["available"]:
        notes.append("Selected semantic backend is unavailable; commands will use deterministic local fallback.")
    notes.append("Serena/clangd/ccls support is optional PoC discovery; current commands emit local candidate evidence.")
    return {
        "status": "ok",
        "project_root": str(root),
        "integration_level": SEMANTIC_INTEGRATION_LEVEL,
        "external_backend_required": False,
        "external_backend_invoked": False,
        "requested_backend": backend,
        "selected_backend": selection,
        "tools": tools,
        "compile_commands": compile_commands,
        "fallback": {
            "available": True,
            "references_backend": "bulk_symbol_scan",
            "overview_backend": "local_semantic_overview",
        },
        "default_flow_changed": False,
        "notes": notes,
    }


def semantic_references(project_root: Path, symbol: str, *, limit: int = 80, backend: str = "auto") -> dict[str, Any]:
    root = project_root.resolve()
    profile = load_profile(root)
    symbol = symbol.strip()
    if not symbol:
        return {
            "status": "invalid_symbol",
            "project_root": str(root),
            "symbol": symbol,
            "integration_level": SEMANTIC_INTEGRATION_LEVEL,
            "external_backend_required": False,
            "external_backend_invoked": False,
            "hits": [],
            "error": "symbol must not be empty",
        }
    doctor = semantic_doctor(root, backend=backend)
    hits = BulkSymbolScanBackend().collect(root, EvidenceQuery(symbols=[symbol], limit=limit), profile)
    return {
        "status": "ok",
        "project_root": str(root),
        "symbol": symbol,
        "integration_level": SEMANTIC_INTEGRATION_LEVEL,
        "external_backend_required": False,
        "external_backend_invoked": False,
        "backend": "bulk_symbol_scan",
        "fallback_used": True,
        "semantic_backend_status": _semantic_backend_status(doctor),
        "compile_commands": doctor["compile_commands"],
        "selected_backend": doctor["selected_backend"],
        "hits": [hit.model_dump(mode="json") for hit in hits],
        "default_flow_changed": False,
        "notes": ["References are candidate evidence from local scan; CODRAX remains responsible for final judgment."],
    }


def semantic_overview(project_root: Path, target: str, *, limit: int = 80, backend: str = "auto") -> dict[str, Any]:
    root = project_root.resolve()
    profile = load_profile(root)
    return _overview_with_profile(root, profile, target, limit, backend=backend)


def _overview_with_profile(
    root: Path,
    profile: ProjectProfile,
    target: str,
    limit: int,
    *,
    backend: str,
) -> dict[str, Any]:
    doctor = semantic_doctor(root, backend=backend)
    try:
        target_path = resolve_project_path(root, target)
    except ValueError as exc:
        return {
            "status": "invalid_target",
            "project_root": str(root),
            "target": target,
            "integration_level": SEMANTIC_INTEGRATION_LEVEL,
            "external_backend_required": False,
            "external_backend_invoked": False,
            "hits": [],
            "error": str(exc),
            "compile_commands": doctor["compile_commands"],
            "selected_backend": doctor["selected_backend"],
        }
    if not target_path.exists() or not target_path.is_file():
        return {
            "status": "target_missing",
            "project_root": str(root),
            "target": target,
            "integration_level": SEMANTIC_INTEGRATION_LEVEL,
            "external_backend_required": False,
            "external_backend_invoked": False,
            "hits": [],
            "compile_commands": doctor["compile_commands"],
            "selected_backend": doctor["selected_backend"],
        }
    index = load_file_index(root)
    if not _index_matches_root(root, index):
        index_build(root)
    target_rel = relpath(target_path, root)
    hits = [
        EvidenceHit(
            backend="local_index",
            kind="file",
            path=target_rel,
            confidence="candidate",
            reason="target file for semantic overview",
        )
    ]
    hits.extend(_extract_overview_hits(root, target_path, target_rel, limit=max(0, limit - 1)))
    return {
        "status": "ok",
        "project_root": str(root),
        "target": target_rel,
        "integration_level": SEMANTIC_INTEGRATION_LEVEL,
        "external_backend_required": False,
        "external_backend_invoked": False,
        "backend": "local_semantic_overview",
        "fallback_used": True,
        "semantic_backend_status": _semantic_backend_status(doctor),
        "compile_commands": doctor["compile_commands"],
        "selected_backend": doctor["selected_backend"],
        "hits": [hit.model_dump(mode="json") for hit in hits[:limit]],
        "default_flow_changed": False,
        "notes": ["Overview is candidate evidence from local parsing; it does not replace CODRAX review."],
    }


def _find_semantic_tools() -> dict[str, dict[str, Any]]:
    tools: dict[str, dict[str, Any]] = {}
    for name in SEMANTIC_BACKENDS:
        command = shutil.which(name)
        tools[name] = {"available": bool(command), "command": command or ""}
    return tools


def _select_backend(tools: dict[str, dict[str, Any]], preferred: str) -> dict[str, Any]:
    if preferred != "auto":
        tool = tools.get(preferred, {"available": False, "command": ""})
        return {
            "name": preferred,
            "available": bool(tool.get("available")),
            "command": tool.get("command", ""),
            "reason": "preferred_available" if tool.get("available") else "preferred_unavailable",
        }
    for name in SEMANTIC_BACKENDS:
        tool = tools.get(name, {})
        if tool.get("available"):
            return {"name": name, "available": True, "command": tool.get("command", ""), "reason": "auto_selected"}
    return {"name": "none", "available": False, "command": "", "reason": "no_semantic_backend_available"}


def _compile_commands_status(root: Path, profile: ProjectProfile) -> dict[str, Any]:
    paths: list[str] = []
    seen: set[Path] = set()
    for raw in [".", *profile.paths.build_roots]:
        base = resolve_project_path(root, raw)
        candidates = [base] if base.name == "compile_commands.json" else [base / "compile_commands.json"]
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen or not resolved.exists() or not resolved.is_file():
                continue
            seen.add(resolved)
            paths.append(relpath(resolved, root))
    return {
        "available": bool(paths),
        "paths": paths,
        "diagnostic": "found" if paths else "compile_commands.json not found under project root/build_roots",
    }


def _semantic_backend_status(doctor: dict[str, Any]) -> str:
    if doctor["selected_backend"]["available"] and doctor["compile_commands"]["available"]:
        return "ready_not_invoked_in_poc"
    if not doctor["compile_commands"]["available"]:
        return "compile_commands_missing"
    return "backend_unavailable"


def _extract_overview_hits(root: Path, target_path: Path, target_rel: str, *, limit: int) -> list[EvidenceHit]:
    hits: list[EvidenceHit] = []
    for line_no, line in enumerate(read_text(target_path).splitlines(), start=1):
        class_match = CLASS_RE.search(line)
        if class_match:
            hits.append(
                EvidenceHit(
                    backend="local_semantic_overview",
                    kind="symbol_overview",
                    path=target_rel,
                    line=line_no,
                    symbol=class_match.group("name"),
                    excerpt=line.strip()[:300],
                    confidence="candidate",
                    reason=f"local {class_match.group('kind')} candidate",
                )
            )
        function_match = FUNCTION_RE.search(line)
        if function_match:
            name = function_match.group("name").split("::")[-1]
            if name not in CONTROL_KEYWORDS:
                hits.append(
                    EvidenceHit(
                        backend="local_semantic_overview",
                        kind="symbol_overview",
                        path=target_rel,
                        line=line_no,
                        symbol=function_match.group("name"),
                        excerpt=line.strip()[:300],
                        confidence="candidate",
                        reason="local function candidate",
                    )
                )
        if len(hits) >= limit:
            break
    return hits


def _index_matches_root(root: Path, index: dict[str, Any]) -> bool:
    return bool(index and index.get("project_root") == str(root) and isinstance(index.get("files"), dict))
