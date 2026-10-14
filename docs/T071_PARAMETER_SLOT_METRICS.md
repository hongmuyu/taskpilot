# T071 — 参数与 Slot 指标

本文只从冻结 Core Holdout v1.0.0 和已提交的 T066–T068 记录复算参数指标，不重新调用模型，不改变 T069/T070。复现命令：

```bash
.venv/bin/python -m eval.t071_slot_metrics --check
.venv/bin/python -m pytest -q tests/taskpilot/test_t071_slot_metrics.py
```

[评分脚本](../eval/t071_slot_metrics.py)校验冻结数据/规则 hash、三组各 21 行来源、选定的上游 MCP 复测指针和唯一 ID。[结果](../eval/results/t071_slot_metrics_v1.json)包含每个标准步骤、关键参数与声明来源、实际关键参数（含被拒输入）及通道、TP/FP/FN、原始文件/样本/回合引用和来源 hash。Assistant 完成状态不进入指标。

## 评分契约

- 计量单位是支持、具类型、已尝试 `tool` 步骤中的去重关键 `(field, normalized value)` 对。匹配为 TP，输出但不在冻结标准中为 FP，标准值未匹配为 FN；标准字段值错误同时产生 FP 与 FN。并行调用可贡献一次标准字段；两条查询都满足冻结标识词规则时，不因额外词不同制造 FP。
- `repository` 忽略大小写；固定 `ref`、路径和 MCP 文本精确匹配；整数不能是 Python `bool`。查询必须包含全部冻结标识词，顺序任意，可加有信息量的词。Schema 合法或工具成功不能替代值比较。
- Precision=TP/(TP+FP)，Recall=TP/(TP+FN)，F1 为二者调和平均。零分母记 `null`（NOT APPLICABLE）并保留原计数。`clarify/reject/no_match` 不进入值对分母；缺值在冻结脚本回复后才可评分。零标准字段的 `core-016` 无值对分母。
- `natural_resolved` 包含模型显式参数及已确认仓库上下文绑定的 `owner/repo/ref`，衡量执行边界可用参数，包含上下文复用。`natural_model_explicit` 只以 `task/observation` 字段为标准分母，仅计模型输出。两者不可混淆。上下文值对工具可用，不代表所有 GitHub HTTP 端点都使用 `ref`。
- `controlled_resolved` 单列 9 条零 LLM TaskPilot 用例；`core-007/008/014` 为冻结脚本回复，不是模型抽取或真人自由回复。通道包括 `confirmed_context`、`model_tool_call`、`controlled_injection`、`scripted_user_reply`。匹配值不能揭示模型认知来源，可审计的是通道来源。

## 结果与分母

**原始 OpenManus：**21 行，12 supported、9 unsupported。9 条 GitHub 调查使用无结构 `python_execute`，没有可比的强类型关键参数接口；1 条 MCP 失败没有标准值字段。仅 **2 个自然 MCP 类型化步骤**贡献 TP/FP/FN=**3/0/0**，Precision/Recall/F1=**1.000/1.000/1.000**。不能把该窄子集与 TaskPilot 的 GitHub+MCP 整体比较；缺参、澄清及参数错误分母为零/NOT APPLICABLE。

**TaskPilot 全量工具：**12 自然任务、9 零 LLM 受控任务，无 unsupported。自然解析后 **16 步**，TP/FP/FN=**41/10/4**，P/R/F1=**0.804/0.911/0.854**。模型显式抽取以 17 个任务/Observation 标准字段计，**13/10/4**，P/R/F1=**0.565/0.765/0.650**。受控解析后 **6 个含值步骤**为 **14/0/0**。全部类型化解析值合计 **55/10/4**，P/R/F1=**0.846/0.932/0.887**；合计包含脚本控制，**不是模型自主结果**。

**TaskPilot K=3：**同样 12 自然、9 受控。自然解析后 **16 步**为 **40/12/5**，P/R/F1=**0.769/0.889/0.825**。模型显式的 17 个字段为 **12/12/5**，P/R/F1=**0.500/0.706/0.585**。受控 **6 个含值步骤**为 **14/0/0**。全部类型化解析值为 **54/12/5**，P/R/F1=**0.818/0.915/0.864**。这次参数指标 K=3 低于全量组，21 条单次任务不能证明总体改善或显著性。

关键字段准确率（匹配标准字段/标准字段）：上游类型化子集 **3/3**；全量自然解析后 **41/45**、模型显式 **13/17**、受控 **14/14**；K=3 自然解析后 **40/45**、模型显式 **12/17**、受控 **14/14**。要求全标准字段且无额外关键值对的严格步骤准确率为：上游 **2/2**，全量自然 **9/16**，K=3 自然 **7/16**。逐步值完整保留在 JSON。

## 缺参、澄清、错误与猜测

各 TaskPilot 组的 `core-007/008/014` 均检测 **3/3** 冻结缺失字段，无额外字段，回复前 **3/3** 零执行；**3/3** 脚本回复合并、重校验并按原 `tool_call_id` 恰好执行一次。`core-009` 畸形 JSON、`core-010` 以 bool 冒充整数均识别为预期错误，**2/2**，均零执行。上游不支持这五条受控边界用例，不补造零分。

两组自然及受控数据均观察到 **0 个已确认标准字段错误值**。自然步骤全量有 10 个、K=3 有 12 个 FP，主要是额外查询或后续步骤工具（例如 `core-006/020/021`）。额外值对是评分 FP，但不能仅因它不在当前步骤标准中就认定没有依据；部分词来自任务或真实 Observation。因此已确认无依据值计数为零，更广泛的无依据猜测数量为 **NOT VERIFIED**，不把全部 FP 称为猜测。

## 人工抽查与限制

- `core-001/003` 对比任务路径/Issue 编号、已确认仓库/ref 与实际参数/上下文；`core-013/015` 对比选定上游复测与两组 MCP 的 text/suffix。
- `core-007/008/014` 检查 `run.pending_missing_fields`、脚本回复来源、validation pending→success、clarification requested→merged、一次执行及原 Tool 消息 ID；`core-009/010` 检查注入原输入与零执行的校验失败。
- `core-005/006/020/021` 即使部分调查成功，遗漏路径/查询与额外调用仍记 FP/FN；失败或 NOT VERIFIED 任务不从可评分步骤剔除。
- 留出集**没有自然多轮参数修正**、没有真人自由回复。确定性 wrong→correct 评分测试不是真实修正运行证据，两者均为 **NOT VERIFIED**。仅凭值匹配也无法观察模型具体使用任务还是 Observation。

## 回归数量核对

T070 提交新增 9 个测试、未删除测试。T071 前重新收集 `tests/taskpilot` 得 **359** 项，增加 T071 的 8 项后为 **367**。更早的 **363** 与 **359 passed** 未将所用命令保存在冻结证据中，无法重建四项差异为历史事实。后续验证明确使用完整 TaskPilot 加 baseline/Browser MCP 范围；T071 未删除或排除 T070 测试。
