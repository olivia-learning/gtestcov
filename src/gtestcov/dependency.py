from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .file_index import load_file_index
from .fs import CPP_SUFFIXES, iter_files, read_text
from .models import DependencyEntry, DependencyReport, SymbolReport, relpath
from .profile import ProjectProfile


def parse_dependency_xml(project_root: Path, profile: ProjectProfile) -> DependencyReport:
    if not profile.dependency.manifest:
        return DependencyReport(manifest_path="")
    manifest = project_root / profile.dependency.manifest
    if not manifest.exists():
        return DependencyReport(manifest_path="")

    try:
        tree = ET.parse(manifest)
    except ET.ParseError:
        return DependencyReport(manifest_path=relpath(manifest, project_root))

    entries: list[DependencyEntry] = []
    for element in tree.getroot().iter():
        attrs = {key.lower(): value for key, value in element.attrib.items()}
        name = attrs.get("name") or attrs.get("package") or attrs.get("component") or attrs.get("id")
        if not name:
            continue
        local_path = (
            attrs.get("path")
            or attrs.get("local_path")
            or attrs.get("localpath")
            or attrs.get("dir")
            or _guess_dependency_path(project_root, profile, name)
        )
        version = attrs.get("version") or attrs.get("rev") or attrs.get("tag") or attrs.get("commit") or ""
        include_paths, source_paths, library_paths = _infer_dep_paths(project_root, local_path)
        entries.append(
            DependencyEntry(
                name=name,
                version=version,
                local_path=local_path,
                include_paths=include_paths,
                source_paths=source_paths,
                library_paths=library_paths,
                host_build=_infer_host_build(project_root / local_path),
                test_treatment=_infer_treatment(name, local_path),
            )
        )

    deduped: dict[tuple[str, str], DependencyEntry] = {}
    for entry in entries:
        deduped[(entry.name, entry.local_path)] = entry
    return DependencyReport(manifest_path=relpath(manifest, project_root), dependencies=list(deduped.values()))


def _guess_dependency_path(project_root: Path, profile: ProjectProfile, name: str) -> str:
    for root_name in profile.dependency.dependency_root:
        candidate = project_root / root_name / name
        if candidate.exists():
            return relpath(candidate, project_root)
        candidate = project_root / root_name / name.lower()
        if candidate.exists():
            return relpath(candidate, project_root)
    return ""


def _infer_dep_paths(project_root: Path, local_path: str) -> tuple[list[str], list[str], list[str]]:
    if not local_path:
        return [], [], []
    base = project_root / local_path
    includes: list[str] = []
    sources: list[str] = []
    libraries: list[str] = []
    if not base.exists():
        return [], [], []
    for path in base.rglob("*"):
        if path.is_dir() and path.name.lower() in {"include", "inc"}:
            includes.append(relpath(path, project_root))
        if path.is_dir() and path.name.lower() in {"src", "source"}:
            sources.append(relpath(path, project_root))
        if path.is_dir() and path.name.lower() in {"lib", "libs"}:
            libraries.append(relpath(path, project_root))
    return sorted(set(includes)), sorted(set(sources)), sorted(set(libraries))


def _infer_host_build(path: Path) -> str:
    if not path.exists():
        return "unknown"
    names = {p.name for p in path.iterdir()} if path.is_dir() else set()
    if "CMakeLists.txt" in names or "Makefile" in names:
        return "partial"
    return "unknown"


def _infer_treatment(name: str, local_path: str) -> str:
    lower = f"{name} {local_path}".lower()
    if any(token in lower for token in ["osal", "rtos", "queue", "timer"]):
        return "fake for host gtest"
    if any(token in lower for token in ["hal", "driver", "register"]):
        return "HAL fake or HIL"
    if "crc" in lower or "checksum" in lower:
        return "use real implementation"
    return "inspect"


def classify_symbol(project_root: Path, symbol: str, profile: ProjectProfile) -> SymbolReport:
    reports = classify_symbols_bulk(project_root, [symbol], profile)
    return reports[0] if reports else SymbolReport(symbol=symbol, kind="unknown", recommendation=_symbol_recommendation(symbol, "unknown"))


