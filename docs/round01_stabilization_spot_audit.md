# Round 01 Stabilization Spot Audit

> Superseded note, 2026-06-30: the schema-2 evidence pack compatibility described in this audit was intentionally removed by `round01_post_stabilization_cleanup.md`. Current evidence pack cache reads are v3-only, old cache formats are misses, and the CODRAX cache payload key is `payloads.codrax`.

日期：2026-06-30

## 1. 审计目标

按 `round01_stabilization_summary.md` 的稳定化结论做代码级 spot audit，并只做小范围修补。

核验点：

1. `detached_evidence.py` 的 PID 存活检测是否能避免假 running。
2. `evidence_pack.py` 的 v3 schema 是否真的兼容旧 schema-2 cache。
3. search / semantic CLI 输出是否明确 optional PoC/fallback。
4. `verify.py` timeout artifact 是否足够清楚，且没有引入 `taskkill` 等系统级强操作。

## 2. 审计结论

整体稳定化方向正确。本轮 spot audit 发现并修补了一个实质问题：

- v3 evidence pack 之前新写结构正确，但读取旧 schema-2 cache 的 fallback 只有意图，不是真正可达路径。

原因是 v3 与 v2 的 cache identity 都包含 `schema_version`，升级到 v3 后同一请求会得到新的 cache key/path；读取逻辑只查 v3 path，因此旧 v2 cache 不会命中。

## 3. 小范围修补

### 3.1 detached evidence

核验结果：通过，并补强测试。

已有路径：

- worker PID 不存在时，`evidence_status` 会写 failed result。
- `evidence_collect` 遇到 stale failed 时返回 failed。

补充：

- 新增 `test_detached_evidence_marks_stale_metadata_failed`，覆盖没有 PID 但 metadata 超过 stale window 的兜底路径。

### 3.2 evidence pack v3 / v2 cache

核验结果：发现问题并修补。

修补：

- `load_evidence_payload` 现在先检查 v3 cache identity。
- v3 miss 后会继续检查 schema-2 legacy cache identity。
- schema-2 legacy pack 通过旧结构 `payload.codrax_evidence` 读取，并映射成 `payload_name: legacy_codrax`。
- 新写仍只写 v3 结构：
  - `metadata`
  - `hits`
  - `payloads.legacy_codrax`

补充：

- 新增 `test_evidence_pack_loads_legacy_schema2_codrax_payload`，证明旧 schema-2 cache 可以被读取。

### 3.3 Zoekt / semantic optional PoC/fallback

核验结果：通过。

证据：

- search 输出包含 `integration_level: optional_poc_fallback`。
- search 输出包含 `external_backend_required: false`。
- Zoekt 输出包含 `integration_status`。
- semantic 输出包含 `integration_level: optional_poc_fallback`。
- semantic 输出包含 `external_backend_required: false`。
- semantic 输出包含 `external_backend_invoked: false`。

覆盖测试：

- `test_search_backend_falls_back_to_local_index_when_zoekt_missing`
- `test_search_cli_doctor_index_query_use_fallback`
- `test_semantic_backend_falls_back_without_compile_commands`
- `test_semantic_cli_doctor_references_overview_use_fallback`

### 3.4 verify timeout

核验结果：通过。

证据：

- timeout 返回值包含 `process_cleanup`。
- timeout json/md artifact 写入 `process_tree_guaranteed: false`。
- warning 明确说明 Windows `shell=True` + `subprocess.kill()` 不证明子进程树都已退出。
- 代码没有引入 `taskkill`、`wmic`、`netcfg`、`pnputil` 等系统级强操作。

覆盖测试：

- `test_verify_command_timeout_is_reported_and_cli_override_works`

## 4. 验证结果

spot audit 相关测试：

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

只有 CRLF/LF 工作区提示，没有 whitespace error。

强操作搜索：

```text
rg -n "taskkill|TerminateProcess|/T /F|wmic|netcfg|pnputil|reg add|SetEnvironmentVariable\('Path'" src tests docs
```

结果只命中文档中“没有引入 `taskkill /T /F`”的说明，没有命中源码或测试里的强系统操作。
