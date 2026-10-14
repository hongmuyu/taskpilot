# T068 — TaskPilot K=3 对照运行

> 本文为历史原始运行记录。T069–T071 后续才完成评分；当前汇总和限制见[正式评测](BENCHMARK.md)。

这是未改动的 [T065 v1.0.0 留出集](../eval/datasets/core_benchmark_v1_holdout.json)上的 **K=3 原始执行组**，与[原始 OpenManus T066](T066_ORIGINAL_OPENMANUS.md)、[同工具池全量组 T067](T067_FULL_TOOL_BASELINE.md)分开。[冻结评分契约](../eval/datasets/core_benchmark_v1_scoring.json)未改变，当时尚未计算正式准确率、任务成功率、参数、Token 和延迟对照。

## 固定执行契约

- 产品应用树：`c9c64263f7070141bab3dfb9d7df6a8aa7871bb3`，与 T067 相同。T068 HEAD 为 `d65e303a10375802dcb1b0cfa11bab525177a3a2`；与 T067 HEAD 的差异仅是已提交的评测脚本、测试和证据，不含 `app/` 改动。
- 真实 Provider：`api.deepseek.com` 的 `deepseek-flash`，temperature `0`、`max_tokens=1024`、关闭 thinking；revision 为 **NOT AVAILABLE**。Python 3.12.13、MCP SDK 1.5.0、HTTPX 0.28.1 与 T067 一致。
- 工具组合：相同的 5 个 GitHub 只读 Local 工具，或 v1/v2 的 5 个真实 stdio 测试工具，加 `terminate`。**21/21** 条名称与 Schema hash 均匹配 T067；每条有独立 Agent、LLM 客户端、Memory、仓库上下文、ToolCollection 和 Trace。
- 预算：8 步、Agent 180 秒、模型请求 45 秒、GitHub HTTP 20 秒、MCP 连接及 Agent 清理 5 秒、MCP 清理 2 秒；仅 `core-017` 使用冻结的 50 ms 工具截止时间。运行前后核对相同任务文字/hash、仓库/ref、Observation 归一化、受控回复、503 注入与校验/执行边界。
- **仅改变候选筛选：**真实模型回合启用 `routing_top_k=3` 及已有 `SemanticToolRetriever` / `InMemoryToolIndex`。本地模型为 `sentence-transformers/paraphrase-MiniLM-L3-v2`，revision `4ca70771034acceecb2e72475f72050fcdde4ddc`，向量版本 `tool-metadata-json-v1/cosine-v1/mean-pool-128-v1`。隔离 Agent 按顺序共享一个内存索引和后端，首次自然任务包含加载/建索引成本；工具池切换会重建索引，事件保留准确元数据指纹和模型/向量版本。
- 运行前来源复核：**8/8 Issue** 正文/更新时间及 **11/11 固定文件** blob hash 匹配 T065；来源核对 HTTP 与任务流量分开。

## 实际 21 条运行

**21/21** 条均执行，包含 12 条真实 Provider 自然任务及 9 条经实际执行边界的受控参数/故障用例，无预检不支持项。[逐条证据复核](../eval/results/t068_k3_review.json)为 **18 success、3 NOT VERIFIED**。安全拒绝、MCP 失败、超时、GitHub 404 只有在对应安全用例中满足预期才算成功；该计数不是正式评测率或 K=3 优势证明。

**48/48 次实际 LLM 工具调用请求**均发送 3 个业务 Schema 和 `terminate`，各有候选身份、相似度、索引版本、选中名称/ID、业务及总 K 的路由事件；校验与执行仍使用完整 ToolCollection。顺序跨 GitHub、MCP v1、MCP v2 再到 GitHub，共 48 次查询 Embedding、4 次索引重建。逐任务保留 Provider usage、Embedding 阶段/耗时、Trace、失败与清理；21 条 Trace 校验通过、21 次清理完成。9 条受控用例**不调用 LLM 或语义检索**，标为 `NO_LLM_TOOL_CALL`，不冒充模型自主选择。

[原始运行](../eval/results/t068_k3_v1.jsonl)中的真实行为：

