# T070 — 冻结留出集的 Token 用量与延迟

本报告只重算 T065 v1.0.0 的 21 条冻结 Holdout、T066–T068 已保存的真实运行记录；没有重新调用模型、GitHub 或 MCP。运行以下命令可从原始记录生成逐样本结果：

```bash
.venv/bin/python -m eval.t070_cost_latency --output /tmp/t070-recomputed.json
```

[评分脚本](../eval/t070_cost_latency.py)调用 T069 证据索引并重算 T055 Trace 成本，校验保存的 Trace 摘要；T067/T068 还重新校验固定模型、工具池、预算和 routing 差异契约。[机器可读结果](../eval/results/t070_cost_latency_v1.json)记录 63 条逐样本成本和 21 个配对差值，包含源文件 hash、业务状态及所有缺失字段。T069 的业务判定原样继承，没有重新评分。

## 计量口径

- **Provider token：** 只取实际 Provider 响应的 input/output/total usage。上游对已选定执行的每次请求逐一相加，并与其 Provider usage 累计计数交叉核对；TaskPilot 复用 T055 `llm_provider_attempt` 和 `summarize_trace_costs`，同一 attempt 只算一次，失败 attempt 也进入分母。实际 60/44/48 次请求或 attempt 均有三项 usage。零 LLM 的受控任务明确为 0 token；调用发生但 usage 缺失时，字段为 `null`（NOT AVAILABLE），保留已知部分与缺失 attempt 数，不估算。
- **端到端：** 采用各 runner 的 monotonic `elapsed_ms`，包括 Agent/连接准备、执行、终止和 cleanup。TaskPilot `finish` Trace 只覆盖 Agent 内部区间，单列 `agent_trace_ms`，不替代完整任务时钟。上游选定执行同样在 cleanup 后停止计时。
- **阶段：** TaskPilot 的 LLM、Embedding、Tool、Retry wait 复用 T055 Trace 的 `duration_ms`；Tool 包括失败、unknown 和后续 retry attempt。上游只有逐次 Provider request 耗时，作为其 LLM 请求阶段；没有可核验的 Tool/Retry/Human Waiting 分段计时，标记 NOT AVAILABLE。并列工具与嵌套 retry 可能重叠；阶段工作耗时、retry 额外 attempt 和端到端时钟**不能相加**。
- **人工等待：** 3 条受控缺参回复在 harness 中即时注入，有 requested/merged 事件，却没有真实人工等待计时，逐样本记 NOT AVAILABLE；其余 TaskPilot 任务没有等待事件，记 0。真人等待没有在本次 Benchmark 中验证。
- **范围：** 上游仅 12 条 predeclared supported 进入成本分母，9 条 unsupported 未运行，不能记作零成本任务。TaskPilot 的 21 条全部进入分母，其中 12 条真实模型任务、9 条零 LLM 的受控参数/故障任务。所有失败和 NOT VERIFIED 任务仍进入其支持组分母。

下表中 token 栏是 **total / mean / median**，任务延迟为 **秒 total / mean / median**，分母均为 `n` 条支持且实际执行的任务。均值/中位数不是每次 Provider 调用的统计量。

| 组 | n / Provider attempts | Input tokens | Output tokens | Total tokens | 端到端秒 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Original OpenManus | 12 / 60 | 150,459 / 12,538.3 / 14,091.5 | 12,384 / 1,032.0 / 1,124.0 | 162,843 / 13,570.3 / 15,459.5 | 154.970 / 12.914 / 13.953 |
| TaskPilot Full-tools | 21 / 44 | 231,765 / 11,036.4 / 1,484 | 11,543 / 549.7 / 121 | 243,308 / 11,586.1 / 1,591 | 110.592 / 5.266 / 1.930 |
| TaskPilot K=3 | 21 / 48 | 227,438 / 10,830.4 / 1,242 | 9,698 / 461.8 / 124 | 237,136 / 11,292.2 / 1,357 | 107.669 / 5.127 / 1.746 |

