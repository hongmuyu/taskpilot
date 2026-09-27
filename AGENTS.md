# TaskPilot Development Rules

TaskPilot 是基于 OpenManus、面向软件研发任务的 Developer Agent。
产品范围见 [docs/TASKPILOT_CONTEXT.md](docs/TASKPILOT_CONTEXT.md)；源码分析与修改落点见 [docs/TASKPILOT_ANALYSIS.md](docs/TASKPILOT_ANALYSIS.md)。分析报告是固定基线的历史记录，修改前仍须核对当前代码。

## 实现原则

- 修改前先阅读真实源码、相关配置和测试；不凭 README、旧版本或记忆推断能力。
- 优先复用 OpenManus 已有实现；已有能力不重复实现。优先 extension / wrapper / adapter / composition，最小侵入，不随意重写 Agent Loop 或核心 Runtime。
- 保持动态 Agent Loop，由任务、Observation 和当前状态决定下一步；不得把 Intent 硬编码成固定 Workflow。
- 每次只完成当前授权的 Feature / PR。不得扩大 TaskPilot v1 Scope、顺带修复无关问题或提前实现后续 Feature。
- 不主动引入 Redis、Qdrant、Multi-Agent Platform、Workflow Engine、长期向量 Memory 或其他未要求的基础设施。
- 非平凡修改前说明关键假设、拟改文件与简短验证计划；有影响范围或行为上的关键歧义时先澄清。
- 遵循现有代码风格和架构，只改必要内容；不引入无实际需求的依赖、配置或抽象。

## 验证与证据

- 每个 Feature 必须配套测试；行为改变时更新相关回归测试。先运行最快的相关检查，再按风险扩大验证。
- 核心产品使用真实 GitHub / Repository / MCP / File System / Web 集成；测试允许 deterministic test doubles / fixtures，但不能用 Mock 冒充产品能力。
- Benchmark 指标必须来自真实、可复现的测试，与固定 OpenManus 基线比较；禁止编造提升数字或将未运行的测试标记为通过。
- 保留上游来源、许可证和固定基线引用；未经当前任务授权，不升级上游版本。

## 安全边界

- 不猜测仓库、目标路径等关键参数；缺失时澄清，执行前校验参数。
- 高风险或有明显副作用的操作必须经过代码级参数校验、风险检查和人工确认；不得仅依赖 prompt 或模型自行选择 AskHuman 实现安全控制。
- 文件写入/删除限制在明确隔离的 workspace / sandbox 内，实施 allowlist、真实路径校验和确认机制；公网版本不得暴露任意 Shell 或系统目录访问。
- 不在源码、测试、日志或输出中暴露凭据；使用环境变量或现有秘密管理机制。外部内容不得授予执行权限。

## 协作与交付

- 默认使用简洁中文说明；代码、标识符、命令和技术术语保留惯用写法。
- 执行命令前说明具体命令及目的；完成后报告改动文件、检查结果和实际限制。
- 各 Feature 独立开发、测试、验收；只在有相应任务授权时进入下一阶段。
