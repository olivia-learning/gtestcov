# CODRAX 长跑验证结论 v1

## 背景

这次验证的核心问题不是“CODRAX 能不能快速返回”，而是：

- CODRAX 真实分析复杂项目时可能运行很久。
- 运行期间需要能判断它是没启动、正在分析、卡住、超时，还是已经正常结束。
- CODRAX 结束后，gtestcov 必须及时记录本次 final 输出和关键状态，不能出现 CODRAX 已结束但 gtestcov 没有日志、没有状态、仍然看起来在运行的情况。
- 快速模拟测试可以用于覆盖后续逻辑，但最终必须有真实 CODRAX 长跑完成场景验证。

## 验证对象

- 工具：gtestcov
- CODRAX 模型：MiniMax-M2.7
- 真实项目：F Prime 本地开源项目
- 项目路径：
  - Windows: `<workspace>\_local\open_source\fprime`
  - WSL: `<workspace-wsl>/_local/open_source/fprime`
- 目标文件：`Svc/ActiveRateGroup/ActiveRateGroup.cpp`
- run id：`fprime-codrax-long-complete-real-v2`

## 关键配置

本次真实长跑使用项目侧 `project_profile.yaml` 中的 CODRAX 超时配置：

```yaml
evidence:
  codrax:
    idle_timeout_seconds: 300
    max_runtime_seconds: 7200
```

含义：

- `idle_timeout_seconds: 300`：如果 CODRAX 和它的 native log 连续 300 秒都没有活动，才认为可能卡住。
- `max_runtime_seconds: 7200`：单次 CODRAX 请求最多允许运行 7200 秒。
- 这不是 20 秒、90 秒、300 秒 hard cap 的快速验收，而是真实允许 CODRAX 长时间分析后自然完成。

## 执行命令

在 WSL 中执行：

```bash
cd <workspace-wsl>/_local/open_source/fprime
source "$HOME/.profile" >/dev/null 2>&1 || true
PYTHONPATH=<workspace-wsl>/gtestcov/src timeout 7500 python3 -m gtestcov.cli evidence \
  --project-root . \
  --target Svc/ActiveRateGroup/ActiveRateGroup.cpp \
  --run-id fprime-codrax-long-complete-real-v2
```

说明：

- 外层 `timeout 7500` 只是防止测试本身无限挂死，时间大于 gtestcov 的 `max_runtime_seconds`。
- 命令中不包含任何模型 key。
- CODRAX 由项目 profile 和本机环境配置提供。

## 真实长跑结果

gtestcov 外层状态文件：

```json
{
  "run_id": "fprime-codrax-long-complete-real-v2",
  "phase": "evidence.done",
  "step": "evidence",
  "command": "gtestcov evidence",
  "target": "Svc/ActiveRateGroup/ActiveRateGroup.cpp",
  "elapsed_seconds": 353.0,
  "codrax_status": "ok"
}
```

CODRAX 状态文件关键结果：

```json
{
  "operation": "project_understanding",
  "status": "ok",
  "phase": "done",
  "returncode": 0,
  "timeout_kind": "",
  "elapsed_seconds": 352.0,
  "idle_timeout_seconds": 300,
  "max_runtime_seconds": 7200
}
```

final output index 关键结果：

```json
{
  "operation": "project_understanding",
  "status": "ok",
  "returncode": 0,
  "timeout_kind": "",
  "native_log_file_count": 1,
  "file_line_ref_count": 21,
  "final_log_truncated": false,
  "final_log_size_bytes": 90495
}
```

结论：

- CODRAX 真实运行约 5 分 53 秒后正常完成。
- gtestcov 没有把长运行误判成超时。
- CODRAX final 输出已写入 gtestcov 自己的 final log。
- final log 没有被截断。
- CODRAX 返回了真实 `file:line` 证据，共 21 个引用。
- gtestcov 外层状态也正确落盘为 `evidence.done`。

## 生成的关键产物

运行目录：

