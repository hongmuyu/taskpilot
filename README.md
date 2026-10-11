# TaskPilot

TaskPilot 是基于 [OpenManus](https://github.com/FoundationAgents/OpenManus) 二次开发的单 Agent、只读 GitHub 仓库调查工具。用户明确指定 `owner/repo` 后，Agent 根据任务及每一步的 Observation 动态调查仓库结构、Issue 和源码，并尽量在回答中给出文件路径、Issue 编号及来源 URL。它不是固定的调查工作流，也不提供仓库写入或 Shell 执行权限。

当前交付的是 **Core 调查链路**，不是最终 Core MVP Benchmark 或公开服务。功能边界与验收状态以 [任务总账](docs/TASKS.md) 为准；实际调用链见 [架构文档](docs/TASKPILOT_ARCHITECTURE.md)。

## 已实现的 Core

- 五个 GitHub REST 只读工具：`github_repository_info`、`github_issue_search`、`github_issue_detail`、`github_code_search`、`github_read_file`，以及 `terminate`。专用 Agent 的工具池不含 OpenManus 的 Shell、Python、浏览器或文件编辑工具。
- 参数 JSON 与现有 Tool schema 的执行前校验；缺少 required 字段时强制澄清，回复合并后重校验，原 `tool_call_id` 保持关联。已确认的 repository/ref 可在当前任务中复用。
- 基于当前任务和最近相关 Tool Observation 的内存 embedding 检索。FastAPI 服务默认向模型发送最多三个业务工具 schema，并额外保留必要控制工具；完整 ToolCollection 仍负责真实 dispatch。CLI 保持未启用 routing 时的全量工具行为。
- Tool 结果状态、MCP `isError`、有界 timeout、只对显式幂等读取的有界 retry，以及版本化 Trace 1.1。Trace 记录调用关系及 provider 明确报告的 token usage；缺失 usage 保持 unknown。
- CLI 与进程内 FastAPI 服务。服务支持任务启动、状态/结果查询、取消和非阻塞缺参回复；每个任务有独立 Agent、Repository Context 和 pending 状态。

通用 MCP 客户端已在受控真实 stdio Server 上验证连接、发现、调用、断连和重连；**上述专用 GitHub 调查入口不会自动连接 MCP Server**。相关证据见 [MCP 验收](docs/evidence/t035_real_mcp_e2e.json)与[故障验收](docs/evidence/t038_real_failure_e2e.json)。

## 本地安装与配置

以下命令从含有 TaskPilot 文件的干净 checkout 根目录执行。已验证平台为 Linux amd64；本地方式需要 `uv 0.10.9`、Python 3.12.x 与模型下载网络。Docker 镜像精确固定 Python 3.12.14，不使用宿主 `.venv`。

```bash
uv venv --python 3.12
uv pip install --python .venv/bin/python --torch-backend cpu -r requirements-service.lock.txt
cp config/config.example-baseline.toml config/config.toml
chmod 600 config/config.toml
```

在本机编辑被 Git 忽略的 `config/config.toml`，填写 `[llm]` 下的 `model`、`base_url`、`api_key`；保留示例中的 `[daytona]` 占位字段。模型须兼容 OpenAI 风格的 Chat Completions tool calling。配置文件**不支持** `${ENV_VAR}` 插值；不要把真实 key 写入命令、提交或运行日志。TaskPilot 不需要启用上游默认 Browser Use MCP：

```bash
export OPENMANUS_DISABLE_BROWSER_USE=1
```

公开仓库的 metadata、Issue 和文件读取可不带 GitHub 凭据；GitHub Code Search 需要环境变量 `GITHUB_TOKEN`。仅在确实需要 Code Search 时，通过当前 shell 的安全凭据机制注入只读 token。项目不读取 `.env` 文件。Token 的权限与 GitHub 限流由使用者控制。

## CLI：真实仓库调查

```bash
.venv/bin/python taskpilot.py \
  --repository FoundationAgents/OpenManus \
  --prompt "读取 README.md，结合仓库信息说明这个项目的用途，并给出来源。"
```

可选 `--ref <branch-or-tag-or-commit>`；未指定时文件读取使用仓库默认分支。若省略 `--repository`，CLI 会通过同步 `AskHuman` 询问；缺少工具 required 参数时也会在 CLI 中询问。以下是另外两类任务示例，实际工具顺序由模型与 Observation 决定：

```bash
.venv/bin/python taskpilot.py --repository FoundationAgents/OpenManus \
  --prompt "调查仓库中与 MCP 调用失败相关的 Issue，列出编号和证据。"
.venv/bin/python taskpilot.py --repository FoundationAgents/OpenManus \
  --prompt "选取一个与 MCP 失败有关的 Issue，定位可能相关的源码并解释依据。"
```

历史三类真实调查见 [PR-02 调查记录](docs/REPOSITORY_INVESTIGATION.md)；其中“尚未实现”的部分是当时状态，当前能力请以本 README、架构文档与任务总账为准。

## FastAPI：任务状态与缺参回复

使用上述本地配置与依赖，从 checkout 根目录启动仅监听本机的服务：

```bash
.venv/bin/python -m uvicorn app.taskpilot.service:app --host 127.0.0.1 --port 8000
```

另开终端调用真实 HTTP 接口：

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail -X POST http://127.0.0.1:8000/tasks \
  -H 'Content-Type: application/json' \
  -d '{"repository":"FoundationAgents/OpenManus","task":"读取 README.md 并给出仓库用途与来源。"}'
curl --fail http://127.0.0.1:8000/tasks/TASK_ID
```

`POST /tasks` 返回 `202` 与 `task_id`。将返回值替换上面的 `TASK_ID`，轮询 `GET /tasks/{task_id}`；结果含 `status`、`execution_status`、`finish_source`、`business_outcome`、`cleanup_status`、`result` 和 `run_id`。若状态为 `clarification_pending`，响应中的 `clarification` 含原 `tool_call_id` 和 `missing_fields`；通过 `POST /tasks/{task_id}/clarifications/{tool_call_id}/reply` 只回复该 task/call 对应的明确字段，例如：

```bash
curl --fail -X POST \
  http://127.0.0.1:8000/tasks/TASK_ID/clarifications/TOOL_CALL_ID/reply \
  -H 'Content-Type: application/json' \
  -d '{"reply":"{\"path\":\"README.md\"}"}'
curl --fail -X DELETE http://127.0.0.1:8000/tasks/TASK_ID
```

取消是本地任务取消；远端超时操作是否已停止可能未知。非法输入返回 422，错误 task 返回 404，重复、迟到或不匹配的澄清回复返回 409。等待澄清不阻塞其他 HTTP 请求；默认等待上限为 120 秒。任务状态只在当前服务进程内存中保存，重启后不会恢复。`/health` 表示 HTTP 进程可响应；本地 Uvicorn 启动不预热 embedding 模型。

## Docker：可复现启动

在已填写上述本地配置后：

```bash
export TASKPILOT_MODEL_CONFIG="$PWD/config/config.toml"
docker compose -f compose.taskpilot.yaml build
docker compose -f compose.taskpilot.yaml up -d --no-build
curl --fail http://127.0.0.1:8000/health
docker compose -f compose.taskpilot.yaml down
```

镜像固定 Python 3.12.14、CPU 依赖和 embedding revision；配置只读挂载，镜像不包含本地 key。首次启动会将模型下载到 Docker 命名卷，后续复用缓存；下载失败会明确退出，HTTP 不会假装就绪。当前主机的代理只监听宿主 loopback，Docker bridge 无法直接访问它；已验证使用**同一镜像的临时容器**预热空卷，再以 `HF_HUB_OFFLINE=1` 启动。命令和冷/热启动说明见 [T059 Docker 指南](docs/T059_DOCKER.md)，干净 checkout 的实际 HTTP 验收、停止/重启及隔离 builder 无缓存构建复核见 [T062 部署记录](docs/T062_DEPLOYMENT.md)。默认 builder 曾有 BuildKit snapshot 导出故障，详见该记录。Compose 默认只绑定 `127.0.0.1`，不传入 `GITHUB_TOKEN`，不是公开部署配置。

## 测试与评测入口

服务锁文件只包含运行依赖；运行测试再安装固定版本的测试工具：

```bash
uv pip install --python .venv/bin/python --torch-backend cpu pytest==8.3.5 pytest-asyncio==0.25.3
.venv/bin/python -m pytest -q tests/taskpilot tests/baseline/test_runtime.py tests/tools/test_browser_use_mcp.py
```

这些是确定性与隔离集成回归，不能替代真实 Provider/GitHub/MCP 验收；真实脱敏证据索引见 [任务总账](docs/TASKS.md)。上游完整测试集含与当前 Python 版本不符的 sandbox 环境断言，不能把上述相关回归说成全量通过。

Routing 数据集分为 [28 条 experiment](eval/datasets/routing_v1_experiment.json) 与 [12 条 evaluation](eval/datasets/routing_v1_evaluation.json)，schema 见 [routing_v1.schema.json](eval/datasets/routing_v1.schema.json)。[T029 实验](docs/T029_TOPK_EXPERIMENT.md)用于选 K，[T030 对照](docs/T030_ROUTING_COMPARISON.md)比较 K=3 和真正的全量工具基线；原始结果在 `eval/results/`，运行脚本为 `eval/t029_topk_experiment.py` 与 `eval/t030_routing_comparison.py`。这些是**单步工具选择阶段实验**，不是 Phase 8 的最终任务级 Benchmark。[T065 独立 holdout 与冻结口径](docs/T065_BENCHMARK_DATASET.md)已建立；正式基线运行和最终数字仍待 T066–T073 验收。重跑脚本会调用真实模型并产生费用，请将 `--output` 指向新的文件，勿覆盖现有证据。

## 边界、来源与许可证

- 当前 Core 是 GitHub 只读调查；写入、审批/风险门禁、复杂 Memory、通用 fallback、Demo UI 和公开部署安全属于尚未交付的 P1/P2 范围。不要将上游仓库内其他入口视为 TaskPilot 已启用能力。
- `finished` 或 Agent `terminate(success)` 是执行/声明状态，**不是独立验证的业务成功**；服务目前将 `business_outcome` 保持 `not_verified`。真实模型自然遗漏关键参数、默认 thinking mode 多轮兼容、其他 MCP transport、公开部署与 Phase 8 完整 Benchmark 仍有 NOT VERIFIED 项，详见任务总账。
- 本仓库保留 OpenManus 的祖先历史，以固定上游 commit `3309bf4e416fb1c74b008f3e86494439a31bad53`（tag `openmanus-baseline-3309bf4`）为二次开发基线。原始多语言 README（[中文](README_zh.md)、[한국어](README_ko.md)、[日本語](README_ja.md)）描述的是上游 OpenManus，不能作为 TaskPilot 功能清单。保留原 [MIT LICENSE](LICENSE)；TaskPilot 的新增说明和代码建立在该基线上。
