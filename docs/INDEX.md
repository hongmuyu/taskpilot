# TaskPilot 公开文档

从仓库根目录的 [README](../README.md) 开始安装和体验。下列材料对应已交付的只读 Core MVP；历史运行记录均保留其原时间点的能力边界。
本公开索引与新增使用/测试/Benchmark 文档发布于 `core-mvp-v1` 正式冻结之后，应从后续 `main` 阅读；Tag 仍固定原始产品代码和证据。

## 使用与架构

- [CLI、FastAPI 与 Docker 使用指南](USAGE.md)
- [Agent Loop、语义路由、校验、MCP、可靠性与 Trace 架构](TASKPILOT_ARCHITECTURE.md)
- [Docker 构建、冷启动与缓存](T059_DOCKER.md)
- [干净环境部署与三类真实 HTTP 验收](T062_DEPLOYMENT.md)
- [测试范围与真实集成证据](TESTING.md)

## 调查与工具证据

- [三类真实仓库调查](REPOSITORY_INVESTIGATION.md)
- [参数校验、澄清与真实 GitHub 执行](PR03_PARAMETER_E2E.md)
- [可靠性回归](PR05_RELIABILITY_REGRESSION.md)、[真实 MCP 生命周期](evidence/t035_real_mcp_e2e.json)与[真实失败 E2E](evidence/t038_real_failure_e2e.json)
- [真实 Provider/GitHub Trace](evidence/t055_provider_github_trace.json)与[服务缺参澄清](evidence/t057_service_clarification.json)

## Benchmark 与复现

- [正式 21 条 Holdout 报告、分母与限制](BENCHMARK.md)
- [冻结数据和评分契约](T065_BENCHMARK_DATASET.md)
- 三组执行记录：[原始 OpenManus](T066_ORIGINAL_OPENMANUS.md)、[TaskPilot 全量工具](T067_FULL_TOOL_BASELINE.md)、[TaskPilot K=3](T068_K3_COMPARISON_RUN.md)
- 指标：[工具选择与预期结果](T069_ACCURACY_SUCCESS.md)、[Token 与延迟](T070_TOKEN_LATENCY.md)、[参数 Slot](T071_PARAMETER_SLOT_METRICS.md)
- [失败分析与干净环境复现](T072_FAILURE_REPRODUCIBILITY.md)

`eval/datasets/` 存放冻结的 Holdout、标注及评分规则；`eval/results/` 保存脱敏原始运行、人工复核与逐样本结果。早期 [K 选择实验](T029_TOPK_EXPERIMENT.md)与[单步路由对照](T030_ROUTING_COMPARISON.md)是阶段证据，与正式 21 条 Holdout 分开。

## 保留的上游组件资料

仓库仍含 OpenManus 的 [Daytona sandbox](../app/daytona/GUIDE.md)、[图表可视化](../app/tool/chart_visualization/GUIDE.md)、[A2A 实验](../protocol/a2a/app/GUIDE.md)与[示例任务](../examples/use_case/EXAMPLE.md)资料。这些指南记录上游组件，不是 TaskPilot 只读 GitHub 入口的安装步骤或已验收能力。
