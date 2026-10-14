# PR-05 Core P0 可靠性回归矩阵（T037）

> 本文是历史 T037 测试记录。后续真实故障 E2E 见 [T038 证据](evidence/t038_real_failure_e2e.json)，当前能力边界见[架构指南](TASKPILOT_ARCHITECTURE.md)。

本矩阵覆盖 `codex/pr-05-t031` 分支上的确定性与隔离集成行为；当时真实外部故障 E2E 留待 T038。

- **结果状态与 Observation：**`tests/taskpilot/test_tool_result_status.py` 检查 Local `ToolResult`、字符串、字典和异常结果；MCP `isError=true/false/absent`、多个内容块、截断、状态头及原始调用 ID。控制工具失败不会结束 Agent。
- **超时、取消、清理与预算：**`tests/taskpilot/test_tool_timeout_policy.py` 检查 Local/MCP 超时记为 `unknown`、取消、部分 MCP 连接清理、有界断开与 Agent 清理，以及运行预算耗尽后不再执行新调用。`tests/taskpilot/test_real_mcp_lifecycle.py` 检查真实 stdio 测试服务的连接超时、慢调用及服务崩溃清理。
- **重试分类与截止时间：**`tests/taskpilot/test_tool_retry_policy.py` 覆盖临时 429/5xx 与连接恢复、最多三次尝试、永久 401/403/404 和 Schema 失败不重试、结果未知的超时、非幂等/MCP 工具不自动重放、Retry-After、取消及 Agent/Tool 截止时间。
- **MCP 故障与生命周期：**`tests/taskpilot/test_real_mcp_lifecycle.py` 检查真实 FastMCP stdio 连接、发现、调用、断开与重连，`isError` 失败 Observation，过期调用以 `failure` 拒绝且零执行，有界清理，无重复/过期注册，以及重连后的 Schema/索引变化。
- **GitHub 只读失败与恢复：**`tests/taskpilot/test_github_tools.py` 覆盖 5 个 Local 工具的 API 失败。Agent 回归覆盖 404 不重放、修正路径并经历一次 503 重试后恢复、Observation 与调用 ID 保留；重复 403 在 `max_steps` 处退出而不宣称成功。
- **跨功能执行边界：**`tests/taskpilot/test_control_tool_preservation.py` 检查 K=1 业务选择仍保留 `terminate`、缺参在执行前强制澄清、重校验、临时读取重试及原调用 ID。`tests/taskpilot/test_clarification_resume.py` 覆盖非法回复、重复/迟到回复、取消及并行调用；`tests/taskpilot/test_tool_schema_validation.py` 覆盖非法 Schema 参数零执行。

Agent 测试中的脚本化 LLM 响应用于验证调用衔接和有界行为，不验证模型判断。T034 非幂等写入、T036 fallback 为 P1，不在本矩阵内；T038 之前，非受控真实 GitHub/MCP 故障仍为 **NOT VERIFIED**，确定性测试替身不能冒充该 E2E。

2026-10-10 验证记录：

- `pytest -q tests/taskpilot/test_tool_result_status.py tests/taskpilot/test_tool_timeout_policy.py tests/taskpilot/test_tool_retry_policy.py tests/taskpilot/test_real_mcp_lifecycle.py tests/taskpilot/test_github_tools.py tests/taskpilot/test_control_tool_preservation.py tests/taskpilot/test_clarification_resume.py`：99 passed。
- `pytest -q tests/taskpilot tests/baseline/test_runtime.py tests/tools/test_browser_use_mcp.py`：272 passed。
- `pytest -q --maxfail=1`：9 passed、1 failed。失败位于无关的 `tests/sandbox/test_client.py::test_sandbox_creation`：预期 Python 3.10，实际 sandbox 为 3.12.14。全套剩余部分为 **NOT VERIFIED**。