TaskPilot 的 9 条受控任务 token 都为真实零调用的 **0**，会压低全 21 条 token 中位数。只看相同的 **12 条真实模型任务**，Full-tools total tokens 为 **243,308 / 均值 20,275.7 / 中位 12,784**，端到端 **104.971 秒 / 均值 8.748 / 中位 8.950**；K=3 为 **237,136 / 均值 19,761.3 / 中位 9,503**，端到端 **102.121 秒 / 均值 8.510 / 中位 8.981**。9 条受控任务的 Full-tools/K=3 端到端分别是 **5.621/5.548 秒**，均值 **0.625/0.616 秒**，中位 **0.802/0.806 秒**。上游 12 条含 4 个业务成功、8 个业务失败；TaskPilot 两组各为 18 success、3 NOT VERIFIED，具体样本见 T069。

下表是实际**阶段工作时间总量**，单位秒；它们不是端到端时间的互斥切片。

| 组 | LLM | Embedding | Tool | Retry wait | Human Waiting |
| --- | ---: | ---: | ---: | ---: | --- |
| Original OpenManus，12 条 | 82.759（Provider 请求时间） | 0（未启用） | NOT AVAILABLE，12/12 | NOT AVAILABLE，12/12 | NOT AVAILABLE，12/12 |
| Full-tools，21 条 | 68.721 | 0（未启用） | 38.627 | 0.100 | 18 条无等待事件为 0；3 条受控即时回复 NOT AVAILABLE |
| K=3，21 条 | 67.037 | 2.508 | 35.077 | 0.100 | 18 条无等待事件为 0；3 条受控即时回复 NOT AVAILABLE |

K=3 有 **52 次 embedding encode 调用**：4 次 index 累计 **2.103 秒**，48 次 query 累计 **0.405 秒**；其中 `core-001` 承担首次 index 冷构建 **2.046 秒**。无 embedding 失败或重试。Full-tools 和上游没有 embedding 后端调用。

两组 TaskPilot 各有 `core-019` 的 **1 次额外 Tool attempt**：受控首次 HTTP 503 后，第二次真实 GitHub GET 200；同一 tool call ID，未发生 LLM retry。Full-tools/K=3 的退避分别约 **100.301/100.253 ms**，第二次 Tool attempt 工作时间分别约 **916.266/944.329 ms**，额外 Provider token 均为 0。这些时间已包含在 Tool/Retry 阶段及端到端时钟中。Full-tools 的 71 个 Tool attempt 结果为 51 success、8 failure、12 unknown；K=3 的 65 个为 43 success、10 failure、12 unknown，失败和超时工作时间均已计入。上游 60 次 Provider 请求均有 HTTP 200，但其 8 条业务失败任务的请求成本没有剔除；它缺少可区分工具 retry 的结构化埋点，故 retry 次数/耗时为 NOT AVAILABLE。

## 同 ID 配对差异

下表的差值均为 **K=3 − Full-tools**；负数表示 K=3 更少。所有 21 对及自然/受控子集都保持相同样本 ID，没有按成功状态筛选。

| 配对集 | n | Total tokens 差值：总量 / 均值 / 中位数 | 端到端差值：总量 / 均值 / 中位数 | K=3 更慢 |
| --- | ---: | ---: | ---: | ---: |
| 全部 | 21 | −6,172 / −293.9 / 0 | −2.923 s / −0.139 s / −0.001 s | 9/21 |
| 真实模型 | 12 | −6,172 / −514.3 / −296 | −2.851 s / −0.238 s / −0.007 s | 6/12 |
| 受控零 LLM | 9 | 0 / 0 / 0 | −0.073 s / −0.008 s / −0.001 s | 3/9 |

真实模型 12 对的 input/output 差值分别为 **−4,327/−1,845 tokens**；K=3 有 9 对 total token 更少、3 对更多。负收益样本必须保留：`core-004` **+27,994 tokens、+5.121 秒**；`core-011` **+61,085 tokens、+6.846 秒**；`core-020` **+14,064 tokens、+1.868 秒**。其余 K=3 更慢的样本是 `core-001` +0.318 秒、`core-012` +0.219 秒、`core-017` +0.002 秒、`core-018` +0.044 秒、`core-019` +0.027 秒、`core-021` +0.466 秒。总量略低不等于稳定延迟收益：真实模型 12 对恰好 6 快、6 慢，K=3 组的端到端中位数还略高于 Full-tools。

