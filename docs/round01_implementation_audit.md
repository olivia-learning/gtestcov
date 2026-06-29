# Round 01 Implementation Audit

审计日期：2026-06-30

## 1. 审计范围

本审计只核对 round01 已声明的实现内容，不继续追加功能。

重点输入：

- `docs/round01_change_summary.md`
- `docs/round01_implementation_phases.md`
- `docs/round01_large_project_codrax_optimization_plan.md`
- `src/gtestcov/` 下 round01 新增或修改的实现
- `tests/test_core.py` 中 round01 相关测试

本轮审计没有执行系统级清理、网络重置、注册表、驱动、WSL、服务、虚拟化相关操作。

## 2. 总体结论

`round01_change_summary.md` 所说的大部分模块、CLI 命令和测试确实存在，当前测试也通过。

但 round01 的实际实现范围明显超过“每次只推进一个阶段”的节奏。它不是只完成第 0 阶段文档，而是一次性推进了第 1 到第 13 阶段中的大量功能。因此，下一步不应继续加功能，应先处理或明确接受本审计列出的风险。

当前最重要的风险有四个：

1. `evidence_pack` 已有统一 hits 和 cache 目录，但读写 API 与 payload 仍以 `CodraxEvidence` / `codrax_evidence` 为中心。
2. detached evidence 已能 start/status/collect，但 status 不校验 PID 存活，也没有取消或 stale running 处理，存在状态长期卡在 running 的风险。
3. Zoekt 与 Serena/clangd/ccls 都保持 optional，没有硬依赖；但真实外部工具路径缺少集成验证，semantic 当前主要是本地 fallback/PoC。
4. verify streaming log 已落地，但 Windows 下 `shell=True` 后使用 `process.kill()` 可能只杀 shell，不一定可靠清理子进程树。

## 3. 当前验证结果

当前测试命令：

```powershell
$env:PYTHONPATH='<project-root>\src'
& '<python>' -m pytest tests\test_core.py
```

结果：

```text
85 passed, 1 skipped in 40.64s
```

说明：

- skipped 项为 Windows 信号终止相关测试。
- 本次审计尝试执行 `git status` 与查找常见 Git 路径，但当前 PowerShell 环境中未找到 `git`，因此本文件不把 git diff/status 作为当前审计证据。

## 4. 需求核对矩阵

| 阶段 | 要求 | 审计状态 | 证据 | 备注 |
|---|---|---:|---|---|
| 0 | 创建并保留 round01 参考文档 | PASS | `docs/round01_implementation_phases.md`、`docs/round01_large_project_codrax_optimization_plan.md` 存在 | 此前已确认内容与用户提供文本一致 |
| 1 | CODRAX 长跑 heartbeat/status/live/final log | PASS | `src/gtestcov/codrax.py` 有 `_emit_codrax_heartbeat`，并 `print(..., flush=True)`；测试含 heartbeat 断言 | stdout heartbeat 不是只写文件 |
| 2 | `codrax-check` 拆为 doctor/quick/deep | PASS | `src/gtestcov/cli.py` 有 `codrax doctor` 与 `codrax-check` 参数；测试覆盖 quick/doctor | CLI 结构已存在 |
| 3 | CODRAX timeout/interrupted artifacts | PASS | `src/gtestcov/codrax.py` 有 timeout warning、pre-terminate snapshot、interrupted artifacts 逻辑 | Windows 信号相关测试有 skipped 项 |
| 4 | 路径保护与 source/test/build scan roots | PASS | discovery/analyzer/profile 相关测试覆盖 scan scope 与 excludes | 仍需保持项目事实只来自 profile、CODRAX file:line 或 gtestcov artifacts |
| 5 | discover/analyze scan progress 与 truncation artifacts | PASS | `tests/test_core.py` 有 scan progress 与 truncation artifact 测试 | 已覆盖大项目扫描路径 |
| 6 | `.gtestcov/cache/file_index.json` | PASS | `src/gtestcov/file_index.py` 存在；测试覆盖 build/status/refresh/discovery hit | 当前实现还记录 C++、GTest/GMock、build/test config 元数据 |
| 7 | local bulk symbol scan | PASS | `src/gtestcov/evidence_backend.py` 中 `BulkSymbolScanBackend`；测试覆盖 bulk 读取行为 | 避免每个符号重复读同一 cpp 文件 |
| 8 | `EvidenceHit` / `EvidenceQuery` 抽象 | PASS | `src/gtestcov/evidence_types.py` 存在；search、semantic、backend 返回统一 hit | 泛化层已存在 |
| 9 | Zoekt search backend PoC | PARTIAL | `src/gtestcov/search_backend.py` 用 `shutil.which` 探测 `zoekt-index` / `zoekt-grep`，缺失时 fallback 到 local index | optional 目标达成；真实 Zoekt 集成路径未验证 |
| 10 | Serena/clangd/ccls semantic backend PoC | PARTIAL | `src/gtestcov/semantic_backend.py` 探测 `serena`、`clangd`、`ccls` 和 `compile_commands.json` | 当前状态明确返回 `ready_not_invoked_in_poc` 或 fallback，未真实调用 semantic server |
| 11 | evidence pack cache v2 | PARTIAL/RISK | `src/gtestcov/evidence_pack.py` 写 `.gtestcov/cache/evidence_pack/<cache_key>.json`，包含 `sources.configured` 与 generic `hits` | 读写函数仍叫 `load_codrax_evidence_pack` / `store_codrax_evidence_pack`，payload key 为 `codrax_evidence` |
| 12 | detached evidence start/status/collect | RISK | `src/gtestcov/detached_evidence.py` 用 `subprocess.Popen` 启动后台 worker；测试覆盖基本 start/status/collect | `status` 不检查 PID 存活；无 stale running、cancel、worker died 检测 |
| 13 | verify streaming logs | PASS/RISK | `src/gtestcov/verify.py` 写 `commands/*.stdout.tail.log`、`*.stderr.tail.log`、timeout json/md，并周期更新 heartbeat | Windows 下 `shell=True` + `process.kill()` 有子进程残留风险 |
| 17/18 | 外部工具 optional，不改变默认流程 | PASS/PARTIAL | search/semantic doctor 均报告 fallback 与 `default_flow_changed: False` | 目前未发现 Zoekt/semantic 硬依赖，但需继续防止默认流程被外部工具绑死 |
| 流程 | 每轮只推进一个阶段 | PROCESS DEVIATION | `round01_change_summary.md` 声明第 1 到第 13 阶段大量实现已完成 | 需要先审计和稳态修补，再进入下一轮 |

