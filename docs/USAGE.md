# 使用 TaskPilot

TaskPilot 的专用入口只启用五个 GitHub 只读工具和终止控制工具。CLI 与服务都需要已明确的 `owner/repo`；默认使用仓库的默认分支读取文件，可指定 ref。服务当前仅适用于本机或受限访问，不提供用户认证或持久任务数据库。

## 本地配置

在仓库根目录使用 Python 3.12 和 `uv`：

```bash
uv venv --python 3.12
uv pip install --python .venv/bin/python --torch-backend cpu -r requirements-service.lock.txt
cp config/config.example-baseline.toml config/config.toml
chmod 600 config/config.toml
```

在 Git 忽略的 `config/config.toml` 中填写 `[llm]` 下的 `model`、`base_url`、`api_key`；保留示例 `[daytona]` 占位字段。该配置不支持 `${ENV_VAR}` 插值。不要把凭据写进命令、提交或日志。普通公开仓库 GET 可不带 GitHub 凭据；Code Search 需要安全注入环境变量 `GITHUB_TOKEN`。TaskPilot 不读取 `.env` 文件。

## CLI

```bash
OPENMANUS_DISABLE_BROWSER_USE=1 .venv/bin/python taskpilot.py \
  --repository FoundationAgents/OpenManus \
  --prompt "读取 README.md，结合仓库信息说明这个项目的用途，并给出来源。"
```

可选 `--ref <branch-or-tag-or-commit>`；省略 `--repository` 时，CLI 通过同步 `AskHuman` 询问。缺少 Tool required 参数时也会澄清。Issue 调查和 Issue → Code 的真实历史示例见[调查记录](REPOSITORY_INVESTIGATION.md)与[参数闭环证据](PR03_PARAMETER_E2E.md)。任务类型不绑定固定工具顺序。

## FastAPI

```bash
OPENMANUS_DISABLE_BROWSER_USE=1 .venv/bin/python -m uvicorn app.taskpilot.service:app --host 127.0.0.1 --port 8000
```

在另一终端调用 HTTP 接口：

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail -X POST http://127.0.0.1:8000/tasks \
  -H 'Content-Type: application/json' \
  -d '{"repository":"FoundationAgents/OpenManus","task":"读取 README.md 并给出仓库用途与来源。"}'
curl --fail http://127.0.0.1:8000/tasks/TASK_ID
```

`POST /tasks` 返回 HTTP 202 与 `task_id`。轮询 `GET /tasks/{task_id}`；结果含 `status`、`execution_status`、`finish_source`、`business_outcome`、`cleanup_status`、`result`、`run_id`。若为 `clarification_pending`，响应中的 `clarification` 含原 `tool_call_id` 和 `missing_fields`。用明确字段回复对应 task/call：

```bash
curl --fail -X POST \
  http://127.0.0.1:8000/tasks/TASK_ID/clarifications/TOOL_CALL_ID/reply \
  -H 'Content-Type: application/json' \
  -d '{"reply":"{\"path\":\"README.md\"}"}'
curl --fail -X DELETE http://127.0.0.1:8000/tasks/TASK_ID
```

等待澄清不阻塞其他请求；默认等待上限 120 秒。取消只针对本地任务，远端超时后的执行结果可能未知。非法请求返回 422，未知任务返回 404，重复、迟到或不匹配的回复返回 409。任务与 pending 状态保存在当前进程内存，服务重启后旧 ID 返回 404。`/health` 只说明进程可响应；本地 Uvicorn 不预热 embedding 模型。Agent 的 `finished`/`terminate(success)` 不是独立验证的业务成功，服务的 `business_outcome` 仍为 `not_verified`。

## Docker Compose

将已填写的本地模型配置通过只读挂载传入：

```bash
export TASKPILOT_MODEL_CONFIG="$PWD/config/config.toml"
docker compose -f compose.taskpilot.yaml build
docker compose -f compose.taskpilot.yaml up -d --no-build
curl --fail http://127.0.0.1:8000/health
docker compose -f compose.taskpilot.yaml down
```

镜像固定 Python 3.12.14、依赖和 embedding revision；首次下载到 Docker 命名卷，后续复用。当前已验证 Linux amd64 的本机受限服务，Compose 默认只绑定 `127.0.0.1`，不传入 `GITHUB_TOKEN`。模型缓存为空、离线复用、代理不通和启动失败的处理见[Docker 指南](T059_DOCKER.md)；从干净 checkout 的真实 HTTP、澄清、404、停止和重启证据见[部署记录](T062_DEPLOYMENT.md)。
