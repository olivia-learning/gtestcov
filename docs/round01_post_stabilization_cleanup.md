# Round 01 Post-Stabilization Cleanup

Date: 2026-06-30

## Summary

This cleanup makes evidence pack cache reading development-stage and v3-only.

Evidence pack schema v3 is now the only accepted cache schema. Old or half-finished evidence pack cache files are treated as cache misses and are not migrated, interpreted, or converted on read. This is intentional because `gtestcov` is still a single-user development-stage tool and does not need to preserve compatibility with temporary cache formats.

The current CODRAX payload key is:

```json
{
  "schema_version": 3,
  "payloads": {
    "codrax": {}
  }
}
```

The old cache layouts are invalid for cache hits:

- `payload.codrax_evidence`
- top-level `codrax_evidence`
- `schema_version` missing
- `schema_version` not equal to `3`
- `payloads.legacy_codrax`

Old cache files are not deleted automatically. They are ignored as misses and can be rebuilt as v3 by the next run.

## Scope

No new external tool integration was performed in this cleanup.

Zoekt and semantic backends remain optional PoC/fallback paths. This round did not add real Zoekt installation validation, real Serena/clangd/ccls invocation, detached evidence cancel commands, Windows process-tree cleanup commands, or build/test/coverage semantic changes.

## Code Changes

- Removed evidence pack schema-2 fallback from cache reading.
- Removed the legacy schema constant.
- Renamed the current CODRAX cache payload from `legacy_codrax` to `codrax`.
- Renamed internal helper functions to `load_codrax_payload` and `store_codrax_payload`.
- Removed the old compatibility aliases `load_codrax_evidence_pack` and `store_codrax_evidence_pack`.
- Removed cache-hit metadata that only existed for legacy fallback, including `legacy_schema` and `checked_schema_versions`.

## Tests

The schema-2 compatibility test was replaced with a test that asserts old and incomplete cache formats miss:

- missing `schema_version`
- `schema_version != 3`
- old `payload.codrax_evidence`
- old top-level `codrax_evidence`

The analyze and cover cache-hit tests continue to verify that current v3 cache behavior works.

## Historical Baggage Inventory

| Item | Classification | Decision |
| --- | --- | --- |
| evidence pack schema-2 fallback | removed_now | Removed from cache reads. |
| `LEGACY_EVIDENCE_PACK_SCHEMA_VERSION` | removed_now | Removed. |
| old `payload.codrax_evidence` cache reader | removed_now | Old payload is now a cache miss. |
| top-level `codrax_evidence` cache reader | removed_now | No current reader exists; top-level payload is a cache miss. |
| test expecting schema-2 compatibility | removed_now | Replaced with old-format miss coverage. |
| `load_codrax_evidence_pack` / `store_codrax_evidence_pack` | removed_now | Removed old compatibility aliases. |
| `legacy_schema` cache-hit metadata | removed_now | Removed. |
| `checked_schema_versions` metadata | removed_now | Removed with legacy fallback. |
| `LEGACY_CODRAX_PAYLOAD = "legacy_codrax"` | removed_now | Replaced by `CODRAX_PAYLOAD = "codrax"`. |
| `load_legacy_codrax_payload` / `store_legacy_codrax_payload` | removed_now | Replaced by clean CODRAX payload helpers. |
| `codrax_evidence.json` and `codrax_evidence.md` run artifacts | not_baggage | These are normal human-readable CODRAX evidence artifacts. |
| `CodraxEvidence` model and analysis fields named `codrax_evidence` | not_baggage | These represent actual CODRAX domain evidence, not cache compatibility. |
| docs about legacy C++ behavior | not_baggage | They describe production-code testing strategy, not cache/API compatibility. |
| `CodraxEvidenceConfig.timeout_seconds = 180` | follow_up_review | Potentially confusing with newer timeout fields; not changed in this task. |
| old install docs mentioning generic `gtestcov codrax-check` | follow_up_review | Documentation cleanup candidate; behavior not changed in this task. |
| dual `gtestcov codrax-check` and `gtestcov codrax doctor` CLI path | follow_up_review | Compatibility/UX debt to review before release; behavior not changed here. |
| Zoekt optional PoC/fallback status | kept_intentionally | Left as optional fallback without real external integration validation. |
| semantic optional PoC/fallback status | kept_intentionally | Left as optional fallback without real external tool invocation. |

## Verification

Verification for this cleanup should include:

```text
pytest tests/test_core.py -k "evidence_pack or cache"
pytest tests/test_core.py
git diff --check
```

Final observed results are recorded in `.ai-bridge/agent-status.md`.
