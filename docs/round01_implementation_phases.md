# gtestcov 大项目优化分阶段实施计划

> 文件建议路径：`docs/implementation_phases.md`

## 1. 目标

本文档定义 `gtestcov` 大项目性能、CODRAX 长跑稳定性、多 evidence backend 和缓存能力的分阶段实施顺序。

核心原则：

```text
每次只做一个小闭环。
每次必须可测试、可回滚、可解释。
不要一次性重构全部流程。
```

## 2. 总体阶段顺序

推荐实施顺序：

```text
0. 文档与分阶段计划
1. CODRAX 长跑 heartbeat / status / live log
2. codrax-check 拆成 doctor / quick / deep
3. CODRAX soft timeout / interrupted artifacts
4. 路径安全 + source-root/test-root/build-root
5. discover/analyze 使用 scan scope + 扫描进度
6. file_index
7. bulk symbol scan
8. Evidence Backend 抽象层
9. Zoekt 搜索后端 PoC
10. Serena + clangd/ccls 语义后端 PoC
11. evidence pack + cache v2
12. detached CODRAX
13. verify streaming logs
```

## 3. 第 0 次：文档与计划

### 目标

只新增或更新文档，不改实现。

### 文件范围

新增或更新：

```text
docs/large_project_codrax_optimization_plan.md
docs/implementation_phases.md
```

禁止修改：

```text
src/gtestcov/*.py
tests/*.py
pyproject.toml
README.md
```

### 必须明确

* CODRAX 不再作为唯一搜索/理解引擎。
* 未来支持 backend：

  * local file_index
  * local bulk symbol scan
  * Zoekt
  * Serena/clangd
  * CODRAX
* 外部工具必须是 optional backend，不得变成默认硬依赖。
* 后续实施必须分阶段，不允许一次性大改。

### 验收

* 只产生文档变更。
* 不改变 CLI。
* 不改变测试。
* 不新增依赖。

## 4. 第 1 次：CODRAX 长跑 heartbeat / status / live log

### 目标

解决 CODRAX 长时间运行时 OpenCode 无输出、用户不知道当前状态的问题。

### 改动范围

```text
src/gtestcov/codrax.py
src/gtestcov/run_status.py
可选新增 src/gtestcov/progress.py
tests/test_core.py
```

### 要求

* CODRAX 子进程启动前必须先写状态文件。
* 运行期间必须持续写：

  * `codrax_status.json`
  * `gtestcov_status.json`
  * `gtestcov_events.ndjson`
* 运行期间必须向 stdout 打 heartbeat。
* 即使 CODRAX 没有 stdout/stderr，gtestcov 也必须输出自己的 heartbeat。
* OpenCode 中必须能看到 run_id、elapsed seconds、status path、last activity。

### 非目标

* 不拆 `codrax-check`。
* 不做缓存。
* 不做 detached mode。
* 不改扫描范围。

## 5. 第 2 次：codrax-check 拆成 doctor / quick / deep

### 目标

避免默认 `codrax-check` 触发大仓深度分析。

### 新命令

```bash
gtestcov codrax doctor --project-root .
gtestcov codrax-check --quick --project-root . --target <target> --build-file <file>
gtestcov codrax-check --deep --project-root .
```

### 语义

* `doctor`：只检查 CODRAX CLI、版本、协议，不读仓库。
* `quick`：只验证明确 target/build-file 可读并能返回 `file:line`。
* `deep`：保留当前深度仓库 citation probe。

### 非目标

* 不做 evidence pack。
* 不做缓存。
* 不做 scan scope。

## 6. 第 3 次：CODRAX soft timeout / interrupted artifacts

### 目标

解决 CODRAX 实际未完成却被 idle/max runtime 或外层 signal 终止后不可解释的问题。

### 要求

新增产物：

```text
codrax_timeout_warning.md
codrax_pre_terminate_snapshot.json
codrax_interrupted.md
codrax_interrupted.json
```

