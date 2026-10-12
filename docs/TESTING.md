# 测试与验证

本仓库保留 TaskPilot 的确定性/隔离集成测试及真实只读集成证据。以下命令在已完成[本地安装](USAGE.md#本地配置)后执行；锁文件只包含运行依赖，测试工具另行安装。

```bash
uv pip install --python .venv/bin/python --torch-backend cpu pytest==8.3.5 pytest-asyncio==0.25.3
.venv/bin/python -m pytest -q tests/taskpilot tests/baseline/test_runtime.py tests/baseline/test_t066_original_baseline.py tests/tools/test_browser_use_mcp.py
```

`core-mvp-v1` 的已交付范围结果为 **390 passed / 12 warnings**：372 个 TaskPilot、13 个运行基线/Browser MCP、5 个原始 OpenManus 基线测试。12 条 warning 为七条 Pydantic class-config 弃用提示、四条移除配置键提示和一条 Requests 依赖版本提示；pytest-asyncio 还在开始测试前提示 fixture loop scope 配置。上游完整 sandbox 测试存在与已验证环境不符的断言，不把上述范围结果称为上游全套通过。

这组测试包含 deterministic fixtures 和隔离的真实 stdio MCP Server，不能代替付费 Provider 或真实 GitHub API。已记录的真实证据包括[三类仓库调查](REPOSITORY_INVESTIGATION.md)、[参数澄清](PR03_PARAMETER_E2E.md)、[MCP 生命周期](evidence/t035_real_mcp_e2e.json)、[真实故障](evidence/t038_real_failure_e2e.json)、[Trace](evidence/t055_provider_github_trace.json)、[服务澄清](evidence/t057_service_clarification.json)及[Docker HTTP](T062_DEPLOYMENT.md)。它们的运行时间、受控注入、失败和未验证项见各记录。

冻结的评测数据、原始运行与评分脚本见[正式 Benchmark](BENCHMARK.md)；离线复算不会重新调用模型。真实重跑需网络及安全注入的模型配置，可能产生费用，且应使用新的输出路径保留原始证据。