def classify_symbols_bulk(project_root: Path, symbols: list[str], profile: ProjectProfile) -> list[SymbolReport]:
    unique_symbols = list(dict.fromkeys(symbol for symbol in symbols if symbol))
    if not unique_symbols:
        return []
    reports = {
        symbol: SymbolReport(symbol=symbol, kind="unknown", locations=[], recommendation=_symbol_recommendation(symbol, "unknown"))
        for symbol in unique_symbols
    }
    roots = [project_root]
    for dep_root in profile.dependency.dependency_root:
        path = project_root / dep_root
        if path.exists():
            roots.insert(0, path)

    for root in roots:
        for path in _candidate_symbol_files(project_root, root, profile):
            text = read_text(path)
            present = [symbol for symbol in unique_symbols if symbol in text]
            if not present:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                line_symbols = [symbol for symbol in present if symbol in line]
                if not line_symbols:
                    continue
                stripped = line.strip()
                for symbol in line_symbols:
                    report = reports[symbol]
                    report.locations.append(f"{relpath(path, project_root)}:{line_number}")
                    report.kind = _classify_symbol_line(symbol, stripped, report.kind)
    result: list[SymbolReport] = []
    for symbol in unique_symbols:
        report = reports[symbol]
        report.locations = sorted(set(report.locations))
        report.recommendation = _symbol_recommendation(symbol, report.kind)
        result.append(report)
    return result


def _candidate_symbol_files(project_root: Path, root: Path, profile: ProjectProfile) -> list[Path]:
    if root == project_root:
        index = load_file_index(project_root)
        if index.get("project_root") == str(project_root.resolve()) and isinstance(index.get("files"), dict):
            files = []
            for rel, record in index["files"].items():
                if str(record.get("suffix", "")).lower() in CPP_SUFFIXES:
                    path = project_root / rel
                    if path.exists() and path.is_file() and _within_source_roots(project_root, path, profile):
                        files.append(path)
            return files
        return iter_files(
            root,
            CPP_SUFFIXES,
            scan_roots=profile.paths.source_roots,
            exclude_dirs=profile.paths.exclude_dirs,
            max_files=profile.paths.max_files,
        )
    return iter_files(root, CPP_SUFFIXES, exclude_dirs=profile.paths.exclude_dirs, max_files=profile.paths.max_files)


def _within_source_roots(project_root: Path, path: Path, profile: ProjectProfile) -> bool:
    resolved = path.resolve()
    for raw_root in profile.paths.source_roots:
        try:
            resolved.relative_to((project_root / raw_root).resolve())
            return True
        except ValueError:
            continue
    return False


def _classify_symbol_line(symbol: str, stripped: str, current_kind: str) -> str:
    if re.search(rf"#\s*define\s+{re.escape(symbol)}\b", stripped):
        return "macro"
    if "static inline" in stripped and symbol in stripped:
        return "static inline"
    if "__attribute__((weak))" in stripped or "__weak" in stripped or " weak " in f" {stripped} ":
        return "weak"
    if re.search(rf"\bextern\b.*\b{re.escape(symbol)}\b", stripped):
        return "extern function"
    if re.search(rf"\b{re.escape(symbol)}\s*\([^;]*\)\s*;", stripped):
        return "extern function"
    return current_kind


def _symbol_recommendation(symbol: str, kind: str) -> str:
    if kind == "macro":
        return "Use the real header; do not link-wrap this macro."
    if kind == "static inline":
        return "Use the real header; add an upper seam if behavior must be controlled."
    if kind == "weak":
        return "A test target may provide a strong override when needed."
    if kind == "extern function":
        return "Use real implementation if host-linkable, otherwise fake/shim at external boundary."
    return "Locate the real declaration before generating tests."


def classify_default_symbols(project_root: Path, profile: ProjectProfile, extra: list[str] | None = None) -> list[SymbolReport]:
    symbols = list(dict.fromkeys(list(profile.embedded_policy.memory_api.keys()) + (extra or [])))
    return classify_symbols_bulk(project_root, symbols, profile)
