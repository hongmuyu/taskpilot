# T062 — 本地可复现部署验收记录

本轮从 `codex/pr-08c-t059` 的干净 checkout（`c3f82b697ea9832c04154390f4608d1154528842`）验证 Docker 构建和真实 HTTP 使用。checkout 没有 `.venv`、本地 `config.toml` 或模型缓存；模型配置来自 checkout 外的 Git ignored 文件，只读挂载。服务只绑定 `127.0.0.1`，保留现有只读 GitHub Tool；这不是公网部署。

## 已验证环境与配置

- Linux x86_64 / Ubuntu 22.04.5 LTS，Docker Engine 29.2.1，Compose 5.0.2；镜像内 Python 3.12.14。
- 基础镜像按 `Dockerfile.taskpilot` 固定 digest；`requirements-service.lock.txt` 的 68 项依赖均以 `==` 固定。实测镜像 ID：`sha256:344b0f197559ff00eefa7a9f4bce238919a103deabc117da8409df331c56f1d0`。
- 模型配置键名为 `llm.model`、`llm.base_url`、`llm.api_key`，实际模型为 `deepseek-flash`；Provider revision 不可用。镜像不包含配置文件、Key 或宿主 `.venv`。缺少配置时容器明确退出。
- embedding 为 `sentence-transformers/paraphrase-MiniLM-L3-v2`，revision `4ca70771034acceecb2e72475f72050fcdde4ddc`。新 Docker 卷在预热前确认不存在，创建后为空；同镜像临时容器冷下载耗时 65.06 秒，无网络离线复用耗时 7.21 秒，输出 384 维向量。

## 从干净检出目录启动

先按 [使用指南](USAGE.md#本地配置) 准备**外部**模型配置文件，不要提交或粘贴 Key。以下示例使用独立 Compose project 和本机端口；`TASKPILOT_MODEL_CONFIG` 必须是已存在配置文件的绝对路径。

```bash
export TASKPILOT_MODEL_CONFIG=/absolute/path/to/config.toml
export TASKPILOT_PORT=18062
docker compose -p taskpilot-t062 -f compose.taskpilot.yaml build
docker compose -p taskpilot-t062 -f compose.taskpilot.yaml create --no-build
```

当前主机的模型下载代理只监听宿主 loopback，Docker bridge 无法直接连接。使用 [T059 指南](T059_DOCKER.md)中的同镜像、空卷预热方式；独立 project 的卷名应改为 `taskpilot-t062_model-cache`：

```bash
docker run --rm --network host -e http_proxy -e https_proxy \
  --mount type=volume,source=taskpilot-t062_model-cache,target=/var/cache/taskpilot/huggingface \
  --entrypoint python taskpilot-service:t059 \
  -c 'import asyncio; from app.taskpilot.tool_embedding_index import LocalTransformerEmbeddingBackend; asyncio.run(LocalTransformerEmbeddingBackend().embed(["startup"]))'
HF_HUB_OFFLINE=1 docker compose -p taskpilot-t062 -f compose.taskpilot.yaml up -d --no-build
curl --fail http://127.0.0.1:18062/health
```

原 `build --no-cache` 在默认 BuildKit 的镜像导出/提取阶段因父 snapshot 不存在而退出 1；相同干净 checkout 的常规 `build` 成功，依赖层由 Docker 缓存复用。PR 最终复核另建隔离的 `docker-container` builder（BuildKit v0.33.1），通过宿主网络和标准代理 build args 从零安装全部 68 个锁定依赖，成功导出 OCI 镜像并导入 Docker：退出码 0、耗时 252 秒。新镜像可导入服务模块，Python 为 3.12.14，且不含本地 `config.toml` 或 `.venv`。见[无缓存构建复核](evidence/t062_no_cache_builder_review.json)。这证明产品镜像能够从零构建；默认 builder 的 snapshot 故障仍保留为该环境的限制，默认 builder 路径未复测成功。先前构建镜像的实际冷模型卷、离线启动和 HTTP E2E 证据仍见下文；T059 的构建证据另见 [T059 交付记录](evidence/t059_docker_delivery.json)。未修改 Dockerfile 或锁定依赖来掩盖故障。

## 真实 HTTP 验收

三条任务均经容器外 `127.0.0.1` HTTP 接口、真实 Provider 和真实 GitHub API，Trace 1.1 由**容器内** `validate_trace_events` 校验。脱敏原始结果见 [总记录](evidence/t062_reproducible_deployment.json)和各 case 文件：

- [正常只读调查](evidence/t062_normal_http.json)：`github_read_file` 与 `github_repository_info` 均返回 HTTP 200；任务 `finished`，清理 `complete`，Trace 32 事件。
- [缺参澄清](evidence/t062_clarification_http.json)：测试入口在真实模型选择 `github_read_file` 后受控移除一次 `path`，**不是模型自然漏参**。服务返回 `clarification_pending`，澄清前零 Tool dispatch、零 GitHub HTTP；回复 API 返回 202，保留原 `tool_call_id`，Trace 显示 `pending → merged → validation success`，原调用仅 dispatch 一次，真实 GitHub HTTP 200，清理 `complete`。测试入口仅挂载 `tests/taskpilot/t062_clarification_server.py`，复用生产 FastAPI/Agent/Tool/Trace 路径。
- [真实读取失败](evidence/t062_not_found_http.json)：明确不存在的文件触发 GitHub HTTP 404；工具和 Observation 均记 `failure`，分类为 `permanent`，一次 attempt、零 retry。Agent 后续发出了执行完成声明，服务的 `business_outcome` 仍为 `not_verified`，没有把声明当成独立的业务成功证据。

运行时任务状态及缓存均只在当前进程/卷内。用 `docker compose ... down` 停止后 HTTP 不可用，保留卷离线重启后健康恢复；旧 task ID 返回 404、镜像 ID 未变，见 [重启记录](evidence/t062_restart.json)。最后停止测试入口并执行 `docker compose -p taskpilot-t062 -f compose.taskpilot.yaml down -v`；容器、网络、卷均已删除，两个本机端口不可用。镜像留在本机以便复核。

## 限制

- 默认 builder 的无缓存镜像导出仍有 snapshot 故障；隔离 builder 的无缓存构建与导出已通过。其他主机 OS/架构、默认 bridge 直接下载模型和公网部署未验证。
- 自然模型漏掉 `path`、Provider revision、独立业务成功判定未验证。公开入口及托管部署分别属于未实施的 T061/T063。
- 活动模型 Key 的字面量未出现在本轮新增文件、分支 diff、证据或当时容器日志。通用形态扫描唯一命中现有静态测试标记，已核对与活动 Key 不同；证据文件不含原始提示、Tool 参数、模型回答或凭据。
