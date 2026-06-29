# gtestcov 大型项目与 CODRAX 后端优化总方案

> 文件建议路径：`docs/large_project_codrax_optimization_plan.md`

## 1. 本文档目标

本文档用于规划 `gtestcov` 在大型 C/C++ 项目中的性能、可观察性、CODRAX 长跑稳定性、证据缓存和多后端代码理解能力。

本阶段只做文档规划，不改实现。

## 2. 当前核心问题

`gtestcov` 当前主流程过度依赖 CODRAX 作为项目事实来源。CODRAX 能提供有价值的 `file:line` 证据和结构化分析，但在大型 C++ 项目中，它不适合承担全部职责。

大型 C++ 项目通常存在以下特点：

* 文件数量大，例如 3 万到 4 万级总文件。
* C/C++ 文件多，例如 2 万到 3 万级。
* 宏、模板、条件编译、生成代码、编译参数复杂。
* 正确语义分析依赖 `compile_commands.json`。
* 首次索引和跨文件引用分析天然耗时。
* 弱 AI 如果直接依赖全仓阅读，会反复搜索、反复读文件、重复消耗 token 和时间。

因此，本项目后续必须从“CODRAX 单后端”演进为“多证据后端 + CODRAX 综合判断”的架构。

## 3. 总体原则

### 3.1 CODRAX 不再作为唯一搜索/理解引擎

CODRAX 后续定位应调整为：

```text
从：主搜索引擎 + 主理解引擎 + 主判断引擎
到：深度证据综合器 + 复杂判断器 + 兜底解释器
```

也就是说：

* 不再让 CODRAX 默认承担全仓搜索。
* 不再让 CODRAX 每次从头找上下文。
* 不再让 CODRAX 负责所有 symbol/reference/build/test 查找。
* CODRAX 应优先消费其它后端提供的候选 `file:line` 证据。
* CODRAX 只在需要跨证据综合判断、解释风险、生成测试义务矩阵时介入。

### 3.2 确定性工具优先，LLM 后置判断

后续设计应遵循：

```text
先用确定性工具缩小范围
再用 CODRAX/弱 AI 做综合判断
```

工具分工：

| 层级       | 角色                                        | 示例                     |
| -------- | ----------------------------------------- | ---------------------- |
| 本地文件索引   | 快速知道有哪些文件、哪些文件变了、哪里有 gtest/gmock/build 配置 | local file_index       |
| 本地批量符号扫描 | 快速定位 symbol 字符串出现位置，避免 symbols × files 扫描 | local bulk symbol scan |
| 搜索后端     | 快速全文搜索、正则搜索、build/test 配置候选定位             | Zoekt                  |
| 语义后端     | 定义、引用、符号概览、跨文件关系                          | Serena + clangd/ccls   |
| 深度推理后端   | 综合证据、解释测试风险、判断任务可执行性                      | CODRAX                 |

### 3.3 外部工具必须是 optional backend

后续引入 Zoekt、Serena、clangd、ccls 等工具时，必须满足：

* 外部工具不能成为默认硬依赖。
* 未安装外部工具时，`gtestcov` 应继续可用。
* 外部工具不可用时，应输出明确诊断并 fallback 到本地 index / bulk scan / CODRAX。
* 新工具必须通过统一 Evidence Backend 接口接入。
* 不允许某个外部工具的特殊字段污染核心模型。
* 不允许因为外部工具缺失导致基础命令无法运行。

### 3.4 所有后端输出统一为 file:line 候选证据

后续所有 evidence backend 都应尽量输出统一结构：

```text
backend
kind
path
line
symbol
excerpt
confidence
reason
```

如果后端无法提供 line，必须显式标记 `line unavailable`，不能伪造行号。

## 4. 未来支持的 Evidence Backend

后续至少支持以下 backend。

### 4.1 local file_index

用途：

* 记录项目文件列表。
* 记录文件大小、mtime、suffix、是否 C/C++。
* 记录是否包含 gtest/gmock include。
* 记录是否 build/test 配置文件。
* 支持增量刷新。
* 为 discover/analyze 提供快速基础事实。

性质：

* 内置后端。
* 默认启用。
* 不需要外部依赖。

### 4.2 local bulk symbol scan

用途：

* 批量扫描多个 symbol。
* 避免当前 `符号数 × 文件数` 的多轮遍历。
* 每个 C/C++ 文件最多读取一次。
* 输出 symbol 出现位置和初步分类。

性质：

* 内置后端。
* 默认启用。
* 不需要外部依赖。
* 精度不如 clangd/ccls，但速度快、可控。

### 4.3 Zoekt

用途：

