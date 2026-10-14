# 使用 TaskPilot

首次安装先按 [README](../README.md) 完成环境和配置。本指南补充日常 CLI/API 操作；容器步骤见[部署指南](T059_DOCKER.md)，源码修改见[开发指南](DEVELOPMENT.md)。当前是只读 GitHub 调查服务，没有聊天网页、用户认证或持久任务数据库。

## 本地配置

所有命令从项目根目录执行。Python 使用 3.12，依赖使用 `requirements-service.lock.txt`，不要用上游 `requirements.txt` 替代服务锁文件。

```bash
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python --torch-backend cpu -r requirements-service.lock.txt
if [ ! -f config/config.toml ]; then
  cp config/config.example-baseline.toml config/config.toml
fi
chmod 600 config/config.toml
```

已有可用 `.venv` 时跳过创建。用编辑器填写 `config/config.toml` 的 `[llm]`：`model`、`base_url`、`api_key` 必须对应同一模型提供方，保留示例 `[sandbox]` 和 `[daytona]`。完整示例见 README。配置直接解析 TOML，不会加载 `.env` 或展开 `${ENV_VAR}`。模型需支持工具调用；当前历史验收不能代表所有模型和 thinking 模式均兼容。

公开仓库常规读取可以不带 GitHub 凭据，仍受限流约束；Code Search 需要有相应权限的 Token。在启动 CLI/API 的同一 Bash 终端设置：

```bash
read -rsp '请输入 GitHub Token（输入不回显）: ' GITHUB_TOKEN
echo
export GITHUB_TOKEN
```

如果系统代理使 GitHub 请求失败，而该主机可以直连，可只为 GitHub 绕过代理并保留原条目：

```bash
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}api.github.com"
export no_proxy="$NO_PROXY"
```

这不是所有网络的通用设置；应根据实际网络选择。不要输出配置或整个环境排错。

## 命令行调查

```bash
OPENMANUS_DISABLE_BROWSER_USE=1 .venv/bin/python taskpilot.py \
  --repository FoundationAgents/OpenManus \
  --prompt "读取 README.md，结合仓库信息说明这个项目的用途，并给出来源。"
```

`--repository` 是 `owner/repo`；`--ref` 可指定分支、标签或提交 SHA，省略时使用默认分支。CLI 默认提供全部工具，不加载语义路由模型；适合首次确认模型 API 与 GitHub 调查链路。没有指定仓库或缺少工具参数时，按终端提示回答。任务类型不绑定固定工具顺序。

结果会打印到终端。历史真实任务见[仓库调查记录](REPOSITORY_INVESTIGATION.md)和[参数闭环证据](PR03_PARAMETER_E2E.md)。`Ctrl+C` 可中断本地程序，但不证明远端请求已撤销。

## 启动 API 服务

在终端 A 执行并保持运行：

```bash
OPENMANUS_DISABLE_BROWSER_USE=1 .venv/bin/python -m uvicorn \
  app.taskpilot.service:app --host 127.0.0.1 --port 8000
```

服务默认每轮提供最多 3 个业务工具及必要控制工具。首次任务加载固定的 Embedding 模型；本地 Uvicorn 启动时不预热，因此健康接口成功不代表模型下载完成。需要网络访问模型提供方、GitHub 和首次下载用的 Hugging Face。

终端 B 从项目根目录调用，统一设置地址；改端口后只需同步这里：

```bash
TASKPILOT_URL=http://127.0.0.1:8000
curl --fail --silent --show-error "$TASKPILOT_URL/health"
TASK_RESPONSE=$(curl --fail --silent --show-error "$TASKPILOT_URL/tasks" \
  -H 'Content-Type: application/json' \
  -d '{"repository":"FoundationAgents/OpenManus","task":"读取 README.md 并给出仓库用途与来源。"}')
TASK_ID=$(printf '%s' "$TASK_RESPONSE" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["task_id"])')
printf '任务 ID：%s\n' "$TASK_ID"
curl --fail --silent --show-error "$TASKPILOT_URL/tasks/$TASK_ID"
```

提交成功返回 HTTP 202 与 `task_id`，此时任务还没有完成。重复最后一条命令查询，也可在浏览器打开 `http://127.0.0.1:8000/docs` 使用接口调试页面。仅使用 Docker、宿主没有 `.venv` 时，可在该页面提交和查询，或手工把响应中的 ID 填入查询 URL。

### 结果怎么看

- `status` 表示 `queued`、`running`、`clarification_pending`、`finished`、`failed` 或 `cancelled`。
- `result` 是 Agent 返回内容；`execution_status`、`finish_source`、`failure_observation_count` 补充执行及失败信息。
- `business_outcome=not_verified` 表示服务未独立验证回答。Agent 的 `terminate(success)` 不能代替真实来源核对。
- `cleanup_status` 表示本地清理状态，`run_id` 用于关联脱敏 Trace。

### 缺参回复与取消

当 `status=clarification_pending`，读取 `clarification` 的 `tool_call_id`、`missing_fields` 等内容。只补充当前询问的字段，例如缺少 `path` 时回复：

```bash
read -rp '请输入响应中的 tool_call_id: ' TOOL_CALL_ID
curl --fail --silent --show-error \
  "$TASKPILOT_URL/tasks/$TASK_ID/clarifications/$TOOL_CALL_ID/reply" \
  -H 'Content-Type: application/json' \
  -d '{"reply":"{\"path\":\"README.md\"}"}'
curl --fail --silent --show-error "$TASKPILOT_URL/tasks/$TASK_ID"
```

回复属于这个任务和原始调用，合并后仍需重新校验。默认等待上限 120 秒；缺参等待不会阻塞其他 HTTP 请求。取消任务：

```bash
curl --fail --silent --show-error -X DELETE "$TASKPILOT_URL/tasks/$TASK_ID"
```

非法请求返回 422，未知任务返回 404，重复、迟到或不匹配的回复返回 409。取消针对本地任务，远端超时后的结果可能未知。状态保存在进程内存中；不要启用多个 Uvicorn worker 分散同一任务的请求，重启后旧任务 ID 会返回 404。

## 停止与部署

本地 Uvicorn 在终端 A 按 `Ctrl+C` 停止。端口冲突时修改 `--port`，同时更新请求地址。Docker 使用不同的启动入口，并会在开放 HTTP 前预热模型；请按[中文部署指南](T059_DOCKER.md)执行。
