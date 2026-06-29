from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .evidence_backend import collect_evidence_hits
from .evidence_types import EvidenceQuery
from .file_index import index_build, load_file_index
from .models import CodraxEvidence, ProjectProfile, relpath
from .profile import PROFILE_NAME, load_profile, profile_to_yaml
from .run_status import utc_now


EVIDENCE_PACK_DIR = Path(".gtestcov") / "cache" / "evidence_pack"
EVIDENCE_PACK_SCHEMA_VERSION = 3
CODRAX_PAYLOAD = "codrax"


def load_codrax_payload(
    project_root: Path,
    target: str,
    operation: str,
    *,
    request_key: str = "",
) -> tuple[CodraxEvidence | None, dict[str, Any]]:
    evidence_data, meta = load_evidence_payload(
        project_root,
        target,
        operation,
        CODRAX_PAYLOAD,
        request_key=request_key,
    )
    if not isinstance(evidence_data, dict):
        return None, meta
    evidence = CodraxEvidence.model_validate(evidence_data)
    evidence.cache = meta
    return evidence, meta


def load_evidence_payload(
    project_root: Path,
    target: str,
    operation: str,
    payload_name: str,
    *,
    request_key: str = "",
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    root = project_root.resolve()
    cache_key, path, fingerprints = _cache_identity(root, target, operation, request_key=request_key)
    meta = _cache_meta(cache_key, path, fingerprints, hit=False, reason="cache_file_missing")
    meta["schema_version"] = EVIDENCE_PACK_SCHEMA_VERSION
    if not path.exists():
        return None, meta
    pack = _read_pack(path)
    if not pack:
        meta["miss_reason"] = "cache_json_invalid"
        return None, meta
    actual_schema_version = pack.get("schema_version")
    if actual_schema_version != EVIDENCE_PACK_SCHEMA_VERSION:
        meta["miss_reason"] = "unsupported_evidence_pack_schema"
        meta["expected_schema_version"] = EVIDENCE_PACK_SCHEMA_VERSION
        meta["actual_schema_version"] = actual_schema_version
        return None, meta
    if pack.get("cache_key") != meta["cache_key"]:
        meta["miss_reason"] = "cache_key_mismatch"
        return None, meta
    payloads = pack.get("payloads") if isinstance(pack.get("payloads"), dict) else {}
    evidence_data = payloads.get(payload_name) if isinstance(payloads, dict) else None
    if not isinstance(evidence_data, dict):
        meta["miss_reason"] = "payload_missing"
        meta["payload_name"] = payload_name
        return None, meta
    hit_meta = _cache_meta(meta["cache_key"], path, meta["fingerprints"], hit=True, reason="cache_file_present")
    hit_meta["schema_version"] = pack.get("schema_version")
    hit_meta["created_at"] = pack.get("created_at", "")
    hit_meta["hit_backends"] = pack.get("sources", {}).get("hit_backends", [])
    hit_meta["payload_name"] = payload_name
    hit_meta["payload_names"] = sorted(payloads.keys()) if isinstance(payloads, dict) else []
    return evidence_data, hit_meta


def store_codrax_payload(
    project_root: Path,
    target: str,
    operation: str,
    evidence: CodraxEvidence,
    *,
    request_key: str = "",
    previous_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = project_root.resolve()
    profile = load_profile(root)
    cache_key, path, fingerprints = _cache_identity(root, target, operation, request_key=request_key)
    hits = collect_evidence_hits(
        root,
        profile,
        EvidenceQuery(target=target, symbols=evidence.symbols[:20], limit=120),
        codrax_evidence=evidence,
    )
    return store_evidence_pack(
        root,
        target,
        operation,
        hits=[hit.model_dump(mode="json") for hit in hits],
        payloads={CODRAX_PAYLOAD: evidence.model_dump(mode="json")},
        request_key=request_key,
        previous_cache=previous_cache,
        fingerprints=fingerprints,
        cache_key=cache_key,
        path=path,
    )


def store_evidence_pack(
    project_root: Path,
    target: str,
    operation: str,
    *,
    hits: list[dict[str, Any]],
    payloads: dict[str, dict[str, Any]],
    request_key: str = "",
    previous_cache: dict[str, Any] | None = None,
    fingerprints: dict[str, Any] | None = None,
    cache_key: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    root = project_root.resolve()
    if fingerprints is None or cache_key is None or path is None:
        cache_key, path, fingerprints = _cache_identity(root, target, operation, request_key=request_key)
    hit_backends = sorted({str(hit.get("backend", "")) for hit in hits if hit.get("backend")})
    pack = {
        "schema_version": EVIDENCE_PACK_SCHEMA_VERSION,
        "cache_key": cache_key,
        "created_at": utc_now(),
        "metadata": {
            "operation": operation,
            "target": target,
            "request_key_hash": _sha256_text(request_key),
            "fingerprints": fingerprints,
        },
        "operation": operation,
        "target": target,
        "request_key_hash": _sha256_text(request_key),
        "fingerprints": fingerprints,
        "sources": {
            "configured": ["local_index", "bulk_symbol_scan", "zoekt", "serena_clangd_ccls", "codrax"],
            "hit_backends": hit_backends,
        },
        "hits": hits,
        "payloads": payloads,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pack, indent=2), encoding="utf-8")
    meta = _cache_meta(cache_key, path, fingerprints, hit=False, reason=(previous_cache or {}).get("miss_reason", "stored_after_miss"))
    meta["stored"] = True
    meta["schema_version"] = EVIDENCE_PACK_SCHEMA_VERSION
    meta["hit_backends"] = hit_backends
    meta["payload_names"] = sorted(payloads.keys())
    return meta


def evidence_cache_status(evidence: CodraxEvidence) -> dict[str, Any]:
    cache = evidence.cache if isinstance(evidence.cache, dict) else {}
    if not cache:
        return {"hit": False, "miss_reason": "not_checked"}
    return {
        "hit": bool(cache.get("hit")),
        "hit_reason": cache.get("hit_reason", ""),
        "miss_reason": cache.get("miss_reason", ""),
        "cache_key": cache.get("cache_key", ""),
        "path": cache.get("path", ""),
        "stored": bool(cache.get("stored")),
    }


def attach_cache(evidence: CodraxEvidence, cache: dict[str, Any]) -> CodraxEvidence:
    return evidence.model_copy(deep=True, update={"cache": cache})


def _cache_identity(
    root: Path,
    target: str,
    operation: str,
    *,
    request_key: str,
    schema_version: int = EVIDENCE_PACK_SCHEMA_VERSION,
) -> tuple[str, Path, dict[str, Any]]:
    index_build(root)
    profile = load_profile(root)
    fingerprints = {
        "profile": _profile_fingerprint(root, profile),
        "index": _index_fingerprint(root),
        "target": _target_fingerprint(root, target),
    }
    identity = {
        "schema_version": schema_version,
        "operation": operation,
        "target": target.replace("\\", "/").strip(),
        "request_key_hash": _sha256_text(request_key),
        "fingerprints": fingerprints,
    }
    cache_key = _sha256_json(identity)
    return cache_key, root / EVIDENCE_PACK_DIR / f"{cache_key}.json", fingerprints


def _cache_meta(cache_key: str, path: Path, fingerprints: dict[str, Any], *, hit: bool, reason: str) -> dict[str, Any]:
    return {
        "hit": hit,
        "hit_reason": reason if hit else "",
        "miss_reason": "" if hit else reason,
        "cache_key": cache_key,
        "path": str(path),
        "fingerprints": fingerprints,
    }


def _profile_fingerprint(root: Path, profile: ProjectProfile) -> dict[str, Any]:
    path = root / PROFILE_NAME
    if path.exists():
        content = path.read_bytes()
        source = relpath(path, root)
    else:
        content = profile_to_yaml(profile).encode("utf-8")
        source = "default_profile"
    return {"source": source, "sha256": _sha256_bytes(content)}


def _index_fingerprint(root: Path) -> dict[str, Any]:
    index = load_file_index(root)
    stable = {
        "project_root": index.get("project_root", ""),
        "scan_roots": index.get("scan_roots", []),
        "exclude_dirs": index.get("exclude_dirs", []),
        "max_files": index.get("max_files"),
        "truncated": index.get("truncated", False),
        "files": index.get("files", {}),
    }
    return {
        "file_count": index.get("file_count", 0),
        "truncated": index.get("truncated", False),
        "sha256": _sha256_json(stable),
    }


def _target_fingerprint(root: Path, target: str) -> dict[str, Any]:
    candidate = (root / target).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return {"kind": "invalid", "value": target}
    if not candidate.exists() or not candidate.is_file():
        return {"kind": "unresolved", "value": target.replace("\\", "/")}
    return {
        "kind": "file",
        "path": relpath(candidate, root),
        "size": candidate.stat().st_size,
        "mtime_ns": candidate.stat().st_mtime_ns,
        "sha256": _sha256_file(candidate),
    }


def _read_pack(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_text(content: str) -> str:
    return _sha256_bytes(content.encode("utf-8"))


def _sha256_json(value: Any) -> str:
    return _sha256_text(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