- `core-007/008/014`：冻结回复前零 Tool/Server 执行，已有合并/重校验保留原 `tool_call_id`，之后原调用恰好执行一次。`core-009/010` 拒绝畸形 JSON/布尔型 `issue_number`，零 GitHub 请求。
- `core-013/015`：模型从 K=3 中选择真实 MCP echo，各一次 Server 执行并返回预期值。`core-016` 的真实 `isError=true` 成为失败，无重试。`core-017` 慢 Server 执行一次，客户端超时记 `unknown`，有界清理，不声称远端取消。
- `core-018`：真实固定 GitHub 404，一次尝试、永久失败。`core-019`：首个 503 为**受控**，第二次是同调用下真实 GitHub 200。HTTP 审计共 49 次 200、7 次 403、1 次真实 404、1 次受控 503；403 保留为失败。
- `core-012`：虽暴露低相似度业务候选，模型选择 `terminate`，无业务执行或环境披露。现有 Top-K 不含拒绝全部正分弱匹配的阈值，不能声称具备此功能。

保留的非成功为 `core-005/006/020`：`005` 在 `rich/cells.py` 被截断于关键循环之前、Code Search 403 后耗尽八步；`006` 获取 Issue，但三次 Code Search 403，未读取固定 validator 文件；`020` 读取真实 Issue 和固定 `rich/ansi.py`，最终解释解析路径，但缺完整固定代码 URL，且引用评论无直接评论链接，因此冻结引用要求为 NOT VERIFIED。

`core-011` 达到步数上限，第一次 `terminate` 失败后再次终止；仍保留有分数的路由事件及固定源码/测试引用，故原始证据复核记成功。额外调用、403 与失败尝试未为美化 K=3 而删除。

## 三组完整性与复现

- [T066 原始 21 行](../eval/results/t066_original_openmanus_v1.jsonl)及[复核](../eval/results/t066_original_openmanus_review.json)：12 条支持并尝试，9 条不支持，工具池不同。
- [T067 manifest 与 21 行](../eval/results/t067_full_tools_v1.jsonl)及[复核](../eval/results/t067_full_tools_review.json)：相同 TaskPilot 应用树/工具组合/预算，关闭语义筛选。
- [T068 manifest 与 21 行](../eval/results/t068_k3_v1.jsonl)、[五条真实预检](../eval/results/t068_k3_preflight.jsonl)、[逐条复核](../eval/results/t068_k3_review.json)、[脚本](../eval/t068_k3_baseline.py)及[完整性测试](../tests/taskpilot/test_t068_k3_baseline.py)。

三组均含相同 21 个冻结 ID。T067/T068 的应用树、环境、模型/采样、工具组合、任务 hash、上下文、受控输入、归一化和预算一致，仅语义筛选及 Embedding/索引成本不同。T066 工具池和预算不同，是原始上游产品基线，不能当作 Top-K 因果对照。

T068 manifest 固定脚本 hash、T067 manifest hash、来源/服务 hash、模型/向量版本及实测环境。逐行保留调用、路由、usage、Embedding 尝试、耗时、Trace 和清理；JSONL 增量写入，失败及 NOT VERIFIED 不丢弃。

独立复跑需配置好的检出环境，通过现有凭据机制提供有效配置，并使用新路径：

```bash
.venv/bin/python -m eval.t068_k3_baseline \
  --output /path/to/new/t068_k3.jsonl
```

脚本排他创建文件，检查未改变的 T065/T067 契约和真实 GitHub 来源标记；工具 Schema、模型、应用树、预算或环境变化时，在记录任务前拒绝执行。

## 剩余限制

- 仅一次完整 21 条 K=3 运行与五条预检；统计可重复性和未报告的 Provider revision 为 **NOT VERIFIED**。T067/T068 之间 Code Search 限流变化，原始 HTTP 状态保留其影响。
- `core-005/006/020` 为 **NOT VERIFIED**，Agent 完成声明不能转换成业务成功。
- 受控边界用例不能证明模型自然漏参或真人等待；真实工具和澄清合并/重校验路径已执行。
- 内存索引仅在本次顺序运行内共享，未评测其他调度/顺序及 Embedding 冷下载；查看 Holdout 结果后未重调 K。
- T066 能力和预算不同；公共子集评分及正式准确率/成功率/成本对照留待 T069–T071，不能从原始处置计数推断。
