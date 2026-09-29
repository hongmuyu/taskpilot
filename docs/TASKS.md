# TaskPilot MVP Task Ledger

## Dashboard

- Current Phase: Phase 2 — Reliable Parameter Execution
- Current PR: PR-03 — Parameter Validation and Clarification（计划，尚未开始）
- Next Task: T013 — Tool Schema Validator — execution boundary
- Total Tasks: 73
- Completed: 12
- TODO: 60
- OPTIONAL: 1
- IN_PROGRESS: 0
- BLOCKED: 0
- Remaining P0: 54
- Remaining P1: 6
- Remaining P2: 1
- Total P0 / P1 / P2: 66 / 6 / 1
- State verified: 2026-09-29；功能基线 main `7ca8a9b02fea07a8de476fdf68ed45e919ba1e28`

本文是后续 MVP 开发的唯一任务总账，负责状态、优先级、依赖、PR 与验收；产品背景见 [Product Context](TASKPILOT_CONTEXT.md)，源码分析见 [Source Analysis](TASKPILOT_ANALYSIS.md)，开发规则见 [AGENTS.md](../AGENTS.md)。本次只建立总账，不实施任何 TODO，也不启动 PR-03。

## Maintenance and priority rules

- ID 永久稳定；新任务使用当前最大 ID 之后的新编号，即使插入早期 Phase 也不重排旧 ID。
- 每个 Task 保留 ID、Title、Phase、Priority、Status、Depends On、Target PR、Goal、Acceptance Criteria、Tests、Out of Scope、Notes。以下按任务卡片呈现，避免十二列宽表。
- Status 仅用 `DONE`、`TODO`、`IN_PROGRESS`、`BLOCKED`、`OPTIONAL`。DONE 必须有验收证据；TODO 不表示已开工；IN_PROGRESS 表示已授权且正在实施；BLOCKED 在 Notes 写具体阻塞；OPTIONAL 不阻塞 MVP。未测证据在 Notes/Tests 标 `NOT VERIFIED`，不是第六种状态。
- P0 是 MVP 出口必需；P1 是可延后质量增强；P2 是可删除附加体验。任何 P0 不得依赖未完成 P1/P2；选做任务不得扩大其安全权限。
- Depends On 是直接交付依赖，不只是编号顺序；`—` 表示无任务依赖。允许引用后置 ID，但依赖图必须无环。同一 PR 内按依赖实施，整个 Feature 验收后才合并。
- Target PR 的 PR-00…PR-09 是项目阶段标识，不是 GitHub PR number。PR-01 对应 GitHub #5，PR-02 对应 #6；后续真实 URL 在相应任务 Notes 更新。必要拆分小 PR 时更新 Target PR，保持 ID 不变。
- DONE 部分不重开或夸大；新增能力另建 TODO 并注明与既有部分的区别。临时 test doubles、静态源码、历史真实 E2E、本轮重测分开记录。
- 每次状态变更同步更新 Dashboard。Completed = Status 为 DONE 的数量；Remaining Px = 该 Priority 且 Status 非 DONE 的数量，包含 OPTIONAL/BLOCKED；TODO 单独计数，不把 OPTIONAL 混入 TODO。当前 12 + 60 + 1 = 73，剩余 54 + 6 + 1 = 61。
- 删除可选范围时保留原 ID，使用 OPTIONAL 并在 Notes 写“移出本次交付”及原因，不用 DONE 冒充完成；它仍进入非 DONE 计数，但不阻塞 Exit Criteria。
- 下一 PR 只实现当前批准的任务范围。此总账不是对后续所有副作用操作的预授权；真实写入仍必须走代码级校验、风险检查和用户确认。

## Verified state and evidence

本轮已完整阅读四份指定文档、Repository Investigation 文档、当前 main 历史及合并 PR，核对现有 Agent、GitHub Adapter、测试和 Dataset v0。证据以当前 checkout 为准；历史分析里的“源码未导入”等描述保留其原时点含义。

