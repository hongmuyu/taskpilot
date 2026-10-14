# T067 — 同工具池全量工具基线

> 本文为历史原始运行记录。T069–T071 后续才完成评分；当前汇总和限制见[正式评测](BENCHMARK.md)。

这是冻结 [T065 v1.0.0 留出集](../eval/datasets/core_benchmark_v1_holdout.json)上的 **TaskPilot 全量工具执行组**，与[原始 OpenManus T066](T066_ORIGINAL_OPENMANUS.md)分开。本文记录原始运行与逐任务判断，当时尚未计算 T069–T071 对比指标。数据集、来源目录和[评分契约](../eval/datasets/core_benchmark_v1_scoring.json)未改变。

## 固定运行配置

- 应用树：`021f23ce7db4e24fb65c16fd82716c2c9467631f:app`。21 条任务各用独立 Agent、LLM 客户端、Memory、仓库上下文、ToolCollection 和 Trace；执行前校验冻结工具名称和 Schema。
- 模型：`api.deepseek.com` 的真实 `deepseek-flash`，temperature `0`、`max_tokens=1024`、关闭 thinking；Provider revision 为 **NOT AVAILABLE**。环境为 Linux、Python 3.12.13、MCP SDK 1.5.0、HTTPX 0.28.1。只检查凭据存在状态，不记录值。
- 预算：8 步、任务 180 秒、模型请求 45 秒、GitHub HTTP 20 秒、MCP 连接及 Agent 清理 5 秒、MCP 清理 2 秒；`core-017` 单个 MCP 工具采用冻结的 50 ms 截止时间。
- GitHub 组合：当前 5 个只读 Local 工具与 `terminate`，使用冻结仓库/ref；MCP v1/v2 组合：5 个真实发现的 stdio 读取工具与 `terminate`，服务源码及 `inputSchema` 匹配 T065，无写工具。相同工具组合、模型、任务/Observation、控制工具和预算成为 T068 的固定对照契约。
- `routing_top_k=None`、`routing_retriever=None`，不创建 Embedding 索引或检索器。审计拦截**每次实际 `LLM.ask_tool`**，将序列化 Schema 与完整当前 ToolCollection 对比。**44/44 次 LLM 调用**均发送包含 `terminate` 的 6 个 Schema，零路由/Embedding 事件。9 条受控边界用例不调用 LLM，标为 `NO_LLM_TOOL_CALL`，不进入 44 次分母。
- 运行前来源复核：**8/8 Issue** 正文/更新时间、**11/11 固定文件** blob hash 匹配 T065。来源复核 HTTP 与任务执行审计分开；逐任务请求、Trace、Provider usage、耗时及清理保留在原文件。

## 实际执行与复核

**21/21** 条均执行，无预检不支持项。[逐样本复核](../eval/results/t067_full_tools_review.json)按冻结行为和业务证据判断为 **19 success、2 NOT VERIFIED**；这是处置计数，不是正式任务成功率或优势声明。21 条 Trace 均通过校验，21 次清理完成，均在记录的预算内返回。

12 条自然任务调用真实 Provider；9 条冻结参数/故障用例在 `ToolCallAgent` 执行边界注入有标识的调用，**不验证模型自然漏参或真人等待**。`core-007/008/014` 通过已有合并/重校验路径提交冻结回复：回复前零执行，回复后原调用执行一次，保留原 ID。`core-009/010` 校验失败，零 GitHub 请求。

`core-013`–`017` 记录真实 MCP stdio v1/v2：两个自然 echo 和一个澄清 echo 返回预期值；`core-016` 一次执行后的 `isError=true` 成为失败 Observation；`core-017` 真实慢 Server 在 50 ms 客户端截止时间后返回 `unknown`/timeout，Server 执行一次，无重试且有界清理，不推断远端完成。`core-018` 的真实固定 GitHub 404 为永久失败、一次尝试。`core-019` 首次 503 为**受控注入**，第二次为同工具调用下的真实 GitHub 200。任务 HTTP 审计共 56 次 200、1 次 404、1 次受控 503、4 次 403、1 次 422；失败状态全部保留。

两条 Issue → 源码为 **NOT VERIFIED**：`core-005` 在读工具截断 `rich/cells.py`、未到 `chop_cells` 循环后耗尽 8 步；`core-006` 读取 `pydantic/_internal/_validators.py` 时截断隐藏了 `decimal_places_validator`。调查中 Code Search 还返回 403。两者虽获取 Issue 并引用固定文件，但缺关键实现证据；`core-006` 的 Agent 成功声明不计业务成功。

`core-011` 有有效 Issue/代码结果，当时复核认为全量组不执行候选排名，路由分数标准不适用；后续正式评分的口径裁定见 T069/T072。`core-012` 安全拒绝 shell/环境请求，无业务执行；只读工具组合不支持 shell。`core-020` 虽有额外失败调用，仍获取所需 Issue/代码证据，失败保留在 Trace。

初始 MCP 预检暴露**仅执行脚本层**的问题：前一受控任务关闭了共享 OpenManus LLM 单例客户端，使自然任务 `core-013` 在到达 Provider 前失败。首跑、独立成功复测、失败复现和修正后的序列均保留。脚本改为每样本独立 LLM 客户端、模型配置不变；最终 21 条无初始化/客户端复用错误，没有修改产品运行时或冻结数据。

## 证据与复现

- [最终 21 条原始记录](../eval/results/t067_full_tools_v1.jsonl)与[逐条复核](../eval/results/t067_full_tools_review.json)
- [初始预检](../eval/results/t067_full_tools_preflight.jsonl)、[独立 MCP 复测](../eval/results/t067_full_tools_preflight_retry_1.jsonl)、[失败复现](../eval/results/t067_full_tools_preflight_retry_2.jsonl)、[修正后预检](../eval/results/t067_full_tools_preflight_fixed.jsonl)
- [执行脚本](../eval/t067_full_tool_baseline.py)与[完整性测试](../tests/taskpilot/test_t067_full_tool_baseline.py)

在已配置的 TaskPilot 检出目录中，使用锁定 Python 环境、有效模型配置，以及按原有凭据机制提供的可选 GitHub 只读 Token：

```bash
.venv/bin/python -m eval.t067_full_tool_baseline \
  --output /path/to/new/t067_full_tools.jsonl
```

替换输出路径；每次运行必须新建文件。脚本复核冻结数据/评分/来源 hash、MCP 服务与真实来源快照，记录失败而不丢弃行，保存前脱敏 Trace ID 和工具参数。跨组准确率、参数、Token 和延迟留待 T069–T071。

## 剩余限制

- Provider revision 与外部提供方的确定性重放：**NOT VERIFIED**；固定温度和参数不能保证远端模型不变。
- `core-005/006` 的实现证据因截断与 Code Search 403 为 **NOT VERIFIED**，在后续分母保留为非成功。
- 模型自然漏参、真人交互回复为 **NOT VERIFIED**；受控用例实际覆盖真实合并/重校验及 HTTP/MCP 路径。
- 未观察到自然发生的 503；`core-019` 明确区分受控首响应与真实第二次读取。
- 关闭路由的组没有候选分数，符合真实全量基线，不代表发生过检索。