行为：

* idle timeout 到达时先 warning，不立即 kill。
* native log 仍增长时继续等待。
* hard kill 前写 pre-terminate snapshot。
* SIGTERM/SIGINT 时写 interrupted artifact。
* `terminated_by_signal` 必须解释可能原因和恢复建议。

### 非目标

* 不做 detached mode。
* 不改 verify。
* 不接外部工具。

## 7. 第 4 次：路径安全 + source-root/test-root/build-root

### 目标

拆分 `project-root` 和扫描范围。

### profile 增加

```yaml
paths:
  source_roots:
    - src
  test_roots:
    - tests
  build_roots:
    - .
  exclude_dirs:
    - .git
    - .gtestcov
    - .repo
    - out
    - build
    - third_party
    - prebuilts
    - vendor
  max_files: 8000
  max_file_bytes: 1048576
```

### 要求

* `run_id` 严格净化。
* `target` 不允许越出 project-root。
* source/test/build roots 不允许越界。
* 旧 profile 保持兼容。

### 非目标

* 不重写 discover。
* 不做 file_index。
* 不做 evidence cache。

## 8. 第 5 次：discover/analyze 使用 scan scope + 扫描进度

### 目标

不再默认扫描整个 `project-root`。

### 要求

* `discover_project` 只扫描 source/test/build roots。
* 排除 profile 中的 exclude dirs。
* 每 N 个文件更新进度。
* 超过 max_files 时写明确产物。
* `analyze` 中涉及扫描的路径也必须走 scan scope。

### 非目标

* 不做持久 file index。
* 不做 bulk symbol scan。
* 不做外部 backend。

## 9. 第 6 次：file_index

### 目标

避免每次 discover/analyze 都重新扫描大项目。

### 新命令

```bash
gtestcov index build --project-root .
gtestcov index refresh --project-root .
gtestcov index status --project-root .
```

### 产物

```text
.gtestcov/cache/file_index.json
```

### 要求

* 支持 mtime/size 增量刷新。
* `discover_project` 优先使用 file_index。
* 文件变化时只更新变化项。
* index hit/miss 原因可见。

### 非目标

* 不做 CODRAX evidence cache。
* 不接 Zoekt/Serena。
* 不改 verify。

## 10. 第 7 次：bulk symbol scan

### 目标

消除 `符号数 × 文件数` 遍历放大。

### 要求

将当前模式：

```text
for symbol in symbols:
    for file in files:
        read file
```

改成：

```text
for file in files:
    read file once
    check all symbols
```

新增或改造：

```text
classify_symbols_bulk(...)
```

### 验收

* 25 个符号时，每个 C/C++ 文件最多读取一次。
* 输出结构兼容原 `SymbolReport`。
* 无 symbol 时不扫描。
* 优先用 file_index 限定候选文件。

## 11. 第 8 次：Evidence Backend 抽象层

### 目标

为 local index、bulk scan、Zoekt、Serena、CODRAX 建立统一证据接口。

### 新增文件

```text
src/gtestcov/evidence_backend.py
src/gtestcov/evidence_types.py
```

### 核心结构建议

```python
class EvidenceHit(BaseModel):
    backend: str
    kind: str
    path: str
    line: int | None = None
    symbol: str = ""
    excerpt: str = ""
    confidence: str = "candidate"
    reason: str = ""
```

### 本阶段只接入

```text
local_index
bulk_symbol_scan
codrax
```

### 非目标

* 不接 Zoekt。
* 不接 Serena。
* 不做完整 evidence pack cache。

## 12. 第 9 次：Zoekt 搜索后端 PoC

### 目标

引入快速全文检索后端，减少 CODRAX 全仓搜索。

### 新命令

```bash
gtestcov search doctor --project-root .
gtestcov search index --project-root .
gtestcov search query --project-root . --query "SomeSymbol"
```

### 要求

