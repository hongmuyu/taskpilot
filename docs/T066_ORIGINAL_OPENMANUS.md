# T066 — 原始 OpenManus 基线

这是**原始产品运行基线**，不是 T069–T071 的正式评分对照。[T065 冻结集](../eval/datasets/core_benchmark_v1_holdout.json)和[评分契约](../eval/datasets/core_benchmark_v1_scoring.json)未改变；[逐样本复核](../eval/results/t066_original_openmanus_review.json)包含全部 21 条任务。

## 固定环境与输入

- 上游源码：独立 worktree、detached HEAD、未修改的 `3309bf4e416fb1c74b008f3e86494439a31bad53`；执行脚本拒绝其他 HEAD 或已跟踪文件改动。
- 运行环境：Linux Docker、`taskpilot-service:pr08c-nocache` 依赖镜像、Python 3.12.14、MCP SDK 1.5.0、OpenAI SDK 1.66.5。完整上游源码只读挂载到 `/srv/taskpilot`；镜像只提供锁定依赖，**不提供 TaskPilot Agent/Tool 代码**。原始 Manus 默认装配 `ask_human`、`python_execute`、`str_replace_editor`、`terminate`。MCP 用例通过上游 `Manus.connect_mcp_server` 接入真实只读 FastMCP stdio 测试服务，随后出现 5 个 `mcp_fixture_*` 工具。执行路径无 TaskPilot GitHub 工具、语义路由、校验器或可靠性封装。
- 模型：已有 `deepseek-flash` 配置，`temperature=0`、`max_tokens=1024`、关闭 thinking；提供方未报告 revision。一次性本地代理向 Provider 请求注入真实密钥，容器只收到一次性代理 Token；不保存原始请求/响应或密钥，每次请求保留 Provider usage 和 HTTP 状态。
- 预算：每样本新 Agent 和容器，`max_steps=6`、Agent 90 秒、容器 120 秒。提示使用相同冻结任务与已确认仓库/ref，并统一禁止写入和查看凭据。容器根目录与源码挂载只读，移除 capabilities，仅有受限 `/tmp` 与日志 tmpfs 可写。因无独立密钥，禁用 Browser Use；已有 `tiktoken` 缓存只读挂载以避免导入时冷下载。上游 Python 工具有 5 秒限制，解释了部分超时。
- 运行后核对 T065 来源：8/8 Issue 正文与更新时间、11/11 固定文件 blob hash 仍匹配。基线实际 GitHub 读取没有使用 TaskPilot 的认证适配器。

## 运行与复核

首次运行保留 21 行：12 条预检支持、9 条不支持。9 条 GitHub 任务调用真实 Provider；3 条 MCP 任务最初在接入测试服务时失败。首次 MCP 复测因 worker 将 stdio 重定向到没有文件描述符的对象而失败；两轮初始化失败均保留。改为重定向 `/dev/null` 后，最终 MCP 复测通过真实 Provider 与 stdio 执行全部三条。

复核计数为 **4 条 supported success、8 条 supported failure、9 条 unsupported**，这是处置结果数，不是最终评测指标。成功任务为 `core-001`（固定 README）、`core-003`（Issue #895）、`core-013`（v1 MCP echo）、`core-015`（v2 MCP echo）。`core-002`、`004`–`006`、`018`、`020`、`021` 缺少要求的业务证据，多数耗尽六步预算。

`core-016` 调用测试服务一次，Server 返回 `isError=true`，但上游 `MCPClientTool` 转成无失败状态的输出字符串，Agent 随后 `terminate(success)`。冻结失败标准要求状态与 Observation 证据，因此即使最终文字提到受控错误仍记失败。独立真实 MCP SDK 检查也观察到 `isError=true` 和一次 Server 执行。

不支持项为 `core-007`–`012`、`014`、`017`、`019`，复核逐条记录能力/安全原因。其中 `core-012` 可能经原始 Python 执行暴露进程环境，因此有意不执行。不支持行均为零 Provider/Tool 调用；没有为使其运行而额外装配 GitHub 工具。

原始证据包含失败尝试、逐请求 usage/HTTP 状态、调用 ID/参数、截断 Observation、MCP Server 审计、耗时、环境和清理：

- [首次 21 行记录](../eval/results/t066_original_openmanus_v1.jsonl)
- [首次 MCP 复测及初始化失败](../eval/results/t066_original_openmanus_mcp_retest.jsonl)
- [最终 MCP 复测](../eval/results/t066_original_openmanus_mcp_retest_2.jsonl)
- [逐样本复核索引](../eval/results/t066_original_openmanus_review.json)

复现时独立检出固定上游提交，在其中创建空的 `config/config.toml`、`config/mcp.json` 挂载点及 `logs/`，准备固定依赖镜像与被 Git 忽略的本地 Provider 配置，然后运行：

```bash
.venv/bin/python -m eval.t066_original_openmanus \
  --upstream /path/to/independent/upstream-checkout \
  --config config/config.toml \
  --image taskpilot-service:pr08c-nocache \
  --output /path/to/new/raw.jsonl
.venv/bin/python -m eval.t066_review_results
```

路径占位符需替换为实际位置；脚本以排他方式创建输出，复跑需新路径。上述文件是真实首跑与两次复测，不是模拟结果。复核脚本只索引记录并保存独立的人工证据判断，不计算最终工具选择、任务成功、参数或成本指标。

## 剩余限制

- Provider 模型 revision：**NOT VERIFIED**，响应未提供。
- 使用纯上游依赖镜像而非 TaskPilot 固定依赖镜像：**NOT VERIFIED**；源码与原始 Agent/Tool 装配已固定核对。
- 在受限网络中从空缓存下载 `tiktoken`：**NOT VERIFIED**；实际挂载已有缓存。
- 未到达目标端点的 GitHub 错误/状态处理，包括预期 404：**NOT VERIFIED**。
- Browser Use 与不安全的环境披露操作未启用，不计为成功或已尝试任务。