## 5. 重点风险说明

### 5.1 Evidence pack 仍偏 CODRAX

实现已经把本地 index、bulk symbol、Zoekt、semantic、CODRAX 都放进 `sources.configured`，并缓存统一 `hits`。

但 cache API 和 payload 仍以 CODRAX 命名：

- `load_codrax_evidence_pack`
- `store_codrax_evidence_pack`
- `payload.codrax_evidence`
- 缺失时返回 `codrax_evidence_missing`

这会让后续 backend 泛化变得别扭。建议下一步把 pack 分成 backend-neutral 元数据、generic hits、legacy CODRAX payload 三层，或者明确这是 CODRAX evidence 的兼容缓存，不要把它宣传为完全通用 evidence pack。

### 5.2 Detached evidence 有 stale running 风险

`evidence_start` 会保存 PID 并启动后台 worker，但 `evidence_status` 只读取 json/meta/status 文件，没有检查该 PID 是否仍存活。

`evidence_collect` 在没有 result 且不是 background worker 时，会直接返回 running。因此如果 worker 异常退出且没有写 result/meta failed，用户侧可能长期看到 running。

建议补充：

- PID 存活检测。
- stale running 超时判定。
- worker died 后把状态转为 failed。
- cancel 命令或可恢复的 cleanup 机制。

### 5.3 External backend 目前主要是 optional PoC

Zoekt：

- 已实现工具探测、index/query 命令路径、local index fallback。
- 测试主要覆盖 Zoekt 缺失时 fallback。
- 未看到真实 Zoekt 环境下的集成测试证据。

Semantic：

- 已实现 Serena/clangd/ccls 探测与 compile_commands 诊断。
- 当前 `semantic_backend_status` 明确可能为 `ready_not_invoked_in_poc`。
- `references` 实际使用 bulk symbol scan，`overview` 实际使用本地解析 fallback。

因此这两项应被标记为 optional PoC，而不是完整外部工具集成。

### 5.4 Verify timeout 的 Windows 子进程风险

verify 已经有 tail log、heartbeat 和 timeout artifacts。

但 `_run_command` 使用 `subprocess.Popen(..., shell=True)`，timeout 时调用 `process.kill()`。在 Windows 上，这可能只终止 shell 进程，不能保证杀掉 shell 启动的子进程树。

建议后续补充 Windows process tree 处理，或在文档中明确 timeout 后可能需要人工确认子进程状态。

## 6. 建议下一步

1. 暂停继续追加 feature。
2. 先把 round01 标成一次“大范围实现”，不要再把它视为单阶段变更。
3. 优先处理 detached stale running 与 evidence pack CODRAX-centered payload。
4. 对 Zoekt 与 semantic external backend 补真实集成验证；如果暂时不做，就在文档和 CLI 输出中明确是 optional PoC/fallback。
5. 检查 verify timeout 在 Windows 下的子进程处理。
6. 风险处理后再进入下一轮阶段实施。
