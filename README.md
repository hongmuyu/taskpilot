# 🔎 TaskPilot

TaskPilot 是基于 [OpenManus](https://github.com/FoundationAgents/OpenManus) 二次开发的只读 GitHub 仓库调查 Agent。输入仓库和自然语言任务后，它根据工具结果逐步调查仓库、Issue 与源码，输出带来源的结论。当前交付的是供本机或受限环境使用的 **Core MVP**。

> 💡 **从这里开始：**先运行命令行版本；需要接口时再启动 API 或 Docker 服务。项目目前没有聊天网页，`/docs` 是 API 调试页面。

想先体验：按下面四步操作。准备 Fork 后二次开发：先点 GitHub 页面右上角 **Fork**，克隆自己的仓库，然后按相同步骤安装；开发分支、同步源仓库和代码入口见[开发指南](docs/DEVELOPMENT.md)。

## ✨ 已实现能力

- **🧭 动态调查：**5 个 GitHub REST 只读工具，覆盖仓库概览、Issue 搜索与详情、代码搜索、文件读取；由模型结合工具结果决定下一步。
- **🛡️ 参数校验与澄清：**执行前校验工具参数，缺参时等待补充，沿用原 `tool_call_id` 合并回复并重新校验；已确认的仓库和 ref 可在任务内复用。
- **🎯 语义工具选择：**API 服务默认通过内存 Embedding 检索最多 3 个业务工具，并保留必要控制工具；命令行默认提供全量工具 Schema。
- **🔍 可靠执行与追踪：**有界超时、幂等读取重试、MCP `isError` 状态传递，以及关联模型、路由、工具和成本的结构化 Trace。
- **📦 服务与部署：**提供命令行、FastAPI 和 Docker 入口，支持任务查询、取消与非阻塞缺参回复。通用 MCP 客户端已验证真实 stdio Server；GitHub 专用入口不自动连接 MCP Server。

## 第一步：准备环境

以下命令使用 **Linux / Ubuntu 的 Bash**；已验收的 Docker 平台为 Linux amd64。需要 Git、curl、Python 3.12，以及支持工具调用的 OpenAI-compatible Chat Completions 模型 API。模型 API 需要你自己的可用账号和额度。

Ubuntu 尚未安装基础工具时：

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates libgomp1
```

如果没有 `uv`，先安装并加载路径；已经安装则跳过：

```bash
curl -LsSf https://astral.sh/uv/install.sh -o /tmp/taskpilot-install-uv.sh
sh /tmp/taskpilot-install-uv.sh
source "$HOME/.local/bin/env"
uv --version
```

只体验原项目时：

```bash
git clone https://github.com/hongmuyu/taskpilot.git
cd taskpilot
```

已经 Fork 时，改用自己 Fork 页面的 **Code → HTTPS** 地址：

```bash
read -rp '请粘贴自己 Fork 仓库的 HTTPS 克隆地址: ' TASKPILOT_FORK_URL
git clone "$TASKPILOT_FORK_URL" taskpilot
cd taskpilot
```

上面两种克隆方式选一种即可。

**已经有项目时，直接进入现有项目根目录，不要再嵌套克隆。** 后面的命令都在包含 `taskpilot.py` 的目录执行。

安装 Python 和项目依赖：

```bash
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python --torch-backend cpu -r requirements-service.lock.txt
```

依赖包含 CPU 版 PyTorch，首次下载可能较慢。使用项目的 `requirements-service.lock.txt`；根目录 `requirements.txt`、`setup.py`、`main.py` 和 `Dockerfile` 是保留的上游入口，不是这里的 TaskPilot 启动方式。若已有可用 `.venv`，跳过创建环境这一步。

## 第二步：填写模型配置

首次创建配置，已有文件时保留原内容：

```bash
if [ ! -f config/config.toml ]; then
  cp config/config.example-baseline.toml config/config.toml
fi
chmod 600 config/config.toml
```

用编辑器打开 `config/config.toml`，填写下面三个必需值。**占位符必须替换，直接复制后无法调用模型。**

```toml
[llm]
model = "替换为提供方实际支持工具调用的模型 ID"
base_url = "https://替换为提供方的API地址/v1"
api_key = "替换为你自己的模型API密钥"
max_tokens = 1024
temperature = 0.0
api_type = "openai"

[sandbox]
use_sandbox = false

[daytona]
daytona_api_key = "unused-local-baseline"
```

- `model` 使用提供方给出的准确模型 ID，不是网页产品名称。
- `base_url` 使用 Chat Completions 的 API 基础地址，不填网页聊天地址，也不拼接 `/chat/completions`；是否带 `/v1` 以提供方为准。
- `api_key` 是对应模型平台的密钥，不能用 GitHub Token 替代。配置直接读取 TOML，**不支持 `${ENV_VAR}` 插值，也不会自动加载 `.env`**。
- 保留 `[daytona]` 占位段，这是继承的配置要求；本入口不需要注册或启动 Daytona。
- 配置文件已被 Git 忽略，不要提交或粘贴密钥，也不要把真实密钥写进命令参数或截图。

公开仓库概览、Issue 和文件读取通常可以不带 GitHub Token，受 GitHub 限流约束。**代码搜索需要有相应访问权限的 GitHub Token。** 如需使用，在准备启动程序的同一 Bash 终端输入：

```bash
read -rsp '请输入 GitHub Token（输入不回显）: ' GITHUB_TOKEN
echo
export GITHUB_TOKEN
```

## 第三步：运行一次命令行调查

先用不依赖代码搜索、也不需要下载 Embedding 模型的简单任务：

```bash
OPENMANUS_DISABLE_BROWSER_USE=1 .venv/bin/python taskpilot.py \
  --repository FoundationAgents/OpenManus \
  --prompt "读取 README.md，说明这个仓库的用途并给出来源。"
```

`--repository` 填 `owner/repo`，不是完整 URL；可用 `--ref <分支、标签或提交SHA>` 固定源码版本。省略仓库时会在终端询问；缺少工具必需参数时按提示补充。

正常运行会看到工具执行记录及调查结果。确认结果确实包含读取到的仓库信息和来源；单独看到 `terminate(success)` 只能说明 Agent 声明结束。模型鉴权、网络或工具失败时，先看下方排错说明。

更完整的历史示例是“调查 OpenManus #1427 中的 MCP 错误，并定位相关源码”，见[真实 Issue → 源码调查记录](docs/PR03_PARAMETER_E2E.md)。该记录不是在线演示服务。

## 第四步：按需启动 API 服务

**终端 A**：在项目根目录启动服务，保持终端运行。若要代码搜索，先在这个终端设置 `GITHUB_TOKEN`。

```bash
OPENMANUS_DISABLE_BROWSER_USE=1 .venv/bin/python -m uvicorn \
  app.taskpilot.service:app --host 127.0.0.1 --port 8000
```

**终端 B**：在同一项目根目录检查服务并提交任务。以下示例自动取出返回的任务 ID：

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health

TASK_RESPONSE=$(curl --fail --silent --show-error http://127.0.0.1:8000/tasks \
  -H 'Content-Type: application/json' \
  -d '{"repository":"FoundationAgents/OpenManus","task":"读取 README.md，说明仓库用途并给出来源。"}')
TASK_ID=$(printf '%s' "$TASK_RESPONSE" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["task_id"])')
printf '任务 ID：%s\n' "$TASK_ID"
curl --fail --silent --show-error "http://127.0.0.1:8000/tasks/$TASK_ID"
```

首次调用服务的任务会加载固定的 Embedding 模型，需要访问 Hugging Face，可能比 CLI 慢。`/health` 可访问不代表模型 API 或 Embedding 已就绪。重复执行最后一条查询命令查看进度，或打开 [API 调试页面](http://127.0.0.1:8000/docs)。

- `queued` / `running`：等待或执行中，稍后再查。
- `clarification_pending`：需要补参数，按[使用指南](docs/USAGE.md#缺参回复与取消)回复。
- `finished`：执行已结束，查看 `result`、`execution_status` 和来源证据；`business_outcome=not_verified` 表示没有独立验证答案正确性。
- `failed` / `cancelled`：查看响应和终端错误。任务状态只保存在内存，重启后旧任务 ID 失效。

本地停止服务：在终端 A 按 `Ctrl+C`。如果 8000 端口占用，将启动命令改为 `--port 18000`，所有请求及浏览器地址也对应改为 18000。

## 🐳 使用 Docker 部署

Docker 路径无需宿主 Python 或 `.venv`，但仍须先获取源码、填写 `config/config.toml`，并安装 Docker Engine 与 Compose。完整前置条件、模型预热、代理和 Token 配置见[中文 Docker 部署指南](docs/T059_DOCKER.md)。

```bash
export TASKPILOT_MODEL_CONFIG="$PWD/config/config.toml"
docker compose -f compose.taskpilot.yaml build
docker compose -f compose.taskpilot.yaml up -d --no-build
docker compose -f compose.taskpilot.yaml ps
docker compose -f compose.taskpilot.yaml logs --tail=50 -f service
```

首次启动会下载 Embedding 模型；等日志显示 Uvicorn 已启动后，在另一终端访问 `http://127.0.0.1:8000/health` 和 `/docs`。日志跟随界面按 `Ctrl+C` 只退出日志，不停止容器。停止服务：

```bash
docker compose -f compose.taskpilot.yaml down
```

Compose 默认仅绑定本机，默认不传入 GitHub Token。远程服务器通过 SSH 隧道访问，步骤见部署指南；当前 API 没有认证，不适合直接暴露到公网。

## 🧰 常见问题

- **`uv: command not found`：**完成 uv 安装并执行 `source "$HOME/.local/bin/env"`，或重新打开终端。
- **找不到 `.venv/bin/python` / `ModuleNotFoundError`：**确认位于项目根目录，并按第一步创建环境、安装锁定依赖；不要改用系统 `python` 混跑。
- **提示缺少 `daytona_api_key`：**在 `config/config.toml` 中保留上面的 `[daytona]` 占位段。
- **模型返回 401、404 或不支持工具调用：**核对模型平台、API 基础地址、模型 ID、额度及工具调用支持；不是修改 GitHub Token。
- **GitHub 返回 401/403：**检查 Token 权限、有效性和限流。CLI/API 读取启动进程的环境；Docker 需要显式传入，见部署指南。
- **服务健康但首个任务失败，或容器提示 `embedding model unavailable`：**检查 Hugging Face 下载网络/缓存。离线启动需要提前缓存完整模型，不能只设置 `HF_HUB_OFFLINE=1`。
- **`Address already in use`：**本地改 `--port`；Docker 启动前 `export TASKPILOT_PORT=18000`，请求也改为新端口。
- **TLS、代理或连接超时：**分别排查模型 API、GitHub API、Hugging Face。宿主机的 `127.0.0.1` 代理不能直接被默认 Docker bridge 访问，处理步骤见部署指南。

## 📊 架构与评测

TaskPilot 复用 OpenManus 的 Agent Loop 和 ToolCollection。每步按任务与最新工具结果选择工具；Top-K 只改变发送给模型的 Schema，执行仍经过参数校验、澄清、超时/重试和 Trace。见[中文架构文档](docs/TASKPILOT_ARCHITECTURE.md)。

冻结的 **21 条独立留出任务（Holdout）** 中，每组每条任务选定一条正式评分记录，初始化复测与后续复跑另计。自主模型选工具正确步数：原始 OpenManus **2/15**、TaskPilot 全量工具 **6/16**、K=3 **7/16**。冻结预期结果达标：上游 **4/12 supported**，另有 **9/21 unsupported**；两组 TaskPilot 均 **18/21**，其中正向调查均 **8/11**。18/21 包含安全拒绝和故障正确处理，不能当作正向业务成功率。

同工具池对照中 K=3 的 Provider Token 总量较低，但 **9/21** 条任务更慢；单次小样本不支持整体提升或统计显著性结论。完整口径见[中文评测报告](docs/BENCHMARK.md)。

## 📚 文档导航

- [使用指南：配置、CLI、HTTP 请求和澄清](docs/USAGE.md)
- [部署指南：Docker、模型缓存、代理与远程访问](docs/T059_DOCKER.md)
- [开发指南：目录、环境、修改流程和验证命令](docs/DEVELOPMENT.md)
- [测试范围与真实集成证据](docs/TESTING.md)
- [架构说明](docs/TASKPILOT_ARCHITECTURE.md) · [评测报告](docs/BENCHMARK.md) · [全部文档](docs/INDEX.md)

公开使用文档应从当前 `main` 阅读；`core-mvp-v1` 标签保留冻结的源码与原始证据。`eval/` 中保存脱敏运行记录、数据和评分脚本。

## 📜 上游来源与许可证

本项目基于 [FoundationAgents/OpenManus](https://github.com/FoundationAgents/OpenManus)，固定上游基线为 [`3309bf4`](https://github.com/FoundationAgents/OpenManus/commit/3309bf4e416fb1c74b008f3e86494439a31bad53)，保留其祖先历史和 [MIT 许可证](LICENSE)。上游组件指南不代表 TaskPilot 专用入口已交付这些能力。
