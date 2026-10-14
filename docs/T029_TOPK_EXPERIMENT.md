# T029 — routing v1.1.0 的 Top-K 实验

这是**单步工具选择实验**，不是任务完成率或 Token/延迟评测。没有打开密封的 evaluation 集，也没有用它选择 K。逐次原始证据见 [`eval/results/t029_experiment_v1_1_0.jsonl`](../eval/results/t029_experiment_v1_1_0.jsonl)。

## 固定设置

- 数据集：`routing_v1_experiment.json`，版本 `1.1.0`，SHA-256 `38fa5d0f48c7e6159adff464c20f417d43b928fbad85028fbe3b0f1ff1cf87eb`；28 个样本。各组原始任务与合成 Observation 逐字相同。合成 Observation 是测试数据，不是真实 GitHub 证据。
- 分组：K=1、K=3、K=5、全量工具。每个样本每组执行一次，共 112 次真实模型调用；组顺序按样本轮换。全量组设置 `routing_top_k=None`、`routing_retriever=None`，按原工具注册顺序发送 5 个业务 Schema 加 `terminate`，不产生路由事件；不是用 K=5 模拟全量。
- 模型：通过 `api.deepseek.com` 的配置接口调用 `deepseek-flash`，temperature 为 `0`，输出上限 `1024`，tool choice 为 `auto`，请求超时 `45s`。提供方在该路径没有公开模型 revision，各组模型配置一致。
- Embedding：`sentence-transformers/paraphrase-MiniLM-L3-v2`，固定 revision `4ca70771034acceecb2e72475f72050fcdde4ddc`，向量版本 `mean-pool-128-v1`。
- 工具池：5 个 GitHub 只读 Local 工具（`github_repository_info`、`github_issue_search`、`github_issue_detail`、`github_code_search`、`github_read_file`）及控制工具 `terminate`。使用真实工具对象与 Agent `think()` 路径，不调用 `act()` 或 GitHub HTTP；每次只有一个决策步骤、零工具执行，以隔离路由与执行影响。
- JSONL manifest 记录执行脚本 hash、起始 HEAD、模型与 Embedding 版本、数据 hash、工具池、控制工具、归一化及预算。每次记录消息 hash、发送的 Schema、模型选择名称/调用 ID、公开回答、原始 T027 路由事件（查询、候选、分数、run ID、索引版本）、错误及执行次数；不记录模型参数值和凭据。

当时的运行命令：

```bash
.venv/bin/python -m eval.t029_topk_experiment --output eval/results/t029_experiment_v1_1_0.jsonl
```

输出是只追加的 JSONL；`--resume` 仅可在数据集和模型配置相同的情况下续跑。脚本不读取 evaluation 文件。另行复跑应使用新输出路径，保留历史记录。

## 配对检查与选择结果

28 个样本各有四次运行，同样本的输入消息 hash 和完整工具注册表跨组一致。112 次模型调用全部完成，无传输/Provider 异常，全部保留 `terminate`，全部零执行。28 次全量运行绕过检索；84 次路由运行各记录一个路由事件，含 5 个业务候选及分数。原始文件扫描未发现凭据形态值。

以下为 23 个 `expected_action=tool` 样本的**描述性计数**。“首个可接受”指首个选中工具属于该样本的多标签 `acceptable_tools`；“任一可接受”检查任一并行调用；“全部可接受”要求没有标签外工具，但不惩罚同一工具重复调用。

| 分组 | 首个可接受 | 任一可接受 | 全部可接受 | 含额外工具的样本 |
| --- | ---: | ---: | ---: | ---: |
| K=1 | 14/23 | 14/23 | 14/23 | 9/23 |
| K=3 | 21/23 | 21/23 | 19/23 | 4/23 |
| K=5 | 19/23 | 23/23 | 13/23 | 10/23 |
| 全量 | 17/23 | 23/23 | 8/23 | 15/23 |

首个工具未命中的全部样本：

- K=1：`exp-007`、`exp-013`、`exp-014`、`exp-015`、`exp-016`、`exp-018`、`exp-019`、`exp-024`、`exp-028`，共 9 个，并行调用也没有补回标签工具。
- K=3：`exp-019`、`exp-028`，共 2 个，无并行补回。
- K=5：`exp-015`、`exp-017`、`exp-020`、`exp-028`，共 4 个，后续并行调用均包含标签工具。
- 全量：`exp-010`、`exp-011`、`exp-015`、`exp-017`、`exp-019`、`exp-024`，共 6 个，后续并行调用均包含标签工具。

无匹配样本 `exp-026` 在四组均选择 `terminate`。四个 `expected_action=clarify` 样本没有在这个仅选择工具的框架中证明完整澄清：

- `exp-021` 缺 Issue：K=1/K=3 选择 `terminate` 并公开请求 Issue 标识；K=5/全量选择仓库信息和 Issue 搜索。
- `exp-022` 缺路径：K=1/K=3 选择 `github_read_file`，K=1 的公开文字猜测 README；K=5/全量选择 `github_repository_info`。
- `exp-023` 缺仓库：各组均选择 `github_repository_info`。测试使用不会执行的占位工具上下文，并在提示中说明仓库尚未确认。
- `exp-027` 缺 Issue：K=1 选择 `github_issue_detail`；K=3 两次选择 `github_issue_search`；K=5/全量选择仓库信息和 Issue 搜索。

这些是选择/澄清失败，不是 API 错误；该单步实验没有调用 T016–T019 的执行层澄清路径。一些工具样本还产生重复并行调用，原记录保留全部名称和调用 ID，没有合并成一次命中。

## 选择结论与限制

**建议下一次受控对照使用 K=3。** 它与当前步骤标签的首调用一致性最高（21/23）；K=5 和全量更常混入额外并行工具，K=1 在 9 个工具样本中排除了有用备选。这是 experiment 集上的选择，不是生产优势结论。当时 evaluation 集仍密封并留待 T030；本文不宣称正式准确率、Token 或延迟结果。

**未验证（NOT VERIFIED）：**模型 revision、重复试验稳定性、该选择实验的实际任务完成/GitHub HTTP 结果，以及缺参样本的成功澄清。无仓库测试不能证明未确认仓库时的生产执行行为。
