# 只读 Repository Investigation

TaskPilot PR-02 增加单 Agent 的 GitHub 只读调查入口。它复用 OpenManus 的 `ToolCallAgent`、`BaseTool`、`ToolCollection` 和现有 step/observation 循环。模型根据用户请求和前一步 Observation 动态选择工具；应用不规定固定的 Issue → Code 工作流。

## GitHub 接入

采用范围窄的 GitHub REST API v3 Local Adapter。选择它而不接 MCP Server，是因为这五类只读操作不需要远程进程管理或工具发现，并且可直接使用现有 `BaseTool` 接口。Adapter 只发 HTTP `GET` 请求。

当前支持：

- Repository metadata / 基本信息
- Repository 范围内的 Issue 搜索
- Issue 详情和最多十条评论
- Repository Code Search
- 指定默认分支或可选 ref 读取文件

通过 `owner/repo` 指定目标，不在产品代码中写死仓库。专用 Agent 只暴露上述五个工具和 `terminate`，不继承 Manus 的 Python、Shell、Browser 或文件编辑工具。

## 配置与运行

模型 Provider 使用 OpenManus 的 `config/config.toml`，详见 [BASELINE_RUNTIME.md](BASELINE_RUNTIME.md)。读取公开仓库的 metadata、Issue 和文件不需要 GitHub 凭据。GitHub Code Search 需要 Token；创建仅有目标仓库只读权限的 Token，并在本机环境变量中设置：

```bash
export GITHUB_TOKEN="YOUR_READ_ONLY_TOKEN"
PYTHONPATH=. .venv/bin/python taskpilot.py \
  --repository FoundationAgents/OpenManus \
  --prompt "查找最近与 MCP 相关的 Issue，并检查可能相关的代码。"
```

可选 `--ref <branch-or-tag>` 用于选择文件读取的 ref。程序从环境变量读取 Token，不会将其写入日志。固定版 OpenManus 配置不加载 `.env` 文件。

## 真实验证

以下端到端验证由真实模型和 GitHub 数据完成。自动化测试使用确定性 HTTP fixture，不依赖 GitHub 网络。

| Case | 结果 | 实际工具证据 |
| --- | --- | --- |
| A — Repository 理解 | VERIFIED | 真实模型选择 `github_repository_info`（无参数）：HTTP 200，840.5 ms，Observation 成功；随后根据 Observation 选择 `github_read_file(path=README.md)`：HTTP 200，542.9 ms，Observation 成功。模型读取 README 后回答并成功终止。 |
| B — Issue 调查 | VERIFIED | 真实模型两次选择 `github_issue_search`（`query="MCP state:open type:issue"`：200 / 894.4 ms；`query="MCP in:title,body is:issue is:open"`：200 / 451.5 ms），随后选择 `github_issue_detail(issue_number=1427)`（200 / 478.0 ms）及评论读取（200 / 463.9 ms）。所有成功调用均返回 Observation。结果只有一个符合条件的 open Issue：#1427，Agent 正常终止。 |
| C — Issue → 代码调查 | VERIFIED | 真实模型读取 #1427 详情（HTTP 200 / 1,116.7 ms）及评论（200 / 419.4 ms），随后根据 Observation 查询仓库信息（200 / 549.1 ms）、动态执行多次 `github_code_search`（包括 `MCPClientTool execute isError`、`CallToolResult isError`、`MCPClientTool` 等，均 HTTP 200，Observation 成功），并读取 `app/tool/mcp.py`、`app/tool/base.py`、`tests/tools/test_browser_use_mcp.py` 和 `app/agent/toolcall.py`（均 HTTP 200，Observation 成功）。Agent 后续继续搜索并读取 Issue 相关实现，最后调用 `terminate`。 |

上述证据摘自本地 Loguru 运行日志。Adapter 为 GitHub 调用记录工具名、相关参数、HTTP 状态、耗时和 Observation 成功状态，不记录访问 Token。公开仓库的 Code Search 需要认证；本机通过 `gh auth login` 完成认证，并由当前 shell 将现有 GitHub CLI 凭据提供给 `GITHUB_TOKEN` 环境变量，未将 Token 写入仓库。

### 上下文规模观察

一次真实 Issue Search 曾返回过大的原始结果，使模型输入超过 100k tokens。工具输出归一化为调查相关字段后，后续相关模型输入约为 1.9k tokens。这是一次真实运行观察，用于说明保留相关字段的必要性；它不是正式 Benchmark，也不代表整体性能提升百分比。

## 已知限制

- GitHub Code Search 要求 `GITHUB_TOKEN`；未认证时返回 HTTP 401。认证后的 Code Search 已在 Case C 验证成功。详见 [GitHub REST Code Search 文档](https://docs.github.com/en/rest/search/search#search-code)。
- Issue Detail 最多返回十条评论；每次 File Read 最多返回 12,000 字符。
- 受 GitHub API Rate Limit 限制；本 PR 未加入 Retry 或 Fallback。
- 当前入口是 CLI；不包括 FastAPI 或 Web UI。
- 真实工具选择效果依赖用户配置的模型及其 Function Calling 能力。
- 当前保留 OpenManus 全量可用工具选择机制；尚未实现 Semantic Retrieval / Top-K。
- 缺失参数仍依赖现有 Agent / Tool 行为；尚无专门的参数澄清流程。
- Retry / Fallback 和更完整的可靠性 Guardrail 尚未实现。
