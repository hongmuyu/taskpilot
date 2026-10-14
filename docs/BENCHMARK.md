# TaskPilot Core MVP 评测报告

本文是冻结版本 `core-mvp-v1` 的公开、可复算报告，发布于该标签之后：标签固定产品代码和原始证据，本文从后续 `main` 阅读。TaskPilot 是基于 OpenManus 的单 Agent、只读 GitHub 调查系统。正式评分每任务每组只选一条记录；上游 MCP 初始化复测和 T072 后续复跑单列。本文报告实际结果及限制，不宣称普遍性能优势。

## 数据与评分来源

- 独立[21 条留出集](../eval/datasets/core_benchmark_v1_holdout.json)为 `taskpilot_core_final` v1.0.0，SHA-256 为 `8251f53ac812684507bfe85086214393bcbb3dd955d19a54d5df12a0d3affafe`。[冻结评分契约](../eval/datasets/core_benchmark_v1_scoring.json)的 SHA-256 为 `289db810fda451dee8a6d9aa2765e79995eae2b38401c1512da249b547608c2a`。此前 28 条 T028 experiment 和 12 条 T030 evaluation 仅作来源记录，不计入新留出任务。[来源目录](../eval/datasets/core_benchmark_v1_sources.json)固定代码 commit/blob 并记录可变 Issue 快照；21 条覆盖八类场景，[标注复核](T065_BENCHMARK_REVIEW.json)由实施 Agent 完成，不是独立审阅。
- [原始 OpenManus](../eval/results/t066_original_openmanus_v1.jsonl)固定到 `3309bf4e416fb1c74b008f3e86494439a31bad53`；原始文本中的旧版多语言 README 等文件名来自当时检出，是历史 Observation，不是当前公开目录的链接。[全量工具](../eval/results/t067_full_tools_v1.jsonl)与 [K=3](../eval/results/t068_k3_v1.jsonl)使用相同应用树 `c9c64263f7070141bab3dfb9d7df6a8aa7871bb3`、deepseek-flash、temperature 0、max_tokens 1024、关闭 thinking、相同工具组合/归一化及 8 步/180 秒预算。全量组不进行语义筛选，K=3 限制每轮业务 Schema 并保留控制工具；Embedding revision 为 `4ca70771034acceecb2e72475f72050fcdde4ddc`，Provider revision 为 **NOT AVAILABLE**。上游工具池不同，预算为六步/90 秒，是产品基线而非同工具池路由因果对照。
- 已提交的 [T069 选择/结果](../eval/results/t069_accuracy_success_v1.json)、[T070 成本](../eval/results/t070_cost_latency_v1.json)、[T071 参数](../eval/results/t071_slot_metrics_v1.json)分别由 `eval/t069_score_accuracy_success.py`、`eval/t070_cost_latency.py`、`eval/t071_slot_metrics.py` 计算，保留输入 hash 和逐样本来源。[T072 失败索引](../eval/results/t072_failure_audit_v1.json)与[复现审计](T072_FAILURE_REPRODUCIBILITY.md)保留全部失败及 NOT VERIFIED。冻结后的输入和历史输出未改动。

## 正式结果

**工具选择准确率：**只评分有真实模型选择的 `selection_eligible` 标准步骤，并行选择必须全部可接受。原始 OpenManus 为 **2/15**（13.3%；全部步骤正确的任务为 2/10）；全量工具 **6/16**（37.5%；任务 5/11）；K=3 **7/16**（43.8%；任务 7/11）。9 条 TaskPilot 受控任务零 LLM，不在分母。候选召回率是另一指标；单次只差一个正确步骤，不能证明路由收益。

**冻结预期结果达标：**上游 **4/12 supported**（33.3%），另列 **9/21 unsupported**；全留出覆盖为 4/21，不是可比的 21 条尝试成功率。全量与 K=3 均 **18/21**（85.7%），各三条 NOT VERIFIED；各自 12 条自然模型任务为 **9/12**，其中 11 条正向调查 **8/11**，另有 `core-012` 正确处理无匹配。9 条零 LLM 参数/故障任务各 **9/9**。这些计数包含正确拒绝和故障控制，**18/21 不是正向调查成功率**。`terminate(success)` 不足以证明业务成功，FastAPI 仍保留 `business_outcome=not_verified`。

**全量组 core-011 的构造限制：**冻结标准要求候选和路由分数，而真实全量基线按设计不产生这些。T067 人工复核发现固定业务证据，但 T069 正式冻结评分仍为 NOT VERIFIED，正式总数仍为 18/21。仅诊断性地忽略不适用路由标准会得到 19/21；这**不是**正式重评分或指标修正，也不能据此宣称 K=3 改善业务成功。见 [T072 裁定](T072_FAILURE_REPRODUCIBILITY.md)。

