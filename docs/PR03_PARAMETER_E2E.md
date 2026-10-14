# PR-03 / T021 参数执行验收证据

运行日期：2026-10-04。产品检出位置为 `codex/pr-03-t013`，T021 提交前版本为 `f4d4a6bf3314c2c0675dadac1f68caf2474d7ccc`。模型为配置的 DeepSeek OpenAI-compatible 接口提供的 `deepseek-flash`，与 PR-02 真实基线一样关闭 thinking。提供方未公开独立模型 revision。

仓库上下文明确确认为 `FoundationAgents/OpenManus`，`ref=None`（GitHub 默认分支，报告为 `main`），`source="user_input"`。进程继承 `GITHUB_TOKEN`，未记录其值或请求头。

真实检查使用生产 `RepositoryInvestigationAgent`、5 个本地 GitHub 工具、`ToolCollection` 校验器、`LLM` 和 `GitHubClient`，访问 `api.github.com`。响应钩子只统计请求并记录 URL 路径及状态；执行探针记录校验结果和调用 ID。人工回复由脚本在已有 CLI `AskHuman` 边界注入。负向用例只在执行边界前修改**真实模型已选中调用的参数**；这是故障注入，不代表模型自然生成了错误参数。

## 参数用例

- **CLI 入口缺仓库：**省略 `--repository`，`AskHuman` 询问一次 `owner/repo`。脚本回复 `FoundationAgents/OpenManus`，通过带 `source="user_input"` 的 `RepositoryContext.parse` 后，真实模型才调用 `github_repository_info`、`github_read_file(path=README.md)` 和 `terminate`。仓库信息与 README 两次请求均为 HTTP 200，最终回答概述仓库。取消、空回复及非法回复在确定性测试中均为零 Agent 创建、零工具执行。
- **缺 path → 澄清 → 合并 → 重校验：**模型选中 `github_read_file`，注入将参数替换为 `{}`，保留 `tool_call_id=call_00_H181JM7mu6WH0rmHaQv54817`。首次校验返回 `MissingParameterFailure`，零 GitHub 请求；`AskHuman` 只问一次 `path`，没有重复询问已确认的仓库。脚本回复 `README.md` 合并到原 pending 调用。同一个 `ToolCollection.execute` 校验器收到 `{"path":"README.md"}` 并返回 `ToolResult`；对 `/repos/FoundationAgents/OpenManus/contents/README.md` 的一次真实 GET 返回 200。Observation 保留原调用 ID，模型最后调用 `terminate(success)`。共两次校验、一次真实 GitHub 执行。
- **合法直接调用：**模型选中 `github_read_file({"path":"README.md"})`，`tool_call_id=call_00_C6kS0AbMqPIomIAhSRPY3249`。无澄清，校验返回 `ToolResult`，恰好一次 README GET 为 200，Observation 保留调用 ID，模型调用 `terminate(success)`。
- **畸形 JSON：**模型选中 `github_read_file`，注入改为 `{"path":`，保留 `tool_call_id=call_00_eAixk2W2QPBsq2mOJB0B1935`。解析产生同 ID 的错误 Observation；`ToolCollection.execute` 为零调用，GitHub HTTP 为零请求。该运行有意限制为一步。
- **基本类型错误：**模型选中 `github_read_file`，注入 `{"path":7}`，`tool_call_id=call_00_gubPPgUSk5FM8kH6kVFX3531`。校验器返回 `ToolValidationFailure`，零 GitHub 请求，Observation 保留原 ID；运行有意限制为一步。
- **类型错误后修正：**注入 `{"path":7}` 的调用 `call_00_NajAxpeXfo42caWoKSoQ1734` 校验失败，零 HTTP。模型随后新建合法调用 `github_read_file({"path":"README.md"})`，ID 为 `call_00_eFGcTu3cRs85JH6XlUHW2350`；一次 README GET 返回 200。最终回答承认类型错误并说明读取成功。这是模型通过新调用修正，与原 pending 调用的澄清恢复不同。

另一次通过自然语言诱导缺失 `path` 的尝试，使模型选择 `terminate(failure)`，没有 GitHub 请求。因此模型自然遗漏必需参数仍为 **NOT VERIFIED**。故障注入证明的是这类调用进入边界后，生产执行、澄清、合并、重校验与真实 API 路径有效。

一次强制要求选择工具的探索尝试生成 `github_read_file({"path":""})`，随后 `terminate(failure)`。已有文件路径业务校验拒绝空字符串，零 HTTP、无澄清。这是非法值，不是 JSON 字段缺失；该尝试没有使用产品默认的 `AUTO` 工具选择。

## PR-02 调查回归复跑

以下为上述检出版本的新真实运行，与[历史 PR-02 证据](REPOSITORY_INVESTIGATION.md)分开。使用真实模型及相同的明确仓库上下文；所有列出的 HTTP 响应均为 200，各运行均产生非空最终回答。

- **A — 仓库理解：**`github_repository_info` → `github_read_file` → `terminate`。HTTP 依次读取 `/repos/FoundationAgents/OpenManus` 与 `/repos/FoundationAgents/OpenManus/contents/README.md`，共两次。最终回答以原文“OpenManus — Repository Summary”开头。
- **B — Issue 调查：**`github_issue_detail` → 两次 `github_code_search` → 两次 `github_read_file` → `terminate`。读取 Issue `#1427`、评论、两次 `/search/code`、`app/tool/mcp.py` 和 `app/tool/base.py`，共六次请求；回答概述该 Issue 和 MCP `isError` 问题。
- **C — Issue → 源码：**`github_issue_detail` → `github_code_search` → 两次 `github_read_file` → `terminate`。读取 Issue `#1427`、评论、`/search/code`、`app/tool/mcp.py` 和 `app/tool/base.py`，共五次请求；回答关联该 Issue 与 `MCPClientTool` 实现。

## 确定性检查与环境检查

T013–T020 覆盖非法 JSON/Schema 零执行、必需字段澄清、pending 回复合并、重校验、恰好一次恢复、仓库上下文来源与复用、Local/MCP 风格 Schema 兼容。T021 增加 CLI 仓库澄清，以及取消、空或非法回复时不执行的测试。冻结 Core MVP 范围见[测试指南](TESTING.md)。

当时使用 `.venv/bin/python -m pytest -q --disable-warnings` 检查：

- 8 个 T013–T020 参数模块及 `tests/taskpilot/test_parameter_entry.py`。
- `tests/taskpilot`。
- `tests/taskpilot/test_github_tools.py tests/baseline/test_runtime.py tests/tools/test_browser_use_mcp.py`。
- 全仓库 `--maxfail=1`，使用相同的 `-q --disable-warnings` 选项。

T013–T021 定向矩阵 **107/107** 通过，TaskPilot **120/120** 通过，PR-02 本地回归 **26/26** 通过。全仓库测试在 **9 passed、1 failed** 后停止：`tests/sandbox/test_client.py::test_sandbox_creation` 预期 Python 3.10，实际 sandbox 为 3.12.14；未修改 sandbox 代码。

**当时未验证（NOT VERIFIED）：**真实 MCP 连接、发现、Server 调用、断开、重连及参数 E2E 留待 T035/T038；默认 thinking 模式、模型自然产生非法/缺失工具参数尚未证明；无关 sandbox 断言阻塞后，仓库全套测试尚未完整验证。
