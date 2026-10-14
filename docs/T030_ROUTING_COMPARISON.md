# T030 — routing v1.1.0 评估集对照

这是**路由阶段的单步对照**，不是阶段 8 的任务级评测。T029 已在独立的 28 条 experiment 集选择 K=3；T030 使用全部 12 条 evaluation，每组各一次，不再调 K。[原始配对运行](../eval/results/t030_evaluation_v1_1_0.jsonl)完整保留。

## 固定设置与证据

- 评估集版本 `1.1.0`，SHA-256 `31bd9377edd126d3a4ce1910c477b8b57792e508e41efc33db85f697d87a81a6`；8 条 `tool`、3 条 `clarify`、1 条 `no_match`。每对原任务与合成 Observation 相同；Observation 是测试数据，不是真实 GitHub 响应。
- 真实模型为 `api.deepseek.com` 的 `deepseek-flash`，temperature `0`、最大输出 `1024`、tool choice `auto`、超时 `45s`。响应模型名相同，但没有独立服务端 revision。两组注册表均为 5 个 GitHub 只读 Local 工具与 `terminate`；K=3 暴露 3+1，full 暴露 5+1。
- K=3 使用固定本地 `sentence-transformers/paraphrase-MiniLM-L3-v2`，revision `4ca70771034acceecb2e72475f72050fcdde4ddc`、向量版本 `mean-pool-128-v1`。full 的 `routing_top_k`、`routing_retriever` 均为 `None`，12/12 发送完整注册表且无路由事件。K=3 有 12/12 原始 T027 事件，含候选、分数、选择与索引版本；每对初始消息 hash 和注册表相同，组顺序按样本交替。
- 每次调用真实 Agent `think()` 和真实模型恰好一次，一个决策步骤、**零工具执行**，隔离 GitHub 执行影响。manifest 记录脚本 hash、起始 HEAD、数据 hash、模型/Embedding 配置和预算；逐行保留候选/发送工具、选择/原 ID、路由、Provider usage、Embedding 次数、错误和时间，不记录凭据及工具参数。
- manifest 的脚本 SHA-256 对应真实运行版本。之后经过 Black 格式化；已恢复执行时 Git blob 并确认 Python AST 与格式化后提交一致。

当时运行命令如下，复跑需有效配置并使用新输出路径：

```bash
PYTHONPATH=. .venv/bin/python eval/t030_routing_comparison.py --output eval/results/t030_evaluation_v1_1_0.jsonl
```

## 结果与分母

候选召回率以 **8 条 tool 样本**为分母，发送 Schema 至少含一个可接受工具即命中。工具选择准确率使用相同分母，要求模型至少选一个工具且**全部选择均可接受**，支持多标签；重复同一可接受工具不降低此分数。3 条澄清和 1 条无匹配单列。

| 指标 | K=3 | 真实全量工具 |
| --- | ---: | ---: |
| 候选召回 | 8/8 | 8/8（全部暴露） |
| 最终工具选择准确率 | 8/8 | 7/8 |
| 无匹配时只选择 terminate | 1/1 | 0/1 |
| Provider 尝试/错误/重试 | 12 / 0 / 0 | 12 / 0 / 0 |
| Provider 输入 Token | 8,392（12/12 响应报告） | 9,922（12/12） |
| Provider 输出 Token | 2,950（12/12） | 2,137（12/12） |
| Provider 总 Token | 11,342（12/12） | 12,059（12/12） |
| Embedding 调用/输入文本 | 13 / 17 | 0 / 0 |
| Embedding 耗时 | 3,214.8 ms | 0 ms |
| 端到端决策延迟总量 | 23,823.0 ms / 12 | 16,264.7 ms / 12 |
| 端到端决策延迟均值 | 1,985.2 ms / 12 | 1,355.4 ms / 12 |
| 端到端决策延迟中位数 | 1,083.0 ms / 12 | 895.2 ms / 12 |

usage 来自每次成功 Provider 响应，不从文本估算；24 次响应三项字段齐全。失败尝试与重试均为零，因此实测重试时间和报告重试 Token 为零。Embedding 单独计时且包含在 K=3 端到端决策时间中；首次建索引进行两次 encode，后续复用。延迟含检索与模型决策，不含工具执行。

相对全量，K=3 输入少 **1,530**、总量少 **717**、输出多 **813** Token；总延迟多 **7,558.3 ms**、均值多 **629.8 ms**。7/12 对更慢（`eva-001/002/004/005/006/008/012`），3/12 对总 Token 更多（`eva-006/008/012`），不宣称延迟改善。

## 失败与异常选择

- `eva-004`：全量同时选择可接受的 `github_code_search` 和额外 `github_repository_info`；K=3 仅选可接受工具。这是全量唯一工具选择错误。
- `eva-008` 无匹配：K=3 选择 `terminate`，全量选择仓库信息，独立于 8/8 分母。
- `eva-012` 澄清：两组都选择业务工具而非仅询问缺失信息。K=3 选择 Issue 搜索和文件读取，全量选仓库信息和 Issue 搜索；在仅选择的框架中属于澄清失败，没有执行业务工具或检验执行层校验/澄清。
- `eva-006/011` 澄清：两组均无工具调用而生成公开文本，保留原响应；单步框架不证明完整澄清/合并/执行。
- `eva-002`：两组均并行两次选择 `github_issue_search`，均视为可接受但保留两 ID。24 次运行无 Provider/传输错误、超时或 usage 缺失。

## 真实 GitHub 调查回归

[复核运行](../eval/results/t030_investigation_regression_verified.jsonl)记录三次独立真实 Repository Investigation Agent、K=3、模型与 GitHub REST。仓库为明确用户输入的 `FoundationAgents/OpenManus` 默认分支，继承的 `GITHUB_TOKEN` 支持认证代码搜索。

| T021 链路 | 真实 HTTP | 业务工具序列 | 结果 |
| --- | --- | --- | --- |
| 仓库理解 | 2 × 200 | 仓库信息、README 读取 | 回答含仓库证据/URL，成功 terminate |
| Issue 调查 | 4 × 200 | Issue 详情、源码读取、Issue 搜索 | 回答含 #1427 和 GitHub URL，成功 terminate |
| Issue → 源码 | 10 × 200 | Issue 详情、代码搜索、源码读取 | 回答含 #1427、源码路径及 URL，成功 terminate |

三次均返回非空回答，无运行异常或步数上限。原记录保留 HTTP 方法/路径/状态、工具名称/ID、路由及脱敏回答片段。[初始运行](../eval/results/t030_investigation_regression.jsonl)也保留；其 `finished=False` 是**测量错误**，因为 `BaseAgent.run()` 会恢复原状态。复核重跑改为从匹配原调用 ID 的成功 terminate Observation 判断结束；两次运行均有真实成功 GitHub 请求。未为修正测量而改产品代码。

## 凭据审计与限制

当时用户确认手动轮换，真实 Provider 鉴权检查成功。精确值扫描未在跟踪文件、T029/T030 结果或项目日志发现活动模型/GitHub 凭据；本地配置仍被 Git 忽略，报告与记录不含密钥值。

**当时未验证（NOT VERIFIED）：**独立 Provider revision、重复运行稳定性、完整任务 Token/延迟与阶段 8 最终评测、三条 clarify 的完整合并执行、真实 MCP 参数 E2E（T035/T038）、默认 thinking 多轮兼容。三类真实回归证明路由下的仓库调查，不代表 PR-03 全部澄清场景。
