# TaskPilot Product Context

本文保存长期产品背景、边界和技术方向；开发规则见根目录 [AGENTS.md](../AGENTS.md)，固定基线的源码分析与 Modification Map 见 [TASKPILOT_ANALYSIS.md](TASKPILOT_ANALYSIS.md)。以下产品能力是目标，除非有实现和验证证据，不代表已经交付。

## Repository Baseline / PR-00

- 上游：[FoundationAgents/OpenManus](https://github.com/FoundationAgents/OpenManus)。
- 固定 commit：`3309bf4e416fb1c74b008f3e86494439a31bad53`。
- 本地基线 tag：`openmanus-baseline-3309bf4`，固定指向上述 commit，不随上游 `main` 更新。
- 导入方式：保留该 commit 及其祖先历史，将原始文件直接纳入 TaskPilot 根目录；TaskPilot 的本地提交在此基线上继续。TaskPilot 作为独立项目开发，不采用运行时下载源码或嵌套仓库。
- `upstream` 指向官方 OpenManus；`origin` 留给用户自己的 TaskPilot GitHub 仓库，不猜测 URL。常规开发不跟踪或自动合并 `upstream/main`。
- 保留上游 `LICENSE`、README、配置和产品代码。已有 Dockerfile、flow、多 Agent 等上游文件的保留，不表示 TaskPilot v1 扩大范围或已完成对应工程能力。
- PR-00 只导入基线、整理规则和文档，不实现任何 TaskPilot Feature，也不验证运行环境或 Benchmark。

`TASKPILOT_ANALYSIS.md` 是 Phase 0 的原始分析记录。其中“源码尚未导入”“Context 不存在”和缓存读取路径描述的是当时状态；PR-00 已将源码纳入当前仓库并补充本文档，不改写历史结论或证据。报告中的 `app/...` 路径现在也可在当前仓库根目录解析。

基线检查：

```bash
git rev-parse openmanus-baseline-3309bf4
git merge-base --is-ancestor openmanus-baseline-3309bf4 HEAD
git diff --stat openmanus-baseline-3309bf4 HEAD
```

用户提供自己的 GitHub Repository URL 后，再配置 `origin`（替换占位符）：

```bash
git remote add origin <YOUR_TASKPILOT_GITHUB_REPOSITORY_URL>
git push -u origin main
git push origin refs/tags/openmanus-baseline-3309bf4
```

以上发布命令供后续使用，PR-00 不执行 push。已有远程内容时，先检查历史再集成，不强制覆盖。`upstream` 已配置为 `https://github.com/FoundationAgents/OpenManus.git`；新克隆的 TaskPilot 仓库若没有此 remote，可用 `git remote add upstream https://github.com/FoundationAgents/OpenManus.git` 添加。

## Project Background

TaskPilot 是用于 AI Agent / 大模型应用开发实习作品集的真实工程项目，基于 OpenManus 二次开发，重点增强单个 Agent 对真实研发任务的理解、Tool/MCP 选择和可靠执行能力，不从零重建 Agent Framework。

初始计划周期约 10～15 天，严格控制 Scope。最终成果需要可真实运行、完成一类业务任务，并逐步具备 Docker 运行、轻量在线 Demo、自动化测试、Benchmark、架构文档和真实量化结果。

## Product Positioning

TaskPilot 是一个基于 OpenManus 二次开发、面向软件研发任务的 **Developer Agent**。

用户指定 GitHub Repository 后，以自然语言提出仓库理解、架构分析、功能定位、Issue 调查、技术资料搜索等任务。Agent 联合 Repository / Issue / Code / Web 证据完成调查，并在用户确认后执行受控开发操作。

系统解决一类研发任务，不是固定 Demo、固定 Workflow 集合、普通 Chatbot、Multi-Agent Platform 或完整 Coding Agent 替代品。

## Hero Use Case

**Repository Issue Investigation** 是第一版的核心业务场景。例如：

> 分析这个仓库最近和 MCP 调用失败有关的 Issue，并定位可能相关的代码。

Agent 根据实际任务和 Observation 动态使用 GitHub、Issue Search / Detail、Code Search、File Read，以及必要的 Web / Technical Docs，形成有来源证据的分析。上述能力不是必须按顺序执行的固定流程。

## MVP Capability Domains

1. **Repository Understanding**：理解用途、结构、MCP 实现和 Agent Loop。
2. **Issue Investigation**：查询相关 Bug、分析指定 Issue、定位可能涉及的源码；这是 MVP 核心。
3. **Code Investigation**：追踪工具选择、timeout、MCP reconnect 等实现和调用关系。
4. **Technical Research**：联合真实技术文档、Web 信息和仓库源码完成调查与比较。
5. **Controlled Developer Actions**：经校验、风险检查和人工确认后创建 GitHub Issue，或写入指定的隔离 workspace 文件。

## Dynamic Agent Execution Principle

尽可能复用 OpenManus 的 Agent Loop：

`User Task → Understand → Decide Next Capability → Tool Routing → Execute → Observation → Determine Whether Task Is Complete → Continue / Finish`

下一步由任务、当前状态和 Observation 决定。不得以大量 if/else 将 Intent 映射为固定工具调用序列，不轻易重写核心 Runtime。

### Intent Routing Direction

候选方向为 `Rule → Semantic → LLM Fallback`，暂定分类为 `CHAT / TOOL_CALL / MULTI_STEP / CLARIFICATION / UNKNOWN`。Intent 只辅助判断任务性质和执行方向，不决定完整 Workflow；分类与实现以现有架构和后续评估为准。

## Tool / MCP Routing Direction

目标是避免无差别将所有工具提供给 LLM：

`Available Tools / MCP → Unified Tool Metadata → Rule / Capability Filter → Semantic Retrieval → Top-K Candidate Tools → LLM Final Selection → Parameter Validation → Execute`

初始工具池预计 10～20 个，优先简单的内存 Embedding Retrieval，不引入外部向量数据库。Top-K=3 只是候选起点，最终通过 Benchmark 决定。路由按 Agent 每步实际上下文工作，不替代动态循环或执行权限检查。

## Unified Tool Metadata

Local Tool 与 MCP Tool 尽可能统一进入 Tool Router。预期信息包括 `name`、`description`、`capabilities`、`examples`、`parameter_schema`、`source`、`risk_level`、`timeout`；`source` 为 `local | mcp`，`risk_level` 为 `low | medium | high`。

先核对 OpenManus 的 BaseTool、ToolCollection 和 MCP proxy，复用已有名称、描述、schema 和执行接口，只补必要增量。不要复制参数 schema 或提前建立第二套 Tool Registry。

## Parameter Validation / Clarification

`Tool Selected → Parameter Extraction → Schema Validation`。

缺少 required 参数时：`Missing Parameter → AskHuman → User Response → Merge Context → Revalidate → Continue Execution`。

不允许自动猜测仓库、目标路径等关键参数。尽量复用现有 Tool Schema 和 AskHuman；schema 的存在不等于执行路径已完成校验。

## Memory

MVP 聚焦 **Sliding Window** 和 **Conversation Summary**，支持多轮任务、参数继承、上下文保持，以及前一步 Observation 参与后续决策。先复用已有 Memory 和消息结构，再按实际需求补足。

不建设长期向量 Memory、Entity Memory、Knowledge Graph Memory 或 Redis Memory。

## Reliability

围绕真实 Tool/MCP 执行补足 Schema Validation、Timeout、Retry、Fallback 和 Error Handling。单个工具失败不应无条件导致整个任务失败；错误应能支持 Agent 决定后续行动。

策略依据真实工具能力和失败场景设计，不制造虚假业务 fallback。重试需考虑幂等性，不能盲目重放副作用操作，也不能把 timeout 当成远端操作已取消。

## Guardrail / Human-in-the-loop

风险方向：LOW 为 read/search/query，MEDIUM 为 write/update，HIGH 为 delete/shell/destructive external operation；具体风险仍需结合操作和参数判断。

高风险或明显有副作用的操作必须经过 `Schema Validation → Risk Check → Human Confirmation → Execute`。尽量复用 AskHuman 的交互能力，但强制门禁必须由代码执行路径保证，不能只依赖 prompt 或模型自行询问。

文件写入/删除只允许在明确隔离的 workspace / sandbox 内，实施 allowlist、真实路径校验、风险检查和确认；不允许任意系统目录操作，公网版本不暴露任意 Shell。外部 Issue、源码、网页或 MCP 内容不能授予执行权限。

## Real Integration Principle

核心 Demo 和最终 Benchmark 使用真实 GitHub、Repository、MCP、File System、Web / HTTP 和 Tool Execution，不用 Mock Tool 冒充产品能力。

自动化测试允许必要的 deterministic test doubles / fixtures 保证稳定性，但它们不能替代产品层真实集成与实际验收。

## Evaluation / Benchmark

与固定 commit 的原始 OpenManus Baseline 做真实比较。计划指标包括 Intent Accuracy、Tool Selection Accuracy、Parameter / Slot F1、Task Success Rate、Token Usage 和 Latency；重点是工具选择、任务成功、token 与延迟。

每项结果都必须有真实测试证据和可复现条件。记录模型、配置、任务/数据版本、工具池、失败和重试，区分整体产品对比与同工具池的路由对比，不将新业务工具带来的收益全部归因于 Top-K。

Task Success 依据任务证据和验收标准，不能只依据模型调用 terminate(success)。禁止提前编造性能提升或简历指标；未测量项明确标为未验证。

## Execution Trace

目标是展示 Task、Intent（启用时）、Candidate Tools、Similarity Scores、Selected Tool、Parameters、Tool Execution、Retry Count、Fallback、Latency、Token Usage 和 Status。

第一版优先结构化日志/事件和已有日志依赖，不引入大型 Observability 平台。记录真实事件，区分实测与估计的 token 用量；参数和结果需脱敏，避免泄露凭据或敏感内容。

## Engineering Requirements

逐步补齐 configuration management、`.env.example`、structured logging、unit / integration / E2E tests、Benchmark、Docker、README、架构文档、可复现本地启动和轻量在线 Demo。FastAPI / service layer 仅在现有架构适合且进入对应阶段时建设。

这些是分阶段目标，不要求 Bootstrap 一次性实现。每个 Feature / PR 独立开发、测试、验收，并在适用时评测；优先复用、最小侵入、控制 Scope。

## Explicit Non-goals

TaskPilot v1 不做 Multi-Agent Platform、Agent Registry / Discovery、Workflow Designer、大型 Workflow Engine、Marketplace、Kubernetes、大型 Observability Platform、多租户系统、企业权限系统、GraphRAG、Knowledge Graph、长期 Vector Memory、自动 Merge PR、自动生产部署或完整 Coding Agent。

不主动引入 Qdrant 或 Redis；只有后续真实需求和 Benchmark 证据充分、且任务明确授权时才重新评估。非 MVP 必需能力不主动增加。