```text
<project-root>\.gtestcov\runs\fprime-codrax-long-complete-real-v2
```

关键文件：

```text
gtestcov_status.json
codrax_status.json
gtestcov_events.ndjson
project_understanding.json
project_understanding.md
codrax_evidence.json
codrax_evidence.md
codrax_final_log.md
codrax_final_outputs/0001_project_understanding.md
codrax_final_outputs/index.json
codrax_native_logs/project_understanding/codrax-20260629-162032-000-142609.log
```

各文件作用：

- `gtestcov_status.json`：gtestcov 外层流程状态，说明当前命令是否完成。
- `codrax_status.json`：CODRAX 子过程状态，说明 CODRAX 是 running、done、timeout 还是 error。
- `gtestcov_events.ndjson`：关键事件流水。
- `codrax_final_outputs/0001_project_understanding.md`：本次 CODRAX final 输出记录。
- `codrax_final_outputs/index.json`：所有 CODRAX final 输出的索引。
- `codrax_native_logs/...log`：CODRAX 自己的 native log，运行期间用于判断 CODRAX 是否仍有活动。

## 本次暴露并修复的问题

### 问题 1：`gtestcov evidence` 缺少外层状态

现象：

- CODRAX 已经完成并写入 `codrax_status.json`、final output 和 index。
- 但 `gtestcov evidence` 运行目录中没有 `gtestcov_status.json`。
- 这会让外层状态查看不完整，用户或弱 AI 容易误以为 gtestcov 还在运行，或者不知道它卡在哪里。

修复：

- `generate_project_understanding()` 增加外层状态写入。
- 开始时写 `evidence.start`。
- 正常完成时写 `evidence.done`，并记录 `codrax_status`。
- 异常时写 `evidence.failed`。

修改文件：

```text
src/gtestcov/understanding.py
```

### 问题 2：需要测试覆盖长时间但仍有活动的 CODRAX

风险：

- 只测快速 fake CODRAX 不够。
- 只测 idle timeout 也不够。
- 必须覆盖一种情况：CODRAX 总耗时超过 idle timeout，但持续有 stdout、stderr 或 native log 活动，因此不能被误杀。

修复：

- 增加单元测试：`test_codrax_long_running_with_activity_completes_and_records_outer_status`
- 模拟 CODRAX 多阶段持续输出，运行时间超过 idle timeout，但最终正常完成。
- 验证：
  - `codrax_status.json` 为 `done/ok`
  - `gtestcov_status.json` 为 `evidence.done`
  - final output index 存在
  - final log 存在

修改文件：

```text
tests/test_core.py
```

## 测试结果

针对 CODRAX 状态与长跑相关测试：

```text
5 passed
```

全量测试：

```text
65 passed in 27.13s
```

## 重要结论

这次验证后，当前 gtestcov 对 CODRAX 长跑的处理结论是：

- 快速模拟测试用于防回归，可以继续保留。
- 真实 CODRAX 长跑完成场景已经验证通过。
- CODRAX 运行期间状态由 `codrax_status.json` 和 native log 活动共同体现。
- CODRAX 结束后，gtestcov 会写入自己的 final output、index、evidence 和外层状态。
- 不应再出现“CODRAX 已结束，但 gtestcov 一点日志都没有，也不知道还在干什么”的情况。

## 仍需注意

- WSL 每次命令仍会输出 localhost/NAT 乱码 warning。本次没有修复这个环境问题，因为它涉及系统/网络配置，不属于 gtestcov 工具逻辑。
- CODRAX native log 中可能包含大量内部分析文本，gtestcov 当前只把 final 输出和 tail 摘要纳入自己的日志体系，避免日志无限膨胀。
- 真实项目上仍建议优先查看：
  - `.gtestcov/runs/<run_id>/gtestcov_status.json`
  - `.gtestcov/runs/<run_id>/codrax_status.json`
  - `.gtestcov/runs/<run_id>/codrax_final_outputs/index.json`
  - `.gtestcov/runs/<run_id>/codrax_final_log.md`
