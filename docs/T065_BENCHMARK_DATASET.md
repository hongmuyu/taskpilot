# T065 — Core 最终评测数据集 v1.0.0

> 本文是历史数据冻结记录。后续三组正式结果见 [Core MVP 评测报告](BENCHMARK.md)。

本文记录**冻结数据和评分契约**，不是评测结果。在此时点尚未执行本留出集上的原始 OpenManus、全量工具或 TaskPilot 评测；T029/T030 只属于路由阶段证据。

## 文件与数据划分

- [留出集](../eval/datasets/core_benchmark_v1_holdout.json)：21 条新任务，`taskpilot_core_final` v1.0.0，角色为 `independent_holdout`。
- [JSON Schema](../eval/datasets/core_benchmark_v1.schema.json)：统一定义八类任务的必需字段、类型和枚举。
- [来源目录](../eval/datasets/core_benchmark_v1_sources.json)：2026-10-11 UTC 采集，固定 GitHub commit/blob，记录可变 Issue 标题/正文 hash 与 `updated_at`、5 个 Local 工具 Schema hash，以及真实 stdio MCP v1/v2 发现结果。
- [评分契约](../eval/datasets/core_benchmark_v1_scoring.json)：运行前冻结，留出集引用其 SHA-256。
- [标注复核](T065_BENCHMARK_REVIEW.json)：逐样本判断由实施 Agent 完成，不是独立外部审阅。

留出集记录此前 T028 的 28 条 `routing_v1_experiment`（用于选择 K）和 12 条 `routing_v1_evaluation`（用于 T030）的 SHA-256、版本与用途。这 40 条只是**来源记录**，不计新留出样本。新任务使用 `encode/httpx`、`pydantic/pydantic`、`Textualize/rich` 及临时只读 MCP Server；T028 使用其他仓库，准确任务提示和仓库 ID 无重叠。不得看过结果后重调 K、重写标签或筛选样本；变更须新增数据/评分版本并说明原因。

| 类别 | 样本数 |
| --- | ---: |
| 仓库理解 | 2 |
| Issue 调查 | 2 |
| Issue → 源码 | 2 |
| 缺失/非法参数 | 4 |
| 语义路由 | 2 |
| 真实 MCP | 3 |
| 失败/超时/重试 | 4 |
| 多步调查 | 2 |

每条记录任务、确认的仓库/ref 或 MCP 上下文、工具组合、准确步骤、多值可接受公开工具名、关键参数值与来源、预期动作/执行次数/尝试次数/状态、业务证据、失败和不支持条件。`controlled_tool_call` / `controlled_fault` 显式标明；这是校验和可靠性探针，**不证明模型自然漏参或外部服务自然故障**。无匹配和澄清步骤设 `selection_eligible=false`。

## 来源稳定性与工具组合

GitHub 文件证据必须读取目录中固定的 40 位 commit SHA，并在评分前核对 blob SHA。即使仓库 ref 固定，Issue 端点仍可变；`updated_at` 与正文 SHA-256 定义快照。发生漂移时标记受影响样本过期/`NOT VERIFIED` 并报告，不静默使用新正文或改标签；重新采集需要新版本。代码搜索是 GitHub 实时索引发现调用，不是固定内容来源，最终代码证据须来自固定提交的文件读取；认证是预检能力条件。

Local 组合为当前 5 个 GitHub 读取工具及原始 `BaseTool.parameters` hash。MCP 组合采用 FastMCP 1.5.0 stdio 真实发现的公开名、原名和 `inputSchema`，记录服务源码 SHA-256 与 `TASKPILOT_MCP_VARIANT` v1/v2。测试服务是真实临时进程，不是 Mock 响应。`terminate` 独立于业务工具池，强制澄清发生在执行层。原始 OpenManus 可能不支持所有组合，必须事先声明并逐基线报告，不能假装具备工具。

## 冻结评分解释

JSON 评分契约为准。候选召回率与最终工具选择准确率以支持、已尝试、`selection_eligible=true` 且 `expected_action=tool` 的**步骤**为分母。命中任一可接受标签即工具可接受；错误或没有选择为错误。澄清、拒绝、无匹配不进入该分母，其安全和任务结果另评。

任务成功率以支持且已尝试的**任务**为分母，failure 和 NOT VERIFIED 作为非成功保留。全部指定业务证据必须能与真实 Tool、HTTP、MCP、Trace 和引用回答独立核对，`terminate(success)` 只是 Agent 声明。负向任务仅在预期安全行为和证据满足时成功。事先声明 unsupported 的用例不进入可比分母，单独计数；未尝试项也须报告。比较时展示共同支持子集与各自能力覆盖。

参数 precision/recall/F1 使用支持、已尝试步骤的关键 `(name, normalized value)` 对。回复前明确 `missing` 和输入 `invalid` 值不进入值对标准集合；安全澄清/拒绝及零执行按执行条件计。来源另行核对。查询标准包含标识词，忽略大小写、顺序任意、允许有信息量的额外词；相关性仍需要来源证据。无法定义的 precision/recall 为 `NOT_APPLICABLE`，保留 TP/FP/FN，不造零或一。

只有 Provider 返回的 input/output/total usage 是实测用量；缺失为 `unknown`，估计单列。每次 LLM 调用只计一次，包括有 usage 的付费失败/重试。端到端时间用单调时钟从受理到终态**及清理**，包括 LLM、Embedding、工具、重试/退避、失败、超时、取消和澄清等待。阶段计时可能重叠，不能简单相加。

## 运行前检查与限制

评测前的隔离检查命令：

```bash
.venv/bin/python -m pytest -q tests/taskpilot/test_final_benchmark_dataset.py
```

它检查引用/hash、Local Schema 与真实 stdio 发现，**不执行评测任务**。正式运行还须记录 TaskPilot/基线 revision、模型/配置、K、工具池、超时、脚本版本、真实响应及所有失败尝试；来源/能力变化按冻结状态规则处理。受控 503 要求脚本仅注入第一次响应，随后执行真实固定 GitHub 读取。在本文历史时点，该脚本及三组正式运行尚为 **NOT VERIFIED**，留待后续任务。
