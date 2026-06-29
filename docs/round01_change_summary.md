# Round 01 Change Summary

## Reference Docs

This round implemented the plans from:

- `docs/round01_large_project_codrax_optimization_plan.md`
- `docs/round01_implementation_phases.md`

## Scope

Round 01 focused on making `gtestcov` usable on large C/C++ projects without forcing CODRAX to be the only search or understanding path. The work added safer scan boundaries, observable long-running command behavior, optional backend layers, cache reuse, and detached evidence collection.

## Implemented Areas

1. CODRAX long-run observability
   - Added heartbeat/status updates for long-running CODRAX calls.
   - Added live/final logs and bounded final log tails.
   - Added soft timeout, interrupted artifacts, and pre-terminate snapshots.

2. CODRAX command modes
   - Split checks into doctor, quick, and deep modes.
   - Added lightweight default diagnostics that do not read the repository.
   - Preserved deeper repository probing behind explicit flags.

3. Project scan safety and indexing
   - Added project-root path guards.
   - Added configurable source/test/build scan roots and excludes.
   - Added scan progress and truncation artifacts.
   - Added `.gtestcov/cache/file_index.json` with file metadata, C/C++ flags, gtest/gmock detection, and build/test config markers.

4. Evidence backend abstraction
   - Added uniform `EvidenceHit` / `EvidenceQuery` structures.
   - Added local index, bulk symbol scan, CODRAX, search, and semantic candidate evidence paths.
   - Kept external backend-specific details out of core models.

5. Optional search and semantic PoCs
   - Added `gtestcov search doctor/index/query`.
   - Added `gtestcov semantic doctor/references/overview`.
   - Zoekt, Serena, clangd, and ccls remain optional; missing tools fall back to local deterministic evidence.

6. Evidence pack and cache v2
   - Added `.gtestcov/cache/evidence_pack/<cache_key>.json`.
   - Reused evidence for repeated target operations.
   - Added cache hit/miss metadata to run status and outputs.
   - Invalidated cache on profile, index, or target changes.

7. Detached CODRAX evidence
   - Added `gtestcov evidence start/status/collect`.
   - Updated MCP evidence collection to start detached work by default.
   - `collect` produces normal evidence outputs and stores evidence packs after completion.

8. Verify streaming logs
   - Added build/test/coverage stdout and stderr tail logs under `.gtestcov/runs/<run_id>/commands/`.
   - Added verify command heartbeat events.
   - Added timeout markdown/json artifacts.
   - Kept existing verify result structure and coverage semantics compatible.

## Main New Files

- `src/gtestcov/file_index.py`
- `src/gtestcov/evidence_types.py`
- `src/gtestcov/evidence_backend.py`
- `src/gtestcov/search_backend.py`
- `src/gtestcov/semantic_backend.py`
- `src/gtestcov/evidence_pack.py`
- `src/gtestcov/detached_evidence.py`

## Verification

Full test suite result:

```text
85 passed, 1 skipped in 40.73s
```

Additional checks:

- `git diff --check` reported no whitespace errors.
- The only diff-check messages were Git line-ending warnings on Windows.
- The two reference docs remain text-equivalent to the original pasted attachments after normalizing line endings.

## Notes

- The skipped test is the Windows signal-termination case; Windows hard-terminates the subprocess and cannot exercise the same Python signal cleanup path.
- No system-level cleanup, network reset, registry edit, driver operation, WSL operation, or service modification was performed.
