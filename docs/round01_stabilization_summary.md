# Round 01 Stabilization Summary

> Superseded note, 2026-06-30: the schema-2 evidence pack compatibility described in this document was intentionally removed by `round01_post_stabilization_cleanup.md`. Current evidence pack cache reads are v3-only, old cache formats are misses, and the CODRAX cache payload key is `payloads.codrax`.

日期：2026-06-30

## 1. 目标

本轮只处理 `round01_implementation_audit.md` 指出的稳定性风险，不继续追加新功能。

覆盖任务：

- `round01-stabilize-1`: detached evidence stale running / PID 存活检测
- `round01-stabilize-2`: evidence pack 命名与 payload 泛化
- `round01-stabilize-3`: Zoekt/semantic CLI 输出明确 optional PoC/fallback
- `round01-stabilize-4`: verify Windows timeout process-tree 风险处理或明确诊断

## 2. 完成情况

### 2.1 detached evidence stale running

状态：完成。

变更：

- `evidence status` 会检查后台 worker PID。
- worker 已退出但没有写 result 时，状态会从 `running` 转成 `failed`。
- 会写入 `detached_evidence_result.json`，包含 `stale_reason`、`process_alive`、`pid` 和错误说明。
- `evidence collect` 遇到 stale failed 状态时返回 failed 结果，不再继续显示假 running。

覆盖测试：

- `test_detached_evidence_marks_dead_worker_failed`
- `test_detached_evidence_marks_stale_metadata_failed`
- `test_detached_evidence_start_status_collect_cli`

### 2.2 evidence pack 泛化

状态：完成。

变更：

- evidence pack schema 升级到 v3。
- 新写出的 pack 拆成：
  - `metadata`: backend-neutral 元数据、operation、target、request hash、fingerprints。
  - `hits`: generic evidence hits。
  - `payloads.legacy_codrax`: 兼容 CODRAX 的 legacy payload。
- 不再写旧结构 `payload.codrax_evidence`。
- 新增 generic API：
  - `load_evidence_payload`
  - `store_evidence_pack`
- 主调用点改为显式 legacy 命名：
  - `load_legacy_codrax_payload`
  - `store_legacy_codrax_payload`
- `load_codrax_evidence_pack` / `store_codrax_evidence_pack` 只作为兼容别名保留。

覆盖测试：

- `test_evidence_pack_cache_hits_and_invalidates_for_analyze`
- `test_evidence_pack_loads_legacy_schema2_codrax_payload`
- `test_cover_reuses_evidence_pack_on_second_same_target_run`

### 2.3 Zoekt / semantic optional PoC/fallback 明示

状态：完成。

变更：

- search 输出新增：
  - `integration_level: optional_poc_fallback`
  - `external_backend_required: false`
  - Zoekt `integration_status`
  - 明确 notes：Zoekt 是 optional PoC，local file_index 是安全 fallback。
- semantic 输出新增：
  - `integration_level: optional_poc_fallback`
  - `external_backend_required: false`
  - `external_backend_invoked: false`
  - 明确 notes：Serena/clangd/ccls 当前是 optional PoC discovery，本地 candidate fallback 仍是当前行为。

覆盖测试：

- `test_search_backend_falls_back_to_local_index_when_zoekt_missing`
- `test_search_cli_doctor_index_query_use_fallback`
- `test_semantic_backend_falls_back_without_compile_commands`
- `test_semantic_cli_doctor_references_overview_use_fallback`

### 2.4 verify Windows timeout 子进程风险

状态：完成，采用明确诊断方案。

变更：

- timeout 时仍调用 `process.kill()` 终止当前 shell/process。
- timeout 返回值新增 `process_cleanup`。
- timeout json/md artifact 写入：
  - cleanup method
  - pid
  - platform
  - `process_tree_guaranteed: false`
  - `manual_check_recommended`
  - Windows shell 子进程树不保证清理的 warning。

说明：

本轮没有引入 `taskkill /T /F` 等强制进程树操作，避免在稳定化阶段扩大系统影响面。

覆盖测试：

- `test_verify_command_timeout_is_reported_and_cli_override_works`

## 3. 验证结果

相关稳定化测试：

```text
11 passed, 78 deselected in 11.40s
```

全量测试：

```text
88 passed, 1 skipped in 48.60s
```

`git diff --check`：

```text
exit code 0
```

仅有 Git 的 CRLF/LF 工作区提示，没有 whitespace error。

## 4. 剩余注意事项

- Zoekt 与 Serena/clangd/ccls 仍不是完整外部工具集成，只是 optional PoC/fallback。
- `load_codrax_evidence_pack` / `store_codrax_evidence_pack` 仍保留为兼容别名，后续可以在大版本边界再移除。
- verify timeout 目前采用风险明示，不做强制子进程树清理。