- **E00 — Bootstrap/history**：`1a4b9ddb55cb2b36798d14cd140ba9ee12f180d9`；固定 tag `openmanus-baseline-3309bf4` 指向 `3309bf4e416fb1c74b008f3e86494439a31bad53`，且是 main 祖先；[Context](TASKPILOT_CONTEXT.md)、[LICENSE](../LICENSE) 与上游历史均在仓库。
- **E01 — Source analysis**：[TASKPILOT_ANALYSIS.md](TASKPILOT_ANALYSIS.md)，固定上游快照的静态分析；不是 Feature 实现证据。
- **E02 — Runtime baseline**：[BASELINE_RUNTIME.md](BASELINE_RUNTIME.md)、[baseline tests](../tests/baseline/test_runtime.py)、[live runner](../tests/baseline/run_live.py)；[PR #5](https://github.com/hongmuyu/taskpilot/pull/5) 已合并，commit `27cb35b08da125bd6044c8d7be969ffbaadf7574`。
- **E03 — Real investigation evidence**：[REPOSITORY_INVESTIGATION.md](REPOSITORY_INVESTIGATION.md) Case A/B/C；[PR #6](https://github.com/hongmuyu/taskpilot/pull/6) 已合并，commit `7ca8a9b02fea07a8de476fdf68ed45e919ba1e28`。
- **E04 — Adapter/tests**：[github_tools.py](../app/taskpilot/github_tools.py)、[test_github_tools.py](../tests/taskpilot/test_github_tools.py)，包含 Context、五类工具、字段归一化和只读 Agent 测试。
- **E05 — Agent/CLI**：[repository_investigation.py](../app/agent/repository_investigation.py)、[taskpilot.py](../taskpilot.py)，工具池为五个 GitHub 读工具加 terminate，复用动态 Agent Loop。
- **E06 — Dataset v0**：[repository_investigation_v0.json](../eval/datasets/repository_investigation_v0.json)，16 条初始任务，覆盖六类 capability；没有完整正式 Benchmark 的运行/评分结果。
- **本轮重测**：下列确定性回归为 **26 passed**（TaskPilot 13 + baseline/Browser MCP fixture 13）；已有依赖/配置 warnings 保留，未修改。未重跑付费 provider 或真实写入；DONE 的真实 E2E 依据已合并 E02/E03 历史证据，不伪装成本轮新测。

```bash
.venv/bin/python -m pytest tests/taskpilot/test_github_tools.py tests/baseline/test_runtime.py tests/tools/test_browser_use_mcp.py -q
```

边界：PR-01 已证明 non-thinking 模式的主链路；默认 thinking-mode 多轮 tool-call 仍 NOT VERIFIED。PR-02 的真实 GitHub REST 闭环不证明真实 MCP 生命周期；MCP 由 T020/T035/T038 补验。当前通用参数校验/澄清、Semantic Router、统一可靠性、受控写、多轮安全窗口、完整 Trace/API/UI、TaskPilot Docker/部署和正式 Benchmark 均未完成。

工程发现：GitHub 原始 Issue Search Observation 曾使模型上下文超过 **100k tokens**；Observation Normalization 后相关后续模型输入约为 **1.9k tokens**。这是已观察到的 context reduction，不能写成正式 Benchmark 的整体性能提升，也不能据此生成简历提升百分比。

## Phase / PR navigation

- Phase 0：T001–T003，Foundation，PR-00 / PR-01，DONE。
- Phase 1：T004–T012，Real Business Loop，PR-02，DONE。
- Phase 2：T013–T021，Reliable Parameter Execution，PR-03，下一阶段。
- Phase 3：T022–T030，Tool Routing，PR-04。
- Phase 4：T031–T038，Reliability，PR-05。
- Phase 5：T039–T046，Controlled Developer Actions，PR-06。
- Phase 6：T047–T052，Memory，PR-07。
- Phase 7：T053–T064，Productization，PR-08 或按依赖拆成小 PR。
- Phase 8：T065–T073，Final Evaluation，计划 PR-09。

后续 Task 的 Acceptance Criteria 和 Tests 是验收要求，不是已通过的结果；实施完成时必须附具体命令、版本、结果和脱敏证据位置。未验收不得将 TODO 改为 DONE。

## Phase 0 — Foundation

已完成；固定上游来源与历史分析，不在本总账重写分析报告。

### T001 — Repository Bootstrap

- **ID:** T001
- **Title:** Repository Bootstrap
- **Phase:** Phase 0
- **Priority:** P0
- **Status:** DONE
- **Depends On:** —
- **Target PR:** PR-00
- **Goal:** 建立可追溯的 TaskPilot 仓库。
- **Acceptance Criteria:** 固定 tag 指向上游 3309bf4e；保留祖先历史和 LICENSE；规则与 Context 纳入仓库。
- **Tests:** 本轮 git rev-parse、merge-base 检查通过；bootstrap diff 已核对。
- **Out of Scope:** 上游升级、产品 Feature。
- **Notes:** E00；Target PR 为 PR-00（bootstrap commit，无独立 GitHub PR）。

### T002 — OpenManus Source Analysis

- **ID:** T002
- **Title:** OpenManus Source Analysis
- **Phase:** Phase 0
- **Priority:** P0
- **Status:** DONE
- **Depends On:** —
- **Target PR:** PR-00
- **Goal:** 保存固定基线的真实源码与修改落点。
- **Acceptance Criteria:** 报告含执行链、Tool/MCP、Memory、风险及源码索引，明确静态分析边界。
- **Tests:** 本轮完整核读报告；对照当前 ToolCallAgent、专用 Agent 和 Tool 实现。
- **Out of Scope:** 将建议当成已实现能力。
- **Notes:** E00、E01；分析先于 bootstrap 完成，随 PR-00 纳入历史。

### T003 — Runtime Baseline Validation

- **ID:** T003
- **Title:** Runtime Baseline Validation
- **Phase:** Phase 0
- **Priority:** P0
- **Status:** DONE
- **Depends On:** T001, T002
- **Target PR:** PR-01
- **Goal:** 验证原始运行主链路并固定环境。
- **Acceptance Criteria:** 环境锁定；文本、Local Tool、错误 Observation、terminate、连续 run 有记录；未验证能力明确标注。
- **Tests:** E02 中真实 provider Case A–E；本轮 baseline 与 Browser MCP fixture 子集 13 tests 通过。
- **Out of Scope:** 真实 MCP 生命周期、thinking mode 兼容、正式 Benchmark。
- **Notes:** PR-01 已合并；DONE 仅覆盖 E02 列明的范围，真实 MCP 增量见 T035/T038。

## Phase 1 — Real Business Loop

已完成的只读闭环。GitHub 是 Local REST Adapter；不将它记为真实 MCP 集成。

### T004 — Repository Context

- **ID:** T004
- **Title:** Repository Context
- **Phase:** Phase 1
- **Priority:** P0
- **Status:** DONE
- **Depends On:** T001
- **Target PR:** PR-02
- **Goal:** 绑定用户显式选择的仓库和可选 ref。
- **Acceptance Criteria:** owner/repo 格式验证；工具共享同一 Context；入口要求显式 repository。
- **Tests:** E04 的 context parse、配置仓库和 ref 传递测试；本轮通过。
- **Out of Scope:** 缺仓库自动澄清、跨调用参数来源管理。
- **Notes:** E03/E04；已有静态绑定 DONE，澄清与参数复用增量见 T016/T019。

### T005 — GitHub Read-only Adapter

- **ID:** T005
- **Title:** GitHub Read-only Adapter
- **Phase:** Phase 1
- **Priority:** P0
- **Status:** DONE
- **Depends On:** T003, T004
- **Target PR:** PR-02
- **Goal:** 提供真实 GitHub 只读业务能力。
- **Acceptance Criteria:** metadata、Issue Search/Detail、Code Search、File Read 五工具可用；仅 GET；错误返回 Observation。
- **Tests:** E04 HTTP fixtures、404 测试；E03 真实 GitHub Case A–C。
- **Out of Scope:** 写入、通用 Retry、MCP transport。
- **Notes:** 已有 20 秒 HTTP timeout 和局部参数检查；不是 T013/T032 的通用策略。

### T006 — Repository Investigation Agent

- **ID:** T006
- **Title:** Repository Investigation Agent
- **Phase:** Phase 1
- **Priority:** P0
- **Status:** DONE
- **Depends On:** T005
- **Target PR:** PR-02
- **Goal:** 复用动态 Agent Loop 执行调查。
- **Acceptance Criteria:** 专用 Agent 仅装配五个 GitHub 工具和 terminate；下一步由 Observation 决定；工具结果带 call ID。
- **Tests:** E04 专用 Agent 两步回归；E03 动态真实调用链。
- **Out of Scope:** 固定 Workflow、Shell、Python 执行、写工具。
- **Notes:** E05；现有 cleanup 关闭 client，不代表连续 run 生命周期已验收，见 T052。

### T007 — Repository Understanding

- **ID:** T007
- **Title:** Repository Understanding
- **Phase:** Phase 1
- **Priority:** P0
- **Status:** DONE
- **Depends On:** T006
- **Target PR:** PR-02
- **Goal:** 基于真实仓库信息回答用途与结构问题。
- **Acceptance Criteria:** 模型读取 repository metadata 和 README，回答有仓库证据并结束。
- **Tests:** E03 Case A：两个 HTTP 200 调用和真实模型结果。
- **Out of Scope:** 所有仓库架构问题的准确率保证。
- **Notes:** 历史 E2E 已验收；本轮未重跑付费 provider，正式覆盖率见 Phase 8。

### T008 — Issue Investigation

- **ID:** T008
- **Title:** Issue Investigation
- **Phase:** Phase 1
- **Priority:** P0
- **Status:** DONE
- **Depends On:** T006
- **Target PR:** PR-02
- **Goal:** 检索并分析仓库内 Issue。
- **Acceptance Criteria:** 搜索与详情/评论组成真实证据；空结果可明确表达；保留 Issue 编号和 URL。
- **Tests:** E03 Case B；E04 Issue scope、detail/comments 回归。
- **Out of Scope:** 全量评论分页、写 Issue。
- **Notes:** 当前详情最多十条评论；更多覆盖由最终数据集明确。

### T009 — Issue → Code Investigation

- **ID:** T009
- **Title:** Issue → Code Investigation
- **Phase:** Phase 1
- **Priority:** P0
- **Status:** DONE
- **Depends On:** T008
- **Target PR:** PR-02
- **Goal:** 从 Issue 动态定位相关实现。
- **Acceptance Criteria:** 真实 Issue 详情后能选择 Code Search/File Read；结论区分来源证据和推测。
- **Tests:** E03 Case C 的 #1427、MCP 源码搜索和文件读取 HTTP 200 记录。
- **Out of Scope:** 自动修复、生成补丁、完整 Coding Agent。
- **Notes:** Code Search 真实验证使用认证；仅代表已记录场景，不是 Task Success Rate。

### T010 — Observation Normalization

- **ID:** T010
- **Title:** Observation Normalization
- **Phase:** Phase 1
- **Priority:** P0
- **Status:** DONE
- **Depends On:** T005
- **Target PR:** PR-02
- **Goal:** 控制 GitHub Observation 体积并保留调查字段。
- **Acceptance Criteria:** 搜索/详情保留相关字段和 URL；正文、评论、文件有现有截断边界。
- **Tests:** E04 字段投影断言；E03 上下文规模观察；源码 _trim 与文件 12,000 字符限制已核对。
- **Out of Scope:** 通用 token budget、正式性能提升结论。
- **Notes:** 原始 Issue Search 曾使模型上下文超过 100k tokens；归一化后相关后续输入约 1.9k tokens，仅为观察到的 context reduction。

### T011 — Dataset v0

- **ID:** T011
- **Title:** Dataset v0
- **Phase:** Phase 1
- **Priority:** P0
- **Status:** DONE
- **Depends On:** T007, T008, T009
- **Target PR:** PR-02
- **Goal:** 保存只读调查的初始任务集。
- **Acceptance Criteria:** 16 条任务覆盖六类 capability，每条有 repository 与 evidence_requirement。
- **Tests:** E06 JSON 解析、任务数量和字段检查；非 16 条真实 E2E 全通过声明。
- **Out of Scope:** 固定快照、完整标注、独立评测集、跑分。
- **Notes:** v0 文件 DONE；routing 标注增量 T028，最终冻结数据集 T065。

### T012 — Real GitHub E2E Validation

- **ID:** T012
- **Title:** Real GitHub E2E Validation
- **Phase:** Phase 1
- **Priority:** P0
- **Status:** DONE
- **Depends On:** T007, T008, T009, T010, T011
- **Target PR:** PR-02
- **Goal:** 留存真实模型调用真实 GitHub 的业务闭环证据。
- **Acceptance Criteria:** Repository、Issue、Issue→Code 三类 E2E 有实际工具名、HTTP 状态及 Observation 记录。
- **Tests:** E03 Case A–C；本轮 E04 对应 13 tests 通过。
- **Out of Scope:** 真实 MCP E2E、路由对比、跨模型泛化。
- **Notes:** PR-02 已合并；E03 是已提交的历史证据摘要，原始日志本地忽略；本轮未重新调用 GitHub 业务 API。

## Phase 2 — Reliable Parameter Execution

下一开发阶段，尚未开始。目标：`LLM Tool Call → Validate → Clarify if required → Revalidate → Execute`。关键仓库、路径和操作目标只能来自用户明确输入或可信的已确认 Context，不能由模型无依据猜测。

### T013 — Tool Schema Validator — execution boundary

- **ID:** T013
- **Title:** Tool Schema Validator — execution boundary
- **Phase:** Phase 2
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T012
- **Target PR:** PR-03
- **Goal:** 在真实 dispatch 前建立统一校验入口，复用原 schema。
- **Acceptance Criteria:** Local/MCP 均不能绕过校验；校验失败时执行计数为零；保留工具已有语义校验。
- **Tests:** spy 验证失败不 dispatch、合法参数只执行一次；现有只读 Agent 回归。
- **Out of Scope:** 新 Tool Registry、重写 Agent Loop、开启写能力。
- **Notes:** 下一 Task；核对 ToolCallAgent.execute_tool 与 ToolCollection.execute，只在批准 PR-03 后实现。

### T014 — Required / basic type / enum validation

- **ID:** T014
- **Title:** Required / basic type / enum validation
- **Phase:** Phase 2
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T013
- **Target PR:** PR-03
- **Goal:** 校验当前工具池需要的 schema 规则。
- **Acceptance Criteria:** required、string/integer/number/boolean/object/array、enum 和 additionalProperties 按实际 schema 生效；bool 不误当 integer；不隐式强转关键参数。
- **Tests:** 缺字段、null、错误类型、非法枚举、额外字段和合法嵌套输入矩阵。
- **Out of Scope:** 自建完整 JSON Schema 引擎。
- **Notes:** 复用成熟能力；不支持的 schema 语义必须显式报告，不能静默放行，MCP 边界由 T020 验收。

### T015 — Invalid JSON handling

- **ID:** T015
- **Title:** Invalid JSON handling
- **Phase:** Phase 2
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T013
- **Target PR:** PR-03
- **Goal:** 将解析失败变成可恢复、可定位的校验反馈。
- **Acceptance Criteria:** 畸形 JSON、顶层 list/null/scalar 均不执行；错误关联原 call ID；合法空 object 依 schema 判断。
- **Tests:** 畸形/截断 JSON、非 object、空参数及后续修正回归。
- **Out of Scope:** 自动猜测或静默修复关键参数。
- **Notes:** 复用现有 JSON 解析，补统一分类和消息协议；已有 try/except 不等于本项完成。

### T016 — Missing parameter detection / clarification trigger

- **ID:** T016
- **Title:** Missing parameter detection / clarification trigger
- **Phase:** Phase 2
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T014, T015
- **Target PR:** PR-03
- **Goal:** 缺关键参数时由执行器强制澄清。
- **Acceptance Criteria:** 缺 repository、path、issue_number 等必需字段可定位；无可信 Context 时询问；回答前零执行；取消/空回复保持不执行。
- **Tests:** 不同缺参组合、缺仓库入口、取消、错误回复；断言不是靠模型主动选 AskHuman。
- **Out of Scope:** 模型补猜仓库、目标路径或授权。
- **Notes:** 复用 CLI AskHuman 交互；服务端非阻塞适配另见 T057。

### T017 — Clarification response merge

- **ID:** T017
- **Title:** Clarification response merge
- **Phase:** Phase 2
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T016
- **Target PR:** PR-03
- **Goal:** 将用户回复合并到对应 pending call。
- **Acceptance Criteria:** 保存 call ID、原参数和字段来源；仅更新明确回答字段；冲突或歧义再次询问；用户显式修改才覆盖已有关键值。
- **Tests:** 多字段/多轮回答、冲突回答、多个 pending call、回复错配隔离。
- **Out of Scope:** 从外部 Issue/网页提取用户授权。
- **Notes:** 不新增长期 slot database；仅任务内状态。

### T018 — Re-validation and exactly-once resume

- **ID:** T018
- **Title:** Re-validation and exactly-once resume
- **Phase:** Phase 2
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T017
- **Target PR:** PR-03
- **Goal:** 补参后重新校验再恢复原调用。
- **Acceptance Criteria:** 每次 merge 后重走同一 validator；仍无效则不执行；有效时一次 dispatch；tool result 匹配原 call ID。
- **Tests:** 无效→有效、多次无效、重复回复、并列 tool calls、取消后迟到回复。
- **Out of Scope:** 用澄清回复跳过安全门禁。
- **Notes:** 参数澄清不等于副作用批准；写入仍需 Phase 5。

### T019 — Repository Context parameter reuse

- **ID:** T019
- **Title:** Repository Context parameter reuse
- **Phase:** Phase 2
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T018, T004
- **Target PR:** PR-03
- **Goal:** 复用用户已确认的 repo/ref 等参数并追踪来源。
- **Acceptance Criteria:** 同任务后续调用继承明确 Context；不用重复询问；任务/仓库切换不串值；冲突不静默覆盖。
- **Tests:** repo/ref 继承、缺失、显式切换、不同任务隔离、无来源值拒绝。
- **Out of Scope:** 把模型推测或 schema default 当成用户关键参数。
- **Notes:** T004 是已有工具静态绑定；此项只补验证/澄清路径的增量。

### T020 — Local Tool / MCP Tool schema compatibility

- **ID:** T020
- **Title:** Local Tool / MCP Tool schema compatibility
- **Phase:** Phase 2
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T014, T018
- **Target PR:** PR-03
- **Goal:** 对接 BaseTool.parameters 与真实 MCP inputSchema。
- **Acceptance Criteria:** Local 行为兼容；MCP 原始名称/schema 可追踪；记录支持的 dialect/keywords；不支持规则有明确拒绝或受控处理。
- **Tests:** 现有五工具回归；真实隔离 MCP server 的 schema discovery 与合法/非法调用，非法参数不抵达 server。
- **Out of Scope:** 假装支持任意 JSON Schema、替换 GitHub Adapter。
- **Notes:** 先 fixture 后真实 server；MCP 连接可靠性另由 T035/T038 验收。

### T021 — Parameter regression tests / real E2E validation

- **ID:** T021
- **Title:** Parameter regression tests / real E2E validation
- **Phase:** Phase 2
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T015, T016, T017, T018, T019, T020
- **Target PR:** PR-03
- **Goal:** 验收完整 validation→clarification→execution 闭环。
- **Acceptance Criteria:** 回归全通过；真实模型缺参→用户补参→真实 GitHub/MCP 执行有脱敏证据；无效输入未执行；原三类调查仍可用。
- **Tests:** deterministic 参数矩阵；真实 repo 缺失、path 缺失、错误类型修正和合法直接执行 E2E。
- **Out of Scope:** Benchmark 提升承诺、PR-04 Router。
- **Notes:** PR-03 出口；使用固定版本/模型/工具池记录，未跑的 case 标 NOT VERIFIED。

## Phase 3 — Tool Routing

Top-K 只影响候选 schema，不授予工具权限、不改变动态 Agent Loop。Top-K = 3 仅是实验起点；K_business 与含控制工具的 K_total 分开记录。仅使用 in-memory embedding index，不引入 Qdrant。

### T022 — Unified Tool Metadata

- **ID:** T022
- **Title:** Unified Tool Metadata
- **Phase:** Phase 3
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T021
- **Target PR:** PR-04
- **Goal:** 在已有工具定义上统一最小元数据。
- **Acceptance Criteria:** name/description/schema 复用原定义；capability/examples/source 可读取；Local/MCP 身份唯一且不丢原名称；risk metadata placeholder 明确待 Phase 5 生效。
- **Tests:** 两来源一致性、重名/名称清洗冲突、schema 引用与版本变化测试。
- **Out of Scope:** 第二套 Registry、把 risk placeholder 当执行授权。
- **Notes:** 含 capability / examples metadata、source metadata；风险占位缺省不应允许写。

### T023 — In-memory embedding index

- **ID:** T023
- **Title:** In-memory embedding index
- **Phase:** Phase 3
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T022
- **Target PR:** PR-04
- **Goal:** 为小型工具池建立可重建内存索引。
- **Acceptance Criteria:** 模型/向量版本可追踪；工具新增、删除、schema/metadata 变化使索引更新；失败可明确反馈。
- **Tests:** 固定向量 fixture 检索、空工具池、更新/失效测试；真实 embedding smoke。
- **Out of Scope:** Qdrant、持久向量服务、大规模检索平台。
- **Notes:** 先固定真实 embedding 方案和成本记录，避免为凑工具数量引入新集成。

### T024 — Semantic tool retrieval

- **ID:** T024
- **Title:** Semantic tool retrieval
- **Phase:** Phase 3
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T023
- **Target PR:** PR-04
- **Goal:** 按任务及当前有效 Observation 检索候选能力。
- **Acceptance Criteria:** 查询包含原任务和最新相关状态；下一步可改变候选；输出工具名/分数可复核。
- **Tests:** 同任务不同 Observation 的候选变化、同义表达、无匹配案例；真实 embedding 检索。
- **Out of Scope:** 按 Intent 写死工具序列。
- **Notes:** embedding 调用和耗时进入实验成本；不得只检索重复 next_step_prompt。

### T025 — Top-K candidate selection

- **ID:** T025
- **Title:** Top-K candidate selection
- **Phase:** Phase 3
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T024
- **Target PR:** PR-04
- **Goal:** 只向本轮 LLM 暴露候选业务工具 schema。
- **Acceptance Criteria:** 保持完整注册表和 cleanup；K 可实验；候选少于 K 正常；无匹配安全澄清/重检索；原 Agent 默认全量行为兼容。
- **Tests:** K 边界、少工具、空召回、注册表不变、候选外调用仍经独立许可/校验门禁。
- **Out of Scope:** Top-K 作为权限系统、固定 K=3。
- **Notes:** 候选入口以当前 ToolCallAgent.think 为核对起点，不复制整套循环。

### T026 — Required control tools preservation

- **ID:** T026
- **Title:** Required control tools preservation
- **Phase:** Phase 3
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T025
- **Target PR:** PR-04
- **Goal:** 保留 terminate 与状态需要的澄清/确认控制入口。
- **Acceptance Criteria:** 控制工具不被业务 Top-K 挤出；K 统计口径明确；缺参时执行器仍强制询问。
- **Tests:** K=1、零业务候选、待澄清、终止回合和并列调用回归。
- **Out of Scope:** 依靠模型是否选 ask_human 决定安全。
- **Notes:** 控制入口与业务检索分开计数；不额外暴露危险工具。

### T027 — Routing event logging

- **ID:** T027
- **Title:** Routing event logging
- **Phase:** Phase 3
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T025, T026
- **Target PR:** PR-04
- **Goal:** 留存可解释的检索与选择证据。
- **Acceptance Criteria:** 记录脱敏 query、候选、分数、selected tool、K、schema/index 版本及运行关联标识；缺失选择也可记录。
- **Tests:** 事件字段断言、可串联单次运行、凭据脱敏和真实路由日志抽查。
- **Out of Scope:** 大型 observability、隐藏推理采集、完整 Trace UI。
- **Notes:** 先最小 routing 事件；T053/T054 汇总并稳定完整事件契约。

### T028 — Routing dataset

- **ID:** T028
- **Title:** Routing dataset
- **Phase:** Phase 3
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T011, T022
- **Target PR:** PR-04
- **Goal:** 建立工具选择标注与独立评估样本。
- **Acceptance Criteria:** 从 v0 扩充多步、近似能力、缺参和无匹配任务；标注可接受工具集合、关键 slot；实验集与评估集分离。
- **Tests:** 标注字段/工具存在性校验；人工核查歧义和证据；固定版本。
- **Out of Scope:** 用 16 条 v0 全当已跑 Benchmark。
- **Notes:** 工具等价选择可多标签，避免只因顺序不同判错。

### T029 — Top-K experiments / full-tool baseline comparison

- **ID:** T029
- **Title:** Top-K experiments / full-tool baseline comparison
- **Phase:** Phase 3
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T027, T028
- **Target PR:** PR-04
- **Goal:** 以同工具池对照选择 K。
- **Acceptance Criteria:** 比较至少多个 K（含 3）与全量工具；同模型/任务/工具/预算/Observation 归一化；记录原始运行、失败及选 K 依据。
- **Tests:** 真实工具和真实模型成组实验；重放运行配置，核验 full-tool 未经过检索过滤。
- **Out of Scope:** 预定最优 K、把新增工具收益归因路由。
- **Notes:** 这是路由阶段实验；最终版本仍需 Phase 8 对比。

### T030 — Tool Selection Accuracy / token / latency comparison

- **ID:** T030
- **Title:** Tool Selection Accuracy / token / latency comparison
- **Phase:** Phase 3
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T029
- **Target PR:** PR-04
- **Goal:** 给出路由准确率和成本权衡。
- **Acceptance Criteria:** 明确候选召回与最终选择准确率的分母；记录 provider usage、embedding/重试成本和端到端耗时；负收益也保留。
- **Tests:** 从实际 run 记录计算指标；抽查原始调用和计算结果；回归 T021 调查链路。
- **Out of Scope:** 预写提升百分比、只报告成功样本。
- **Notes:** PR-04 出口；正式 resume metrics 仅从最终 Benchmark 提取。

## Phase 4 — Reliability

已有 GitHub HTTP timeout、错误转 Observation 和上游局部重试可复用；以下是统一策略与真实 MCP 的增量。无万能 fallback；timeout 不代表远端副作用已取消。

### T031 — Tool result status normalization / MCP isError preservation

- **ID:** T031
- **Title:** Tool result status normalization / MCP isError preservation
- **Phase:** Phase 4
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T030
- **Target PR:** PR-05
- **Goal:** 保留真实成功、失败和未知状态。
- **Acceptance Criteria:** Local 结果与 MCP isError 在字符串化前归一；业务失败不因未抛异常被记成功；Observation 保留必要错误类别。
- **Tests:** ToolResult/string/dict、isError=true/false、异常、多内容响应和截断后状态回归。
- **Out of Scope:** 重建结果框架、从日志文案判断成功。
- **Notes:** 优先复用 ToolResult；不得等字符串化后再猜 isError。

### T032 — Per-tool timeout policy

- **ID:** T032
- **Title:** Per-tool timeout policy
- **Phase:** Phase 4
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T031
- **Target PR:** PR-05
- **Goal:** 给实际启用工具明确执行预算。
- **Acceptance Criteria:** 工具/连接 deadline 明确且有界；超时进入可判别 Observation；区分取消与结果未知；保持现有合理 HTTP 限制。
- **Tests:** 慢 Local/MCP、连接超时、任务预算耗尽、cleanup 有界测试。
- **Out of Scope:** 宣称 asyncio timeout 能终止所有同步代码或远端动作。
- **Notes:** 公共策略增量；不为未启用的任意 Shell 扩大范围。

### T033 — Retry classification / idempotent read retry

- **ID:** T033
- **Title:** Retry classification / idempotent read retry
- **Phase:** Phase 4
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T032
- **Target PR:** PR-05
- **Goal:** 仅重试可恢复且幂等的读取。
- **Acceptance Criteria:** transient 与 auth/schema/permission/预算错误分开；重试次数与总预算有界；遵守限流等待边界；永久错误不重复请求。
- **Tests:** 429/5xx/网络中断、401/403、非法参数、耗尽预算；断言 attempts 与终态。
- **Out of Scope:** 无限重试、隐式嵌套重试放大。
- **Notes:** 核对上游 LLM retry 与 SDK attempt；复用已有能力，避免双重重试。

### T034 — Non-idempotent action safety

- **ID:** T034
- **Title:** Non-idempotent action safety
- **Phase:** Phase 4
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T033
- **Target PR:** PR-05
- **Goal:** 在接入写操作前约束非幂等重放。
- **Acceptance Criteria:** 未知结果/timeout 的写操作禁止自动重放；只有明确去重或结果核验机制才可恢复；政策与读工具区分。
- **Tests:** 超时但已执行、网络回包丢失、策略缺失均不重试的测试。
- **Out of Scope:** 将“未收到响应”视为“未执行”。
- **Notes:** 此阶段仅策略和测试；写能力到 PR-06 才启用。

### T035 — MCP failure handling / connection and lifecycle validation

- **ID:** T035
- **Title:** MCP failure handling / connection and lifecycle validation
- **Phase:** Phase 4
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T031, T032, T033
- **Target PR:** PR-05
- **Goal:** 验证真实 MCP 的连接、发现、调用和清理。
- **Acceptance Criteria:** 至少一种实际使用 transport 和真实无副作用 server 验证；断连有清晰状态；重连/重新装配不重复或保留失效工具；能力/schema 变化同步检索索引。
- **Tests:** 真实 stdio 或 SSE server 的 connect/list/call/close、异常 cleanup、重连；记录 server/SDK 版本。
- **Out of Scope:** 声称所有 MCP server/transport 均兼容。
- **Notes:** PR-01/PR-02 的 MCP NOT VERIFIED 由本项补足；测试 doubles 不替代真实 transport。

### T036 — Capability-equivalent fallback

- **ID:** T036
- **Title:** Capability-equivalent fallback
- **Phase:** Phase 4
- **Priority:** P1
- **Status:** TODO
- **Depends On:** T035
- **Target PR:** PR-05
- **Goal:** 仅在确有等价能力时提供替代路径。
- **Acceptance Criteria:** 先证明输入、输出证据、权限和副作用语义等价；记录原因和替代工具；无等价能力明确失败。
- **Tests:** 等价读能力、非等价候选、权限不同、无候选、替代失败测试。
- **Out of Scope:** 万能 fallback、用不相关工具伪造成功。
- **Notes:** 可延后；没有真实等价工具对时保留 TODO/说明，不为完成任务增加集成；P0 不依赖本项。

### T037 — Reliability regression tests

- **ID:** T037
- **Title:** Reliability regression tests
- **Phase:** Phase 4
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T031, T032, T033, T034, T035
- **Target PR:** PR-05
- **Goal:** 固定基本可靠性失败矩阵。
- **Acceptance Criteria:** 状态、timeout、retry、MCP 错误/断连和未知写结果全部有确定性断言；失败后 Agent 可继续或有界退出。
- **Tests:** targeted unit/integration tests 和 TaskPilot/基线回归；若做 T036 则加入 fallback 回归。
- **Out of Scope:** 只测 happy path、将测试注入视为真实网络 E2E。
- **Notes:** 单个失败不必让整任务失败，但最终结果需说明证据缺口。

### T038 — Real failure E2E

- **ID:** T038
- **Title:** Real failure E2E
- **Phase:** Phase 4
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T037
- **Target PR:** PR-05
- **Goal:** 在真实工具/transport 验证故障行为。
- **Acceptance Criteria:** 真实 MCP server 返回 isError、断连/超时场景有记录；真实 GitHub 不存在资源等只读失败有记录；尝试有界且不误报成功。
- **Tests:** 受控真实进程终止/慢响应及真实读取失败 E2E，记录 fault 注入方式和恢复/终止证据。
- **Out of Scope:** 对不受控服务制造故障、写操作盲重试。
- **Notes:** PR-05 出口；不得把 fixture 失败矩阵冒充真实 failure E2E。

## Phase 5 — Controlled Developer Actions

所有副作用操作必须经过 `Validation → Risk Check → Human Confirmation → Execute`，由代码执行路径强制。最小 P0 是明确隔离 workspace 的文件写入；GitHub Issue 创建是可删减的 P1 等价业务扩展。外部内容不能授予执行权限。

### T039 — Controlled write capability / risk level policy

- **ID:** T039
- **Title:** Controlled write capability / risk level policy
- **Phase:** Phase 5
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T021, T034, T038
- **Target PR:** PR-06
- **Goal:** 只暴露最小受控 workspace 写入能力。
- **Acceptance Criteria:** 定义读/写/破坏性风险与参数关联；仅批准的文件动作可执行；未知风险拒绝；替换 T022 的风险占位语义。
- **Tests:** 读写混合命令、模型直接调用、未知风险、外部内容诱导测试；全部经过共同执行边界。
- **Out of Scope:** 任意 Shell、系统目录、删除/自动修复能力。
- **Notes:** 启用写工具必须与 T040–T043/T045 同 PR 交付，不能先上线裸写。

### T040 — Human Confirmation

- **ID:** T040
- **Title:** Human Confirmation
- **Phase:** Phase 5
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T039
- **Target PR:** PR-06
- **Goal:** 每次副作用前展示具体操作并等待用户决定。
- **Acceptance Criteria:** 显示工具、规范化目标、参数摘要与风险；明确批准才继续；拒绝/取消/超时均不执行。
- **Tests:** approve/reject/cancel、空回复、模糊同意、未确认直接调用；执行计数断言。
- **Out of Scope:** prompt-only 安全、模型代替人类批准。
- **Notes:** CLI 复用人工交互接口；API 适配在 T057。

### T041 — Approval / rejection binding

- **ID:** T041
- **Title:** Approval / rejection binding
- **Phase:** Phase 5
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T040
- **Target PR:** PR-06
- **Goal:** 将决定绑定到唯一操作和规范化参数。
- **Acceptance Criteria:** task/call/操作/参数变更使原批准失效；拒绝不能被迟到回复或模型重试覆盖；批准一次消费。
- **Tests:** 修改 path/body、跨 task 回复、过期/重复批准、拒绝后重放、确认与执行间目标变化。
- **Out of Scope:** 会话级永久授权、从 Issue 文本提取批准。
- **Notes:** 人工批准也不能绕过 allowlist；参数必须在执行前再次核对。

### T042 — Workspace allowlist / path validation

- **ID:** T042
- **Title:** Workspace allowlist / path validation
- **Phase:** Phase 5
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T039
- **Target PR:** PR-06
- **Goal:** 将文件操作约束在用户明确选择的隔离 workspace。
- **Acceptance Criteria:** 校验真实路径与父目录；拒绝越界、系统目录、路径穿越；不存在文件的父路径也检查；执行目标与批准一致。
- **Tests:** 相对/绝对路径、../、相似目录前缀、不存在父目录、允许范围内写入。
- **Out of Scope:** 任意宿主路径、仅字符串前缀检查。
- **Notes:** 配合 T043 验证 symlink 与确认后路径替换，不能把容器存在等同目录安全。

### T043 — Symlink boundary tests

- **ID:** T043
- **Title:** Symlink boundary tests
- **Phase:** Phase 5
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T041, T042
- **Target PR:** PR-06
- **Goal:** 验证符号链接与时序边界不可绕过路径政策。
- **Acceptance Criteria:** 指向 workspace 外的文件/目录链接均拒绝；确认后替换链接不能越界；安全链接政策明确且测试一致。
- **Tests:** 使用真实临时隔离目录测试内部/外部 symlink、嵌套链接、确认后替换；零边界外写入。
- **Out of Scope:** 以 mock 路径检查代替真实文件系统测试。
- **Notes:** 若平台无法保证安全执行，则拒绝该路径，不放宽边界。

### T044 — GitHub Issue creation or equivalent additional action

- **ID:** T044
- **Title:** GitHub Issue creation or equivalent additional action
- **Phase:** Phase 5
- **Priority:** P1
- **Status:** TODO
- **Depends On:** T041, T043, T045
- **Target PR:** PR-06
- **Goal:** 按真实需求增加一个外部受控写动作。
- **Acceptance Criteria:** 在明确授权测试仓库创建 Issue；repo/title/body 校验、风险确认、绑定和去重全部生效；回传真实 URL。
- **Tests:** 测试仓库 real write E2E、拒绝路径和重复请求；fixture 只覆盖稳定边界。
- **Out of Scope:** 任意仓库写、自动 merge、生产部署。
- **Notes:** 时间不够可删除；T039 的受控文件写入已满足 MVP 等价动作，不要求两个都实现。

### T045 — Duplicate execution protection

- **ID:** T045
- **Title:** Duplicate execution protection
- **Phase:** Phase 5
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T034, T041, T042
- **Target PR:** PR-06
- **Goal:** 阻止重复确认、重试或恢复导致二次副作用。
- **Acceptance Criteria:** 同一操作标识至多一次有效提交；重复请求返回已知状态；未知结果先核验；不能因 timeout 自动生成新操作重放。
- **Tests:** 并发/重复提交、回包丢失、恢复重试与跨运行状态边界；断言真实副作用次数。
- **Out of Scope:** 引入 Redis/分布式事务平台。
- **Notes:** 明确去重状态的作用域和重启限制；无法安全核验时拒绝自动重放。

### T046 — Real write E2E

- **ID:** T046
- **Title:** Real write E2E
- **Phase:** Phase 5
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T039, T040, T041, T042, T043, T045
- **Target PR:** PR-06
- **Goal:** 验证最小受控动作的完整安全闭环。
- **Acceptance Criteria:** 在隔离 workspace 真实写入一次；内容/目标与确认一致；拒绝、非法参数、越界和重复请求不产生额外写入。
- **Tests:** 真实模型→校验→风险→人类批准→文件核验 E2E；保留脱敏 trace 和反例证据。
- **Out of Scope:** 只用 Mock 写成功、在用户未授权位置写入。
- **Notes:** PR-06 出口；若交付 T044 则额外核验真实 Issue URL；不以可选动作阻塞 P0。

## Phase 6 — Memory

复用现有 Memory；P0 只保证完整工具回合、关键上下文和连续运行。Conversation Summary 与 token-pressure trigger 为 P1。无真实需求不建设长期记忆。

### T047 — Tool-call round-safe window / prevent dangling tool result

- **ID:** T047
- **Title:** Tool-call round-safe window / prevent dangling tool result
- **Phase:** Phase 6
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T046
- **Target PR:** PR-07
- **Goal:** 按完整工具回合裁剪现有窗口。
- **Acceptance Criteria:** assistant tool_calls 与对应 tool results 成组保留/移除；多工具和图片消息关联有效；所有消息追加路径遵守窗口规则。
- **Tests:** 窗口边界、批量 calls、缺失结果、图片和长 Observation 回归；请求消息协议检查。
- **Out of Scope:** 新 Memory 平台、简单按消息条数截断调用对。
- **Notes:** 核对 Memory.add_message/add_messages 与 think 的直接追加路径；不重写 Agent Loop。

### T048 — Preserve task goal / critical parameters

- **ID:** T048
- **Title:** Preserve task goal / critical parameters
- **Phase:** Phase 6
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T047, T019
- **Target PR:** PR-07
- **Goal:** 裁剪后保留原任务和用户已确认关键参数。
- **Acceptance Criteria:** 原目标、repo/ref/path 及来源可恢复；外部 Observation 不能覆盖用户参数；新任务不误继承。
- **Tests:** 长对话裁剪、用户更正、多仓库切换、恶意外部内容和下一步参数核验。
- **Out of Scope:** 推测 slot、Entity Memory、Knowledge Graph Memory。
- **Notes:** 批准只绑定具体操作，不因 Memory 保留而扩展授权。

### T049 — Conversation summary

- **ID:** T049
- **Title:** Conversation summary
- **Phase:** Phase 6
- **Priority:** P1
- **Status:** TODO
- **Depends On:** T048
- **Target PR:** PR-07
- **Goal:** 在确有长对话需求时压缩历史。
- **Acceptance Criteria:** 摘要有原始来源；保留目标/关键约束与证据引用；不伪造结果或批准；摘要失败保留安全窗口行为。
- **Tests:** 长会话摘要前后事实对照、失败/空摘要、来源遗漏、批准信息不扩权测试。
- **Out of Scope:** 长期向量检索、Redis Memory。
- **Notes:** 可延后；P0 不依赖摘要；真实需要时再实现。

### T050 — Token-pressure trigger

- **ID:** T050
- **Title:** Token-pressure trigger
- **Phase:** Phase 6
- **Priority:** P1
- **Status:** TODO
- **Depends On:** T049
- **Target PR:** PR-07
- **Goal:** 按测得上下文压力触发摘要。
- **Acceptance Criteria:** 明确预算和估算口径；计入工具 schema 与摘要成本；避免每轮反复压缩；超限仍可有界失败。
- **Tests:** 阈值上下边界、超大单条 Observation、摘要失败、重复触发和成本记录。
- **Out of Scope:** 宣称估算等同 provider 实测、无限压缩循环。
- **Notes:** 可与 T049 一起删除；PR-02 的字段归一化不能视为本项 DONE。

### T051 — Multi-turn regression

- **ID:** T051
- **Title:** Multi-turn regression
- **Phase:** Phase 6
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T047, T048
- **Target PR:** PR-07
- **Goal:** 验证窗口下的多轮调查和澄清。
- **Acceptance Criteria:** 多轮保留任务目标/已确认参数；无悬空 tool result；错误/澄清/控制工具混合场景仍正确。
- **Tests:** 长对话 deterministic 回归、真实模型多轮调查；若做 T049/T050 则纳入对应用例。
- **Out of Scope:** 将两轮 Memory 留存等同完整多轮正确性。
- **Notes:** 比较 E02 连续 run 的已知基线行为，只修改 TaskPilot 所需增量。

### T052 — Consecutive run lifecycle verification

- **ID:** T052
- **Title:** Consecutive run lifecycle verification
- **Phase:** Phase 6
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T035, T051
- **Target PR:** PR-07
- **Goal:** 验收连续任务的状态、预算和连接生命周期。
- **Acceptance Criteria:** 连续至少两次 run 均能调用真实工具；step 预算明确；GitHub client/MCP 连接可用且清理；跨任务 Context 不串联。
- **Tests:** 同会话继续、新任务、正常/异常/取消后再运行；真实 GitHub/MCP 生命周期检查。
- **Out of Scope:** 仅凭 state=IDLE 判断成功、未验证的全局并发支持。
- **Notes:** PR-07 出口；PR-01 的 Memory/累计 current_step 记录是 characterization，不是此项实现。

## Phase 7 — Productization

可按依赖拆分 PR-08a（Trace）、PR-08b（service/交互）、PR-08c（Docker/docs/deployment），均为计划标识而非已存在的 GitHub PR。先完成下面 P0，再决定是否做 UI/在线托管增强。

### T053 — Structured Execution Trace

- **ID:** T053
- **Title:** Structured Execution Trace
- **Phase:** Phase 7
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T027, T038, T046, T052
- **Target PR:** PR-08a (Trace)
- **Goal:** 将真实执行事件串成可审计任务记录。
- **Acceptance Criteria:** 覆盖 task/step/routing/LLM/validation/clarification/approval/tool/retry/Observation/finish；准确区分失败、拒绝、未知和成功；输出前脱敏。
- **Tests:** 三类调查、缺参、失败、受控写和取消的 trace 完整性；敏感值不落日志。
- **Out of Scope:** 大型 observability、记录模型隐藏推理。
- **Notes:** 复用现有日志；T027 routing 事件与 GitHub 日志已存在，补统一结构和全链路关联。

### T054 — Stable task / step / tool IDs and trace event schema

- **ID:** T054
- **Title:** Stable task / step / tool IDs and trace event schema
- **Phase:** Phase 7
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T053
- **Target PR:** PR-08a (Trace)
- **Goal:** 定义可维护的事件契约和稳定关联标识。
- **Acceptance Criteria:** schema 版本、task_id/step_id/tool_call_id/attempt 等唯一且可关联；澄清恢复与重试沿用关联；不混淆工具身份和调用 ID。
- **Tests:** schema 校验、重复/缺字段、重试/恢复链、连续任务 ID 隔离。
- **Out of Scope:** 通用事件平台或消息队列。
- **Notes:** 事件只表示真实发生动作；没有 fallback 就不生成虚假 fallback 事件。

### T055 — Token / latency attribution

- **ID:** T055
- **Title:** Token / latency attribution
- **Phase:** Phase 7
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T054
- **Target PR:** PR-08a (Trace)
- **Goal:** 按任务和调用归因成本。
- **Acceptance Criteria:** provider usage 与估算分开；包含失败/重试/embedding/摘要；端到端、LLM、工具、人类等待分别计量；缺失 usage 标未知。
- **Tests:** 多 attempt、连续 run、部分无 usage、等待/取消的计时和汇总一致性。
- **Out of Scope:** 用共享 LLM 累计差当可靠并发计数、编造缺失 token。
- **Notes:** 不建设多租户统计；为最终 Benchmark 提供可核验原始数据。

### T056 — FastAPI service layer

- **ID:** T056
- **Title:** FastAPI service layer
- **Phase:** Phase 7
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T052, T054
- **Target PR:** PR-08b (service/demo)
- **Goal:** 提供薄任务服务，复用现有 Agent。
- **Acceptance Criteria:** 支持启动、查询结果/状态、取消及生命周期清理；输入显式校验；最终成功按证据判断，不能照搬 terminate(success)。
- **Tests:** API 生命周期、非法输入、异常/取消、隔离 Context 和真实只读任务 smoke。
- **Out of Scope:** 重建 Runtime、多租户、Workflow Engine。
- **Notes:** 保留 CLI；阻塞人机交互不得直接在事件循环调用，需 T057 一起验收。

### T057 — Non-blocking clarification / confirmation adapter

- **ID:** T057
- **Title:** Non-blocking clarification / confirmation adapter
- **Phase:** Phase 7
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T018, T041, T056
- **Target PR:** PR-08b (service/demo)
- **Goal:** 在 service 挂起/恢复人工交互。
- **Acceptance Criteria:** 返回 pending 状态和操作绑定 ID；回复只恢复对应调用；拒绝/取消/迟到/重复回复安全；等待期间服务仍可响应其他请求。
- **Tests:** 缺参和批准两条链路；多个 pending 请求隔离；取消、重复回复、超时及真实 E2E。
- **Out of Scope:** 企业审批平台、将 AskHuman.input 直接包 HTTP。
- **Notes:** 一个任务等待时不得发送含未闭合 tool call 的新模型请求。

### T058 — Simple Demo UI

- **ID:** T058
- **Title:** Simple Demo UI
- **Phase:** Phase 7
- **Priority:** P1
- **Status:** TODO
- **Depends On:** T055, T057
- **Target PR:** PR-08b (service/demo)
- **Goal:** 提供最小任务提交、结果和人工交互界面。
- **Acceptance Criteria:** 可输入 repo/任务、查看来源与状态、补参、明确批准/拒绝具体操作；错误状态清晰。
- **Tests:** 浏览器 smoke：调查、缺参、拒绝/批准与失败结果；确认目标和后端一致。
- **Out of Scope:** UI 框架重构、复杂仪表盘。
- **Notes:** 时间不足可删除，P0 用 CLI/API 与可复现部署交付；若公开则必须通过 T061。

### T059 — Docker / reproducible startup

- **ID:** T059
- **Title:** Docker / reproducible startup
- **Phase:** Phase 7
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T056, T057
- **Target PR:** PR-08c (delivery)
- **Goal:** 在干净环境运行 TaskPilot。
- **Acceptance Criteria:** 固定依赖/运行条件；外部注入凭据；镜像不含 secrets；文档命令从 clean checkout 启动并可完成真实只读任务。
- **Tests:** 干净镜像 build/run、健康检查、缺配置错误、启动/停止及真实 GitHub smoke；不依赖宿主 .venv。
- **Out of Scope:** Kubernetes、把上游 Dockerfile 存在当验证通过。
- **Notes:** 明确支持的平台；记录构建版本、配置键名和运行证据，不提交值。

### T060 — Final README / architecture docs

- **ID:** T060
- **Title:** Final README / architecture docs
- **Phase:** Phase 7
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T055, T057, T059
- **Target PR:** PR-08c (delivery)
- **Goal:** 交付与实际功能一致的使用和架构说明。
- **Acceptance Criteria:** README 含安装/配置/启动/示例/限制/测试/评测入口；架构说明当前链路和安全边界；保留上游来源和许可证；文档链接可用。
- **Tests:** 从 clean checkout 按 README 复现；链接/命令核查；架构对照已合并实现。
- **Out of Scope:** 复制 PRD、改写历史分析以伪装当时已实现。
- **Notes:** 评测完成后由 T073 补真实数字；不提前填写结果。

### T061 — Public demo safety restrictions

- **ID:** T061
- **Title:** Public demo safety restrictions
- **Phase:** Phase 7
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T046, T057, T059
- **Target PR:** PR-08c (delivery)
- **Goal:** 确保可部署 Demo 的暴露面受控。
- **Acceptance Criteria:** 禁止任意 Shell/系统目录；仓库/workspace allowlist、凭据最小权限、输入/输出脱敏、资源预算及基础访问限制可验证；公网默认关闭写或限制到明确测试目标并逐次确认。
- **Tests:** 外部内容诱导、越界、未确认写、任意工具调用、预算耗尽和泄密回归；部署配置核查。
- **Out of Scope:** 企业 auth、多租户权限体系、公开任意代码执行。
- **Notes:** 可使用现有托管访问控制或单实例简单限制；安全条件不满足则不公开该能力。

### T062 — Reproducible deployment

- **ID:** T062
- **Title:** Reproducible deployment
- **Phase:** Phase 7
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T059, T060, T061
- **Target PR:** PR-08c (delivery)
- **Goal:** 交付可复现部署路径，满足 MVP 运行出口。
- **Acceptance Criteria:** 有明确的构建/启动/配置/停机/清理命令和验证记录；第三方干净环境可运行；部署版本和限制可追踪。
- **Tests:** 按文档在干净容器环境完整启动并完成调查/缺参/安全受控动作验收。
- **Out of Scope:** 生产级 SRE、多区域容灾、自动生产部署 Agent。
- **Notes:** MVP 接受 online demo 或 reproducible deployment；此 P0 路径必须完成，不因未购买托管而阻塞。

### T063 — Online deployment

- **ID:** T063
- **Title:** Online deployment
- **Phase:** Phase 7
- **Priority:** P1
- **Status:** TODO
- **Depends On:** T062
- **Target PR:** PR-08c (delivery)
- **Goal:** 将安全 Demo 部署到明确选择的托管环境。
- **Acceptance Criteria:** 可访问 URL、部署版本、只读或受限写配置可核查；与可复现部署一致；费用/凭据由真实配置提供。
- **Tests:** 从外部访问健康检查、真实只读调查和安全拒绝；记录托管限制。
- **Out of Scope:** 擅自购买资源、未经授权写公网仓库。
- **Notes:** 可延后；目标平台/账号未确定时实施阶段明确记录阻塞；不影响已验收的 T062。

### T064 — Richer UI / advanced trace visualization

- **ID:** T064
- **Title:** Richer UI / advanced trace visualization
- **Phase:** Phase 7
- **Priority:** P2
- **Status:** OPTIONAL
- **Depends On:** T058, T054
- **Target PR:** PR-08c (delivery)
- **Goal:** 在有余量时增强 trace 阅读体验。
- **Acceptance Criteria:** 可筛选 step/tool/attempt 并定位错误和引用；数据来自真实事件；不改变执行语义。
- **Tests:** UI smoke 与原 trace 字段/计数一致性。
- **Out of Scope:** 新工具集成、大型可观测性平台。
- **Notes:** OPTIONAL；最先删除的范围，不阻塞任何 P0。

## Phase 8 — Final Evaluation

PR-09 是计划中的最终评测 PR；不挤入 PR-03。所有指标来自真实执行，先冻结口径和数据再运行；未运行或缺失证据标 NOT VERIFIED，禁止预写“提升 xx%”。

### T065 — Final benchmark dataset

- **ID:** T065
- **Title:** Final benchmark dataset
- **Phase:** Phase 8
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T028, T062
- **Target PR:** PR-09 (evaluation/docs)
- **Goal:** 冻结可复现、带标注的最终任务集。
- **Acceptance Criteria:** 覆盖调查、缺参、错误参数、MCP、失败、受控动作和多轮；固定 repo/ref/Issue 快照或采集时间；明确期望证据、工具和 slot；与调参集分开。
- **Tests:** 数据 schema/引用有效性检查、人工标注复核、预先冻结评分口径。
- **Out of Scope:** 仅重复三个 Demo case、事后剔除失败题。
- **Notes:** 复用 v0 并补固定版本/标注；v0 DONE 不等于本项完成。

### T066 — Original OpenManus baseline

- **ID:** T066
- **Title:** Original OpenManus baseline
- **Phase:** Phase 8
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T003, T065
- **Target PR:** PR-09 (evaluation/docs)
- **Goal:** 运行固定上游原始装配的产品参照。
- **Acceptance Criteria:** 使用 commit 3309bf4e416fb1c74b008f3e86494439a31bad53；记录原工具池、环境、模型和限制；无对应能力/无法安全运行的任务明确标 unsupported/NOT VERIFIED，单列分母。
- **Tests:** 独立 checkout、真实 provider/工具运行；核验无 TaskPilot 实现混入；保存原始记录。
- **Out of Scope:** 为使 baseline 好看而改造上游、启用不受控危险动作。
- **Notes:** 安全可比子集与全量可用性分开；不能把新增 GitHub 工具收益全归因 Top-K。

### T067 — Same-tool-pool full-tool baseline

- **ID:** T067
- **Title:** Same-tool-pool full-tool baseline
- **Phase:** Phase 8
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T030, T065
- **Target PR:** PR-09 (evaluation/docs)
- **Goal:** 隔离路由候选裁剪的影响。
- **Acceptance Criteria:** 与 TaskPilot 使用同模型、任务、工具池、参数校验、可靠性和 Memory，仅关闭 semantic candidate filtering；记录控制工具、预算和配置差异。
- **Tests:** 真实执行并断言全部允许工具 schema 被发送；多次运行使用相同口径和数据版本。
- **Out of Scope:** 与原始 OpenManus baseline 混为一组。
- **Notes:** 与 T066 分开报告；这组用于路由因果对照。

### T068 — TaskPilot comparison runs

- **ID:** T068
- **Title:** TaskPilot comparison runs
- **Phase:** Phase 8
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T055, T065, T066, T067
- **Target PR:** PR-09 (evaluation/docs)
- **Goal:** 运行最终版本并形成三组可比证据。
- **Acceptance Criteria:** 固定 TaskPilot revision、K、索引/模型/工具版本和采样参数；保留所有尝试、失败和限流；人工等待策略一致并单列。
- **Tests:** 真实 GitHub/MCP/受控文件系统集成；重复运行或明确样本/重复次数限制；审计 run manifest。
- **Out of Scope:** Mock 产品能力、只挑最佳 run。
- **Notes:** P1 未做则记录 disabled；所有对照统一相关配置，不能隐去成本。

### T069 — Tool Selection Accuracy / Task Success Rate

- **ID:** T069
- **Title:** Tool Selection Accuracy / Task Success Rate
- **Phase:** Phase 8
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T068
- **Target PR:** PR-09 (evaluation/docs)
- **Goal:** 以标注与真实证据计算选择和任务成功。
- **Acceptance Criteria:** 定义正确工具集合、每步/每任务分母及多工具判定；成功需满足证据要求；terminate(success) 不构成独立成功。
- **Tests:** 人工抽查评分与原始 trace、失败/unsupported 分母核对、确定性指标计算测试。
- **Out of Scope:** 将候选召回率当最终准确率、排除失败后报成功率。
- **Notes:** 报告样本数量、绝对结果和不确定性；不要求预设提升才能完成。

### T070 — Token Usage / End-to-end Latency

- **ID:** T070
- **Title:** Token Usage / End-to-end Latency
- **Phase:** Phase 8
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T055, T068
- **Target PR:** PR-09 (evaluation/docs)
- **Goal:** 对比完整执行成本与耗时。
- **Acceptance Criteria:** provider input/output、embedding、重试、摘要分别记账；缺失 usage 明示；报告端到端及分阶段延迟、人类等待、重复次数与聚合口径。
- **Tests:** 汇总值与原始 usage/时间戳抽样核对；失败 attempts 计入；可重复运行计算。
- **Out of Scope:** 只计最终成功请求、把估算混成实测。
- **Notes:** 不将 PR-02 >100k→约1.9k 的单次观察当整体 Benchmark 提升。

### T071 — Parameter / Slot metrics

- **ID:** T071
- **Title:** Parameter / Slot metrics
- **Phase:** Phase 8
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T065, T068
- **Target PR:** PR-09 (evaluation/docs)
- **Goal:** 测量参数提取与澄清质量。
- **Acceptance Criteria:** 定义字段级 precision/recall/F1 或明确等价指标；关键参数准确率、缺参检测、澄清后有效率与无依据猜测单列；标注来源可追踪。
- **Tests:** 基于真实调用与用户回复评分；缺参/错误类型/多轮改值样本；人工核验关键 slot。
- **Out of Scope:** 凭模型自评填分、把 schema 合法等同用户意图正确。
- **Notes:** 口径在 T065 冻结，报告实际样本与零分母处理。

### T072 — Failure analysis / benchmark reproducibility

- **ID:** T072
- **Title:** Failure analysis / benchmark reproducibility
- **Phase:** Phase 8
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T069, T070, T071
- **Target PR:** PR-09 (evaluation/docs)
- **Goal:** 解释失败并让结果可复跑。
- **Acceptance Criteria:** 按 routing/parameter/tool/MCP/context/permission/network 分类，附代表性证据；提供 manifest、命令、数据版本、评分脚本和脱敏结果；真实失败不被隐藏。
- **Tests:** 独立 clean 环境重跑代表性样本和指标计算；记录网络/模型漂移；核验无凭据入库。
- **Out of Scope:** 为补分扩大 Feature、未经授权升级上游。
- **Notes:** 验收阻塞缺陷回原 Task 修复并重测；非 P0 改进仅记后续建议，不自动开工。

### T073 — Final resume metrics / MVP exit review

- **ID:** T073
- **Title:** Final resume metrics / MVP exit review
- **Phase:** Phase 8
- **Priority:** P0
- **Status:** TODO
- **Depends On:** T060, T062, T072
- **Target PR:** PR-09 (evaluation/docs)
- **Goal:** 用真实结果完成最终交付并停止扩展。
- **Acceptance Criteria:** README/最终报告/简历数字均能追溯 run 和计算口径；复核所有 P0 DONE、Exit Criteria 全满足；不夸大收益；冻结 MVP revision。
- **Tests:** 核验数字和引用；运行当时全部 TaskPilot scope tests；按 Exit Criteria 核对真实 E2E、Docker、部署和文档证据。
- **Out of Scope:** 预写提升百分比、为了简历数字继续堆 Feature。
- **Notes:** 若未优于 baseline 就如实报告绝对指标与失败分析；完成即停止新增 Feature。

## Explicit Deferred / Non-goals

以下不进入当前 MVP，不为它们建立当前 TODO Task。仓库保留的上游文件不表示 TaskPilot 承诺交付对应能力。

- Multi-Agent
- Agent Registry
- Agent Discovery
- Workflow Designer
- Workflow Engine
- Qdrant
- Redis
- GraphRAG
- Knowledge Graph
- Long-term vector memory / Vector Long-term Memory
- Entity Memory / Knowledge Graph Memory / Redis Memory
- Enterprise auth
- Multi-tenancy
- Kubernetes
- Large observability platform
- Full coding agent
- Auto merge / production deployment agent

只有真实需求、证据和后续独立授权出现时才重新评估；不得为了填满工具池或展示架构主动加入。Intent classifier 与额外 Web/工具集成也不作为当前 MVP 依赖；已有能力按真实调查需要复用，不新增独立 Feature。

## Time-budget cuts

按以下顺序删减本次交付范围，保留 ID 和延期原因：

1. **P2：T064**，richer UI / advanced trace visualization。
2. **P1：T063、T058**，online hosting 和 simple Demo UI；保留 P0 CLI/API、安全策略和可复现部署。
3. **P1：T044、T036**，额外 GitHub Issue 写动作和 capability-equivalent fallback；保留最小受控文件写与明确失败。
4. **P1：T049、T050**，Conversation Summary 与 token-pressure trigger；保留完整回合窗口、任务/关键参数和连续 run 验证。

上述六个 P1 和一个 P2 可整体延后；没有 P0 硬依赖它们。不得通过删验证、关安全门禁、跳过真实集成或伪造 Benchmark 来压缩 P0；若 P0 未通过，MVP 尚未完成。

## MVP Exit Criteria

以下全部满足并在 T073 附证据后，MVP 才可以停止开发：

- [ ] **Real Repository Investigation works**：真实 Repository、Issue、Issue→Code 调查有来源和有效结果；T007–T012 历史验收在最终版本回归（T068/T073）。
- [ ] **Parameter validation works**：required/type/enum/invalid JSON 不绕过 dispatch；T013–T015、T020、T021。
- [ ] **Missing parameter clarification works**：缺关键参数询问、明确回复合并、重新校验后一次执行；T016–T019、T021。
- [ ] **Semantic Tool Routing works**：真实 semantic retrieval、Top-K 和控制工具保留；K 来自实验；T022–T030。
- [ ] **Real Tool/MCP execution works**：真实 GitHub Local Tool 和至少一种真实 MCP transport/server 的 discovery/call/cleanup 有证据；T020、T035、T038、T068。
- [ ] **Basic reliability works**：isError 保留、明确结果状态、有界 timeout、分类重试与幂等读策略；T031–T035、T037–T038。
- [ ] **Controlled action safety works**：至少一个真实受控动作通过 Validation → Risk Check → Human Confirmation → Execute；拒绝、越界、symlink、参数变更和重复提交不绕过；T039–T043、T045–T046。
- [ ] **Memory essentials work**：完整回合、目标/关键参数保留，多轮与连续 run 生命周期通过；T047–T048、T051–T052。
- [ ] **Tests pass for TaskPilot scope**：所有已交付 TaskPilot unit/integration/regression 检查通过，真实 E2E 另附记录；未运行项目明确 NOT VERIFIED；T073。
- [ ] **Benchmark completed**：冻结数据集、原始 OpenManus 与同工具池 full-tool 两组参照、TaskPilot、真实指标、失败分析和复现方式齐备；T065–T072。
- [ ] **README / architecture completed**：与最终功能和安全边界一致，命令和引用经过核查；T060、T073。
- [ ] **Docker startup works**：干净镜像可启动并跑通真实任务，不依赖宿主环境；T059。
- [ ] **Online demo or reproducible deployment exists**：至少 T062 的可复现部署通过，公开服务额外满足 T061；T063 在线托管可选。
- [ ] **Resume metrics come from real benchmark**：每个简历数字能回溯数据、版本、运行与计算口径，没有预写提升；T073。
- [ ] **All P0 are DONE**：Dashboard 重新计算 Remaining P0 = 0；无 P0 BLOCKED/IN_PROGRESS/TODO；所有 DONE 有相应验收证据。

**一旦 P0 全部完成且上述出口验收通过，停止继续增加 Feature。** P1/P2 未完成不阻止 MVP 停止；它们保留为延期记录。若最终 Benchmark 没有显示提升，如实报告绝对结果、权衡与失败，不因追求漂亮指标扩大 Scope。

当前下一步仅是后续独立授权的 **T013 / PR-03: Parameter Validation and Clarification**；本次文档任务到总账合并即结束。