* 快速全文检索。
* 快速正则检索。
* 查找 build/test 配置候选。
* 查找符号字符串、宏、测试用例、fixture、fake/harness。
* 为 CODRAX 提供候选文件列表，减少 CODRAX 全仓搜索。

性质：

* 外部 optional backend。
* 不得成为硬依赖。
* 未安装时必须 fallback。
* 首先做 PoC，不直接改变主流程默认行为。

### 4.4 Serena + clangd/ccls

用途：

* C/C++ 符号级查询。
* 找定义。
* 找引用。
* 获取 symbol overview。
* 辅助判断 target 依赖、调用方、已有测试覆盖面。
* 依赖 `compile_commands.json` 时提供更可靠语义结果。

性质：

* 外部 optional backend。
* 不得成为硬依赖。
* 没有 `compile_commands.json` 时输出明确诊断，不得让主流程崩溃。
* clangd/ccls 后端选择应可配置。
* 初期只做 doctor / overview / references PoC。

### 4.5 CODRAX

用途：

* 综合多后端证据。
* 验证证据是否足以生成测试。
* 输出项目事实、风险、测试义务、manual review 条件。
* 在其它后端不足时作为兜底深度分析。
* 对复杂判断提供结构化答案和 `file:line` 证据。

性质：

* 仍是重要后端，但不再是唯一后端。
* 深度 CODRAX 请求必须支持长跑可观察性。
* 深度 CODRAX 请求必须支持缓存。
* 深度 CODRAX 请求后续应支持 detached start/status/collect 模式。

## 5. 七个痛点与解决方向

| 编号 | 痛点              | 解决方向                                                      |
| -- | --------------- | --------------------------------------------------------- |
| 1  | CODRAX 缓存未利用    | evidence pack + backend cache + CODRAX 原生缓存复用             |
| 2  | 扫描范围过大          | 拆分 project-root/source-root/test-root/build-root          |
| 3  | 符号扫描性能差         | file_index + bulk symbol scan + Serena/clangd             |
| 4  | 进度反馈不足          | heartbeat/status/live log/progress.json                   |
| 5  | CODRAX 处理时间长    | quick/deep 拆分、缓存、detached、后端前置缩小范围                        |
| 6  | CODRAX 异常终止     | soft timeout、interrupted artifacts、pre-terminate snapshot |
| 7  | codrax-check 过重 | doctor / quick / deep 三层拆分                                |

## 6. 推荐最终架构

```text
gtestcov
  |
  +-- Path Scope Layer
  |     +-- project_root
  |     +-- source_roots
  |     +-- test_roots
  |     +-- build_roots
  |     +-- exclude_dirs
  |
  +-- Local Index Layer
  |     +-- file_index
  |     +-- incremental refresh
  |
  +-- Evidence Backend Layer
  |     +-- local file_index backend
  |     +-- local bulk symbol scan backend
  |     +-- Zoekt backend optional
  |     +-- Serena/clangd backend optional
  |     +-- CODRAX backend optional but important
  |
  +-- Evidence Pack Layer
  |     +-- target evidence pack
  |     +-- cache key
  |     +-- hit/miss reason
  |
  +-- Analysis Layer
  |     +-- profile candidates
  |     +-- target understanding
  |     +-- symbol resolution
  |     +-- test obligation matrix
  |
  +-- Task Layer
  |     +-- task.md
  |     +-- permission warmup
  |     +-- memory refresh
  |
  +-- Verification Layer
  |     +-- preflight
  |     +-- streaming build/test/coverage
  |     +-- coverage provenance
  |
  +-- Observability Layer
        +-- gtestcov_status.json
        +-- codrax_status.json
        +-- progress.json
        +-- gtestcov_events.ndjson
        +-- live logs
        +-- final logs
        +-- timeout/interruption artifacts
```

## 7. 第 0 次实施范围

本次只做文档，不做任何实现。

允许新增或更新：

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

除非用户另行明确要求。

## 8. 第 0 次验收标准

本次完成后应满足：

* 文档明确说明 CODRAX 不再作为唯一搜索/理解引擎。
* 文档明确列出未来支持 backend：

  * local file_index
  * local bulk symbol scan
  * Zoekt
  * Serena/clangd
  * CODRAX
* 文档明确说明外部工具必须是 optional backend，不得成为默认硬依赖。
* 文档明确列出后续分阶段实施顺序。
* 不修改任何实现代码。
* 现有测试无需因为本次变更而改变。

## 9. 后续注意事项

后续任何实现阶段都必须遵循：

* 每次只做一个阶段，不跨阶段实现。
* 先写或更新测试，再改实现。
* 不引入不可选外部依赖。
* 不让外部工具缺失导致基础命令不可用。
* 不让 CODRAX 再承担默认全仓搜索。
* 所有长跑任务必须先落盘状态，再运行，并持续输出 heartbeat。