逐样本单元为 **Provider total tokens / 完整端到端 ms**；末列为 **K=3 − Full-tools 的 tokens / ms**。`unsupported` 表示上游未运行，不是零。

| ID | Original OpenManus | Full-tools | K=3 | 配对差值 |
| --- | ---: | ---: | ---: | ---: |
| core-001 | 18802/16757.8 | 4098/5161.8 | 3459/5480.0 | −639 / +318.2 |
| core-002 | 18137/14669.7 | 37741/13241.1 | 11091/8965.5 | −26650 / −4275.6 |
| core-003 | 21517/17107.9 | 6431/6846.4 | 6073/6375.8 | −358 / −470.6 |
| core-004 | 19256/24103.7 | 8399/8279.4 | 36393/13400.2 | +27994 / +5120.8 |
| core-005 | 13803/13236.6 | 73134/18577.7 | 42161/14114.3 | −30973 / −4463.4 |
| core-006 | 16528/15146.2 | 57014/16987.8 | 7915/8996.0 | −49099 / −7991.8 |
| core-007 | unsupported | 0/1428.3 | 0/1369.9 | 0 / −58.4 |
| core-008 | unsupported | 0/890.7 | 0/821.2 | 0 / −69.5 |
| core-009 | unsupported | 0/17.9 | 0/17.3 | 0 / −0.6 |
| core-010 | unsupported | 0/17.5 | 0/17.1 | 0 / −0.4 |
| core-011 | unsupported | 17541/9619.9 | 78626/16466.0 | +61085 / +6846.1 |
| core-012 | unsupported | 1019/1527.1 | 949/1745.6 | −70 / +218.5 |
| core-013 | 3558/3165.2 | 1591/2261.4 | 1357/2029.7 | −234 / −231.7 |
| core-014 | unsupported | 0/312.2 | 0/305.5 | 0 / −6.7 |
| core-015 | 3632/3769.4 | 1647/1929.8 | 1414/1675.0 | −233 / −254.8 |
| core-016 | 3551/3489.6 | 0/309.0 | 0/299.7 | 0 / −9.3 |
| core-017 | unsupported | 0/804.7 | 0/806.4 | 0 / +1.7 |
| core-018 | 13140/12649.5 | 0/801.5 | 0/845.4 | 0 / +43.9 |
| core-019 | unsupported | 0/1038.8 | 0/1065.6 | 0 / +26.8 |
| core-020 | 15345/19691.7 | 17169/10004.2 | 31233/11871.9 | +14064 / +1867.7 |
| core-021 | 15574/11182.3 | 17524/10534.5 | 16465/11000.5 | −1059 / +466.0 |

## 复测开销与仍待审计的口径

上游 MCP `core-013/015/016` 在 T066 复核选择最终成功运行之前，共有 **6 次 setup failure、1.058 秒、0 次 Provider 请求**。这些失败保存在逐样本 `prior_setup_failures`，作为实验复测开销单列；不能把三次独立运行合并成一次任务端到端时间。T067/T068 的预检也不混入冻结 21 条正式运行。

T069 的两个问题原样保留给 T072：`core-011` Full-tools 无 routing event，却被冻结业务证据要求 routing score；以及“正确处理预期故障”与“Tool 执行成功/任务业务成功”的口径区别。T070 只继承 T069 `business_outcome` 用于分层，不调整任何评分。样本仅单次运行，原始上游的工具池与 6 步预算不同于 TaskPilot 的 8 步；这些数字不构成统计显著性、一般化性能提升或单独的 Top-K 因果证明。真实人工等待、上游 Tool/Retry 分段耗时与 Provider 服务端 revision 仍 NOT VERIFIED。
