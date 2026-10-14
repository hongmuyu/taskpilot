# Docker 部署指南（Linux amd64）

本文部署现有的只读 TaskPilot FastAPI 服务。镜像固定 Python 3.12.14 和 `requirements-service.lock.txt` 中的 CPU 依赖，不使用宿主 `.venv`。首次启动将固定的 Embedding 模型下载到独立 Docker 卷，再开放 HTTP。

历史构建与真实 HTTP 验收见 [T062 部署记录](T062_DEPLOYMENT.md)。本文的操作说明不代表本次文档更新重新运行了部署验收。

## 1. 前置条件

- 已安装并启动 Docker Engine，且 `docker compose` 可用。需要有运行 Docker 的权限；以下命令均使用同一普通用户的 Docker 上下文。
- 已克隆 TaskPilot 并进入仓库根目录，存在 `compose.taskpilot.yaml`。
- 已按 [README](../README.md#第二步填写模型配置) 填写 `config/config.toml` 的真实模型配置，并保留 `[daytona]` 占位段。
- 构建需要访问镜像仓库和依赖下载源；首次启动需要访问 Hugging Face；执行任务需要访问模型 API 和 GitHub。

查看本机工具是否具备：

```bash
docker --version
docker compose version
docker info
```

如果命令不存在，应先完成适合当前系统的 Docker Engine / Compose 安装；如果提示无法连接 daemon 或权限不足，先解决 Docker 本身的服务或权限问题。不要通过开放 Docker socket 的公共读写权限来绕过。

## 2. 准备配置并构建

首次配置时执行，已有配置不会覆盖：

```bash
if [ ! -f config/config.toml ]; then
  cp config/config.example-baseline.toml config/config.toml
fi
chmod 600 config/config.toml
```

用编辑器填写模型 ID、API 基础地址与密钥，不能保留模板占位值。然后在**同一终端**执行：

```bash
export TASKPILOT_MODEL_CONFIG="$PWD/config/config.toml"
docker compose -f compose.taskpilot.yaml build
```

`TASKPILOT_MODEL_CONFIG` 必须是已存在文件的绝对路径；它只指定宿主的挂载文件，不替代 TOML 中的模型配置。配置以只读方式挂载到容器的 `config/config.toml`，不写入镜像。新开终端执行 Compose 前，重新导出这个变量。

使用 `Dockerfile.taskpilot`，不要直接构建根目录的上游 `Dockerfile`。镜像标签由 Compose 固定为 `taskpilot-service:t059`，不是需要从公网拉取的预发布 TaskPilot 镜像。

## 3. 启动并等待就绪

```bash
docker compose -f compose.taskpilot.yaml up -d --no-build
docker compose -f compose.taskpilot.yaml ps
docker compose -f compose.taskpilot.yaml logs --tail=50 -f service
```

首次模型下载可能需要数分钟。日志出现 Uvicorn 启动完成后，另一终端执行：

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health
```

浏览器访问 `http://127.0.0.1:8000/docs` 即可调试接口。项目没有另一个聊天前端；根路径不是聊天页面。跟随日志时按 `Ctrl+C` 只退出日志查看，容器继续运行。

端口占用时，在启动前设置宿主端口：

```bash
export TASKPILOT_PORT=18000
docker compose -f compose.taskpilot.yaml up -d --no-build
```

此时健康接口及浏览器地址改为 `http://127.0.0.1:18000`。Compose 仍只绑定宿主回环地址，容器内端口仍为 8000。

## 4. 提交并查询任务

无需宿主 Python 的调用方式：

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/tasks \
  -H 'Content-Type: application/json' \
  -d '{"repository":"FoundationAgents/OpenManus","task":"读取 README.md，说明仓库用途并给出来源。"}'
```

成功受理返回 HTTP 202。复制返回的 `task_id`，在下面提示中输入；不要把字面量 `TASK_ID` 当成真实 ID：

```bash
read -rp '请输入上一步返回的 task_id: ' TASK_ID
curl --fail --silent --show-error "http://127.0.0.1:8000/tasks/$TASK_ID"
```

重复查询直到完成或需要澄清。`clarification_pending` 的回复方式见[使用指南](USAGE.md#缺参回复与取消)。`finished` / `terminate(success)` 只表示 Agent 声明执行完成，`business_outcome=not_verified` 保留独立业务判定边界；还需查看结果与来源。

## 5. 可选：传入 GitHub Token

默认 Compose 不继承宿主 `GITHUB_TOKEN`。普通公开仓库读取通常可无 Token，代码搜索需要认证；仅在需要时添加覆盖文件。先在当前 Bash 终端安全输入：

```bash
read -rsp '请输入 GitHub Token（输入不回显）: ' GITHUB_TOKEN
echo
export GITHUB_TOKEN
```

将以下内容保存为 `/tmp/taskpilot-github.override.yaml`。文件仅列变量名，不存放真实值：

```yaml
services:
  service:
    environment:
      - GITHUB_TOKEN
```

再用两个配置文件启动：

```bash
docker compose -f compose.taskpilot.yaml -f /tmp/taskpilot-github.override.yaml up -d --no-build
```

后续重建或重启仍使用同样的两个 `-f` 参数，并确认启动终端中已设置变量。不要展示 `docker inspect` 的完整环境或展开后的 `docker compose config`，避免泄露 Token。

## 6. 首次下载、模型缓存与离线预热

Embedding 模型固定为 `sentence-transformers/paraphrase-MiniLM-L3-v2`，revision 为 `4ca70771034acceecb2e72475f72050fcdde4ddc`。缓存卷默认名为 `taskpilot_model-cache`；若使用 `docker compose -p 其他名称`，卷名前缀随之改变。

正常联网时直接按上文启动即可。配置缺失、格式无效或模型下载失败时，容器会退出，HTTP 不会开放。**空缓存不能直接用离线模式启动。** 完成下载后，可用以下命令复用缓存：

```bash
HF_HUB_OFFLINE=1 docker compose -f compose.taskpilot.yaml up -d --no-build
```

这里的离线仅指不再访问模型下载站点；执行调查仍需要模型 API 和 GitHub 网络。

### Linux 宿主代理只监听 127.0.0.1 时

默认 Docker bridge 中的 `127.0.0.1` 是容器本身，不能连接宿主回环代理。对于首次模型下载，可以使用同一镜像在临时容器中预热模型卷，然后运行正常服务：

```bash
export TASKPILOT_MODEL_CONFIG="$PWD/config/config.toml"
docker compose -f compose.taskpilot.yaml create --no-build
docker run --rm --network host \
  -e http_proxy -e https_proxy -e HTTP_PROXY -e HTTPS_PROXY \
  -e no_proxy -e NO_PROXY \
  --mount type=volume,source=taskpilot_model-cache,target=/var/cache/taskpilot/huggingface \
  --entrypoint python taskpilot-service:t059 \
  -c 'import asyncio; from app.taskpilot.tool_embedding_index import LocalTransformerEmbeddingBackend; asyncio.run(LocalTransformerEmbeddingBackend().embed(["startup"]))'
HF_HUB_OFFLINE=1 docker compose -f compose.taskpilot.yaml up -d --no-build
```

前提是宿主已正确设置代理环境变量；上述命令只传变量名，不写代理值。预热下载发生在临时容器内，不复制宿主缓存。此方法只解决模型缓存，**不会给正常 bridge 容器配置模型 API 或 GitHub 的运行时代理**。如果这些 API 也必须通过回环代理访问，先使用 [README](../README.md) 的本地运行方式，或为容器配置实际可达的代理；不要把容器内 `127.0.0.1` 当成宿主地址。

## 7. 远程服务器访问

在服务器上按上述步骤启动，保持服务绑定 `127.0.0.1`。在自己的电脑上建立 SSH 隧道（替换用户名与主机）：

```bash
ssh -N -L 18000:127.0.0.1:8000 用户名@服务器地址
```

然后在自己电脑访问 `http://127.0.0.1:18000/docs`。如果服务器实际使用 `TASKPILOT_PORT=18000`，隧道远端部分也改为 `127.0.0.1:18000`。当前服务没有认证、多租户隔离或持久任务数据库，不属于公网部署方案。

## 8. 停止、重启和排错

在设置过 `TASKPILOT_MODEL_CONFIG` 的项目终端执行：

```bash
docker compose -f compose.taskpilot.yaml ps
docker compose -f compose.taskpilot.yaml logs --tail=50 service
docker compose -f compose.taskpilot.yaml down
```

`down` 停止服务并保留模型卷；下次可重复 `up -d --no-build`。`down -v` 会删除模型缓存，只有明确要清空缓存时使用。重启后原内存任务会丢失，旧 task ID 返回 404。

- **要求设置 `TASKPILOT_MODEL_CONFIG`：**新终端没有导出变量；重新设置绝对配置路径。
- **`model config ...`：**确认文件存在，TOML 正确，三个模型字段已填写，密钥不是模板值。
- **`embedding model unavailable`：**检查下载网络和模型卷；空缓存先在线预热，不能直接离线启动。
- **端口绑定失败：**用 `TASKPILOT_PORT` 改宿主端口，不必停止别的项目。
- **模型或 GitHub 认证错误：**区分模型 TOML 与 GitHub 环境变量，两者不互相替代。
- **构建阶段下载失败：**这是镜像/软件源网络问题，和运行时模型缓存不同；检查 Docker 构建网络与代理。
- **BuildKit snapshot 错误：**历史默认 builder 曾在无缓存镜像导出阶段失败，独立 builder 随后从零构建成功，见 [T062](T062_DEPLOYMENT.md)。不要因此直接清空所有 Docker 镜像、卷或其他项目缓存。

容器进程使用 root 以读取宿主 `0600` 配置挂载，未挂载 Docker socket。历史验收环境、失败和平台限制均保留在部署记录中；Windows、macOS 或其他架构不能据此视为已验收。
