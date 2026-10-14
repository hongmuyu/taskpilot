# T069 — 冻结留出集的准确率与预期结果达标率

本文是冻结 Core Holdout v1.0.0 的 21 条任务、单次运行评分。精确逐样本结果可通过下列命令复算：

```bash
.venv/bin/python -m eval.t069_score_accuracy_success --output /tmp/t069-recomputed.json
```

评分脚本核对冻结数据/评分 hash、三组原始运行与裁定文件、唯一 ID、选定的上游 MCP 复测指针及 TaskPilot Trace/清理有效性。[63 条机器可读判断](../eval/results/t069_accuracy_success_v1.json)包含每个标准步骤、可接受工具集合、模型所选名称/ID、原始证据指针、来源复核、业务结果和原因，不复制原始参数或提示。T066 初始化失败仍保留，评分使用此前已选定的成功 MCP 复测。

## 冻结评分规则的应用

- **自主工具选择准确率：**分母只含支持、已尝试任务上有真实模型回合的 `selection_eligible=true` 步骤。标准步骤 `index=N` 对齐实际 assistant 第 N 回合，不因结果事后重排绕路步骤。并行选择的每个工具都须属于该步多值 `acceptable_tools`；额外错误工具或未选择都记错。重复选择可接受工具仍仅计该步骤一次。澄清、拒绝、无匹配及 9 条零 LLM 受控任务不进入分母；该指标不是候选召回率。
- **任务级选择诊断：**含可评分步骤的支持任务计一次，仅当全部这些步骤正确才通过；补充而不替代步骤级主指标。后续未标注调用保留为 `unlabelled_followup_turns`，不临时创造标签或分母。
- **预期结果达标：**支持且已尝试的任务，在所有冻结业务证据核实且无失败条件时计成功；failure 与 NOT VERIFIED 留在分母。unsupported 单列，并展示全 21 条覆盖率；`terminate(success)` 不能充当业务证据。
- **判断来源：**T066–T068 复核提供任务级裁定，评分脚本校验来源 hash/ID 与 Trace 完整性。发现与冻结标准冲突时，公开记录并修正 T069 评分，不改旧复核、留出集、规则或原始运行。

| 分组 | 自主选择正确步骤 | 全部合格步骤正确的任务 | 支持任务的预期结果达标 | 全 21 条覆盖 | 业务结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| 原始 OpenManus | 2/15 (13.3%) | 2/10 | 4/12 (33.3%) | 4/21 | 4 success, 8 failure, 9 unsupported |
| TaskPilot 全量工具 | 6/16 (37.5%) | 5/11 | 18/21 (85.7%) | 18/21 | 18 success, 3 NOT VERIFIED |
| TaskPilot K=3 | 7/16 (43.8%) | 7/11 | 18/21 (85.7%) | 18/21 | 18 success, 3 NOT VERIFIED |

下表每格为 `业务结果；正确/可评分自主步骤`。横线表示没有可评分自主步骤；保留全部 21 个冻结 ID。

| 样本 | 原始 OpenManus | 全量工具 | K=3 |
| --- | --- | --- | --- |
| core-001 | success; 0/1 | success; 0/1 | success; 1/1 |
| core-002 | failure; 0/1 | success; 0/1 | success; 1/1 |
| core-003 | success; 0/1 | success; 1/1 | success; 1/1 |
| core-004 | failure; 0/1 | success; 1/1 | success; 1/1 |
| core-005 | failure; 0/2 | NOT VERIFIED; 1/2 | NOT VERIFIED; 0/2 |
| core-006 | failure; 0/2 | NOT VERIFIED; 0/2 | NOT VERIFIED; 0/2 |
| core-007 | unsupported; — | success; — | success; — |
| core-008 | unsupported; — | success; — | success; — |
| core-009 | unsupported; — | success; — | success; — |
| core-010 | unsupported; — | success; — | success; — |
| core-011 | unsupported; — | NOT VERIFIED; 1/1 | success; 1/1 |
| core-012 | unsupported; — | success; — | success; — |
| core-013 | success; 1/1 | success; 1/1 | success; 1/1 |
| core-014 | unsupported; — | success; — | success; — |
| core-015 | success; 1/1 | success; 1/1 | success; 1/1 |
| core-016 | failure; — | success; — | success; — |
| core-017 | unsupported; — | success; — | success; — |
| core-018 | failure; — | success; — | success; — |
| core-019 | unsupported; — | success; — | success; — |
| core-020 | failure; 0/2 | success; 0/2 | NOT VERIFIED; 0/2 |
| core-021 | failure; 0/3 | success; 0/3 | success; 0/3 |

## 原始证据人工抽查

- **core-005/006：**两组 TaskPilot 均获取 Issue。全量组读取固定文件，但关键代码在截断之后且 Code Search 403；K=3 同样缺充分固定代码证据。要求的代码/引用为 NOT VERIFIED。上游六步运行未获得 Issue 到源码的关联证明。
- **core-011：**K=3 有路由候选、分数和执行结果；全量组按设计零路由事件，但固定搜索/读取支持回答。T067 复核为 success，冻结 `trace` 标准却明确要求候选与分数，所以 T069 公开记为 NOT VERIFIED，旧复核与标签不改。这是路由专属用例的跨基线可比性问题。
- **core-016/017：**各 TaskPilot 运行均一次真实 stdio Server 执行。`isError` 保留失败执行/Observation，慢调用为 unknown timeout、一次尝试，不声称远端取消。上游 `016` 在 `isError` 后错误宣称成功，复核仍为 failure。
- **core-018/019：**404 各一次真实 GitHub GET 和失败 Observation；503 各一次受控临时失败后真实 200，同一调用下两次尝试，不把额外重试单独计成功。
- **core-020：**全量组的固定 Issue/代码引用支持回答；K=3 虽读取文件，最终代码 URL 与评论引用不完整，仍为 NOT VERIFIED。两组早期并行/绕路选择均偏离标准步骤，上游缺必要证据。
- **core-021：**两组 TaskPilot 虽早期并行选择、绕路，最终取得所需关联证据，说明任务结果与严格步骤选择是不同指标。

抽查使用原 HTTP 状态/路径、MCP 执行审计、Trace 状态/attempt ID、assistant 工具选择及保存的理由。紧凑结果指向原 assistant 回合，详细 Trace 和响应仍在原文件。

## 解释限制

每条任务每组只有一次正式运行，不支持统计显著性或整体提升。上游工具池及六步预算不同，TaskPilot 两组同工具池、八步预算，只有后者隔离候选筛选。按公开工具名称精确评分时，上游 `python_execute` 即使获取有效证据也不算 GitHub 工具命中。未标注后续回合只能披露，不能事后增加标签评分。9 条受控任务证明执行边界，不证明模型自主选择。全量基线无法满足 `core-011` 的路由专属标准，该差异保留而不抹平。Token、延迟及参数指标不在 T069 范围。