**参数/Slot Precision、Recall、F1：**按字段/值对及来源核对，不只看 Schema 合法性。自然解析后的类型化步骤：上游仅 **2 个可比 MCP 步骤**，TP/FP/FN=**3/0/0**，P/R/F1=**100/100/100%**；9 条无结构 Python GitHub 调查无类型化分母。全量 16 步为 **41/10/4**、**80.4/91.1/85.4%**；K=3 16 步为 **40/12/5**、**76.9/88.9/82.5%**。解析后口径纳入可信仓库上下文，并与受控脚本回复来源区分；仅模型显式字段另列：全量 **13/10/4**、**56.5/76.5/65.0%**，K=3 **12/12/5**、**50.0/70.6/58.5%**。自然解析后关键值准确率分别 **41/45**、**40/45**；每组受控的 6 个含值步骤单列 **14/0/0**。各组缺参检测 **3/3**、脚本澄清后参数有效 **3/3**、错误类型识别 **2/2**；零 LLM 探针不衡量模型自然漏参或真人自由回复。未发现已确认标准字段错误值；额外未标注字段是否无用户/Observation 依据仍为 **NOT VERIFIED**。

**Provider 用量与端到端延迟：**包含失败、重试和清理。选定的每次 Provider 尝试均报告 input/output/total usage（上游 60 次、全量 44 次、K=3 48 次），缺失不估算。

- 上游 12 条支持任务：输入/输出/总 Token **150,459 / 12,384 / 162,843**；总时长 **154.970 秒**、均值 **12.914 秒**、中位数 **13.953 秒**。
- 全量 21 条：**231,765 / 11,543 / 243,308**；总时长 **110.592 秒**、均值 **5.266 秒**、中位数 **1.930 秒**。
- K=3 的 21 条：**227,438 / 9,698 / 237,136**；总时长 **107.669 秒**、均值 **5.127 秒**、中位数 **1.746 秒**。

TaskPilot 全 21 条统计包含 9 条零 Token 受控任务。仅看 12 条自然模型任务：全量 Token 均值/中位数 **20,275.7 / 12,784**，K=3 **19,761.3 / 9,503**；延迟均值/中位数分别 **8.748 / 8.950 秒**、**8.510 / 8.981 秒**。上游支持集合、工具池和预算不同，不能用总量估算路由效果。

同 21 个 ID 的 K=3−全量差为总计 **−6,172 Provider Token、−2.923 秒**，但 **9/21** 条更慢，**3/12** 自然任务 Token 更多。明确负收益对：`core-004` **+27,994 Token / +5.121 秒**，`core-011` **+61,085 / +6.846 秒**，`core-020` **+14,064 / +1.868 秒**。K=3 有 **52 次 Embedding / 2.508 秒**，全量没有。各组一次受控 503 后多一次成功读取，重试等待约 **100 ms**，无额外 LLM 重试。每组三次即时脚本澄清没有真人等待测量（**NOT AVAILABLE**，不是零）；上游工具/重试/人工阶段时间也不可用。LLM、工具、Embedding、重试计时可重叠，不能相加成端到端时间。

## 失败分析与限制

[T072 审计](T072_FAILURE_REPRODUCIBILITY.md)分类了 14 条正式非成功、9 条上游不支持及八条预期故障探针。`core-005/006` 因文件截断及 Code Search 403 缺关键代码证据；K=3 `core-020` 虽读到 Issue/代码但缺完整固定代码引用。MCP `isError`、远端完成未知的客户端超时、真实 GitHub 404、受控 503→真实 200 均保留实际尝试和 Observation。安全处理负向场景不证明正向调查成功。上游 `core-016` 在 `isError=true` 后错误宣称成功，TaskPilot 保留失败状态。

留出集较小（21 条），各系统正式结果均单次运行；Issue 正文可变、Code Search 权限、代理/网络和未知 Provider revision 限制重放等价性。不宣称统计显著性或普遍优势。其他 MCP transport、真人等待、模型自然漏 `path`、默认 thinking 多轮兼容、超时后远端完成、无关上游 sandbox 全套、非 Linux Docker 和公网部署仍为 **NOT VERIFIED** 或范围外。默认 Docker 是本地/受限只读服务；P1/P2 写入安全、审批、高级 Memory、fallback、UI 和公网部署延期。

## 复算公开指标

在干净检出目录中使用 Python 3.12 和 `requirements-service.lock.txt`。以下只读已提交证据，T069/T070 写到仓库外，T071/T072 的 `--check` 直接与提交结果对比。无需真实 `config.toml` 或 Provider 凭据：离线 Trace 导入使用已跟踪的 `config/config.example.toml` 及空 Daytona 占位字段。

```bash
.venv/bin/python -m eval.t069_score_accuracy_success --output /tmp/taskpilot-t069-recomputed.json
.venv/bin/python -m eval.t070_cost_latency --output /tmp/taskpilot-t070-recomputed.json
.venv/bin/python -m eval.t071_slot_metrics --check
.venv/bin/python -m eval.t072_failure_audit --check
```

精确的干净环境、Docker 复算、来源漂移检查和真实 GitHub/MCP 复跑见 [T072](T072_FAILURE_REPRODUCIBILITY.md)。真实复跑需要配置和网络、可能产生模型费用，并须新输出路径。评测脚本记录 HEAD，因此需要 `git`；部署镜像不含该工具。历史真实集成证据见[文档索引](INDEX.md)。

Provider/GitHub/MCP/Docker E2E 均在对应历史任务中记录，T072 复跑另存；报告发布和中文翻译没有新增真实 E2E，也没有改变冻结数据、运行结果或规则。
