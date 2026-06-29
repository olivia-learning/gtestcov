from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .codrax import FILE_LINE_RE
from .dependency import classify_symbols_bulk
from .evidence_types import EvidenceHit, EvidenceQuery
from .file_index import load_file_index
from .fs import read_text
from .models import CodraxEvidence, ProjectProfile


class EvidenceBackend(Protocol):
    name: str

    def collect(self, project_root: Path, query: EvidenceQuery, profile: ProjectProfile) -> list[EvidenceHit]:
        ...


class LocalIndexBackend:
    name = "local_index"

    def collect(self, project_root: Path, query: EvidenceQuery, profile: ProjectProfile) -> list[EvidenceHit]:
        index = load_file_index(project_root)
        files = index.get("files")
        if not isinstance(files, dict):
            return []
        hits: list[EvidenceHit] = []
        target = query.target.replace("\\", "/").strip()
        target_name = Path(target).name if target else ""
        for rel, record in sorted(files.items()):
            rel_text = str(rel).replace("\\", "/")
            if target and rel_text != target and Path(rel_text).name != target_name:
                continue
            hits.append(
                EvidenceHit(
                    backend=self.name,
                    kind="file",
                    path=rel_text,
                    confidence="candidate",
                    reason="line unavailable; matched target path/name from file_index",
                    excerpt=f"size={record.get('size', 0)} mtime_ns={record.get('mtime_ns', 0)}",
                )
            )
            if len(hits) >= query.limit:
                break
        return hits


class BulkSymbolScanBackend:
    name = "bulk_symbol_scan"

    def collect(self, project_root: Path, query: EvidenceQuery, profile: ProjectProfile) -> list[EvidenceHit]:
        if not query.symbols:
            return []
        hits: list[EvidenceHit] = []
        for report in classify_symbols_bulk(project_root, query.symbols, profile):
            for ref in report.locations:
                path, line = _split_file_line(ref)
                hits.append(
                    EvidenceHit(
                        backend=self.name,
                        kind="symbol",
                        path=path,
                        line=line,
                        symbol=report.symbol,
                        confidence="cited",
                        reason=report.kind,
                        excerpt=_line_excerpt(project_root, path, line),
                    )
                )
                if len(hits) >= query.limit:
                    return hits
        return hits


class CodraxEvidenceBackend:
    name = "codrax"

    def collect(self, project_root: Path, query: EvidenceQuery, profile: ProjectProfile) -> list[EvidenceHit]:
        return []

    def collect_from_evidence(self, evidence: CodraxEvidence, limit: int = 80) -> list[EvidenceHit]:
        hits: list[EvidenceHit] = []
        for ref in evidence.file_line_refs:
            path, line = _split_file_line(ref)
            hits.append(
                EvidenceHit(
                    backend=self.name,
                    kind="file_line",
                    path=path,
                    line=line,
                    confidence="cited" if evidence.status == "ok" else "candidate",
                    reason=f"codrax_status={evidence.status}",
                )
            )
            if len(hits) >= limit:
                break
        return hits


def collect_evidence_hits(
    project_root: Path,
    profile: ProjectProfile,
    query: EvidenceQuery,
    *,
    codrax_evidence: CodraxEvidence | None = None,
    backends: list[EvidenceBackend] | None = None,
) -> list[EvidenceHit]:
    active_backends: list[EvidenceBackend] = backends or [LocalIndexBackend(), BulkSymbolScanBackend()]
    hits: list[EvidenceHit] = []
    for backend in active_backends:
        hits.extend(backend.collect(project_root, query, profile))
        if len(hits) >= query.limit:
            return hits[: query.limit]
    if codrax_evidence:
        hits.extend(CodraxEvidenceBackend().collect_from_evidence(codrax_evidence, limit=query.limit - len(hits)))
    return hits[: query.limit]


def _split_file_line(ref: str) -> tuple[str, int | None]:
    match = FILE_LINE_RE.search(ref)
    if not match:
        return ref.replace("\\", "/"), None
    return match.group("path").replace("\\", "/"), int(match.group("line"))


def _line_excerpt(project_root: Path, path: str, line: int | None) -> str:
    if not line:
        return ""
    candidate = project_root / path
    if not candidate.exists() or not candidate.is_file():
        return ""
    lines = read_text(candidate).splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()[:300]
    return ""
