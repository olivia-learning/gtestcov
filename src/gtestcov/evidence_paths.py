from __future__ import annotations

from pathlib import Path

from .codrax import FILE_LINE_RE
from .models import CodraxEvidence


TEST_SOURCE_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}


def codrax_test_source_dirs(evidence: CodraxEvidence) -> dict[str, list[str]]:
    refs_by_dir: dict[str, list[str]] = {}
    for line in evidence.harnesses:
        for ref in _file_refs(line):
            path = _normalize_path(ref.rsplit(":", 1)[0])
            if not _looks_like_test_support_source(path):
                continue
            parent = str(Path(path).parent).replace("\\", "/")
            if not parent or parent == ".":
                continue
            refs_by_dir.setdefault(parent, []).append(ref)
    return refs_by_dir


def _looks_like_test_support_source(path: str) -> bool:
    candidate = Path(path)
    if candidate.suffix.lower() not in TEST_SOURCE_EXTENSIONS:
        return False
    parts = [part.lower() for part in candidate.parts]
    stem = candidate.stem.lower()
    return (
        any(part in {"test", "tests", "ut", "unit_test", "unit_tests"} for part in parts)
        or "test" in stem
        or "tester" in stem
        or "harness" in stem
        or "fixture" in stem
    )


def _file_refs(line: str) -> list[str]:
    refs: list[str] = []
    for match in FILE_LINE_RE.finditer(line):
        path = match.group("path").replace("\\", "/")
        refs.append(f"{path}:{match.group('line')}")
    return refs


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip().lstrip("./")