* Zoekt 是 optional backend。
* Zoekt 不存在时正常 fallback。
* 搜索结果转为 `EvidenceHit`。
* 不改变主流程默认行为，先作为 PoC。

## 13. 第 10 次：Serena + clangd/ccls 语义后端 PoC

### 目标

引入 C++ 语义查询后端。

### 新命令

```bash
gtestcov semantic doctor --project-root .
gtestcov semantic references --project-root . --symbol <symbol>
gtestcov semantic overview --project-root . --target <file>
```

### 要求

* Serena/clangd/ccls 是 optional backend。
* 没有 `compile_commands.json` 时输出明确诊断。
* 不得让基础流程崩溃。
* 结果转为 `EvidenceHit`。
* 不替代 CODRAX，只提供候选证据。

## 14. 第 11 次：evidence pack + cache v2

### 目标

在 backend 抽象之后再做 evidence pack，避免写成 CODRAX 专用缓存。

### 输入来源

```text
local_index
bulk_symbol_scan
Zoekt
Serena/clangd
CODRAX
```

### 产物

```text
.gtestcov/cache/evidence_pack/<cache_key>.json
```

### 要求

* 同一 target 第二次 `cover` 命中 cache。
* `profile-sync` 与 `analyze` 不重复跑 CODRAX。
* cache hit/miss 原因写入 status。
* profile/index/target 变化时 cache 失效。

## 15. 第 12 次：detached CODRAX

### 目标

深度 CODRAX 请求不再强制同步阻塞，避免外层 runner timeout 直接杀掉 CODRAX。

### 新命令

```bash
gtestcov evidence start --project-root . --target <target>
gtestcov evidence status --project-root . --run-id <run_id>
gtestcov evidence collect --project-root . --run-id <run_id>
```

### 要求

* `start` 快速返回 run_id/status path。
* `status` 可轮询。
* `collect` 在完成后生成 evidence pack。
* MCP 深度请求默认走 start/status/collect。

## 16. 第 13 次：verify streaming logs

### 目标

build/test/coverage 长跑也要有进度和日志。

### 产物

```text
.gtestcov/runs/<run_id>/commands/
  build.stdout.tail.log
  build.stderr.tail.log
  test.stdout.tail.log
  test.stderr.tail.log
  coverage.stdout.tail.log
  coverage.stderr.tail.log
```

### 要求

* build/test/coverage 运行中有 heartbeat。
* timeout 写 markdown/json artifact。
* 保持原 verify 结果结构兼容。
* 不改变 coverage 判断语义，除非另行立项。

## 17. 每次 Codex 修改的通用约束

每次实现阶段都必须遵守：

```text
本次只做当前阶段，不实现后续阶段。
先写或更新测试，再改实现。
不要重写无关模块。
不要改 public CLI 行为，除非本阶段明确要求。
保持旧测试通过。
如果发现需要大范围重构，先写 TODO 文档，不要顺手实现。
输出本次改动文件清单、行为变化、测试结果。
```

## 18. 外部工具约束

Zoekt、Serena、clangd、ccls 等外部工具必须满足：

* optional backend。
* 未安装时不影响基础功能。
* 不自动下载安装到用户系统。
* 不修改系统配置。
* 不成为默认硬依赖。
* 不绕过 gtestcov 的 file:line evidence policy。
* 不直接授权弱 AI 修改生产代码。

## 19. 最小可用改造目标

若只做第一批，应至少完成：

```text
1. CODRAX heartbeat/status/live log
2. codrax-check doctor/quick/deep
3. CODRAX soft timeout/interrupted artifacts
4. source-root/test-root/build-root
5. scan scope
6. file_index
7. bulk symbol scan
```

完成这七步后，大项目体验应显著改善：

* OpenCode 不再长时间无输出。
* `codrax-check` 不再默认触发大仓深度分析。
* 扫描范围可控。
* 本地扫描可增量。
* 符号解析不再出现 `符号数 × 文件数` 放大。
* CODRAX 异常终止有明确诊断和恢复建议。
