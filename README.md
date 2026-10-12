# TaskPilot

TaskPilot 是基于 [OpenManus](https://github.com/FoundationAgents/OpenManus) 二次开发的只读 GitHub 仓库调查 Agent。它根据任务和最新工具结果调查仓库、Issue 与源码，并保留可追溯的调用、失败及评测证据。当前交付的是供本机或受限环境使用的 **Core MVP**。

## Demo

试着问：**“调查 OpenManus #1427 中的 MCP 错误，并定位相关源码。”** 已记录的[真实 Issue → Code 调查](docs/PR03_PARAMETER_E2E.md)依次使用 Issue、Code Search 和文件读取，最终引用 Issue 与源码。这里链接的是历史真实运行证据，不是在线演示服务。

## Features

- **动态仓库调查：**五个 GitHub REST 只读工具，覆盖仓库概览、Issue 搜索与详情、代码搜索、文件读取；工具顺序由模型结合 Observation 决定。
- **执行前参数保护：**复用工具原有 schema；缺参进入澄清，回复合并后重新校验，保留原 `tool_call_id`。已确认的仓库与 ref 可在当前任务中复用。
- **语义工具选择：**服务默认用内存 embedding 检索最多 3 个业务工具，必要控制工具另行保留；完整工具集合继续负责真实执行。CLI 默认使用完整工具 schema。
- **可靠执行与追踪：**有界超时、仅对明确幂等的读取重试、MCP `isError` 状态保留，以及关联 LLM、路由、工具和成本的 Trace 1.1。
- **两种入口：**CLI 和进程内 FastAPI；服务提供任务查询、取消及非阻塞缺参回复。通用 MCP 客户端已用真实 stdio Server 验证，但专用 GitHub 入口不会自动连接 MCP Server。

## Quick Start

需要 Python 3.12、`uv`、可用的模型 API 和网络；以下命令在 Linux amd64 上验证。请在本地填写被 Git 忽略的配置文件中的 `[llm].model`、`base_url`、`api_key`，不要提交凭据。

```bash
git clone https://github.com/hongmuyu/taskpilot.git
cd taskpilot
uv venv --python 3.12
uv pip install --python .venv/bin/python --torch-backend cpu -r requirements-service.lock.txt
cp config/config.example-baseline.toml config/config.toml
chmod 600 config/config.toml
```

在本机编辑 `config/config.toml` 后运行：

```bash
OPENMANUS_DISABLE_BROWSER_USE=1 .venv/bin/python taskpilot.py \
  --repository FoundationAgents/OpenManus \
  --prompt "读取 README.md，说明这个仓库的用途并给出来源。"
```

## Installation

本地安装使用固定的服务依赖锁文件；根目录 `setup.py` / `requirements.txt` 仍属上游 OpenManus 打包入口，不用于安装 TaskPilot 服务。模型配置只从 `config/config.toml` 读取，不对 `${ENV_VAR}` 做插值。公开仓库的常规读取可无 GitHub token；Code Search 需要以环境变量 `GITHUB_TOKEN` 安全注入只读凭据。Docker 固定 Python 3.12.14，首次启动会下载并缓存 embedding 模型；冷启动、离线预热与代理限制见[Docker 指南](docs/T059_DOCKER.md)。

## Usage

CLI 可指定 `--ref <branch-or-tag-or-commit>`；省略 `--repository` 时会询问。启动本机服务：

```bash
OPENMANUS_DISABLE_BROWSER_USE=1 .venv/bin/python -m uvicorn app.taskpilot.service:app --host 127.0.0.1 --port 8000
```

在另一终端检查服务：

```bash
curl --fail http://127.0.0.1:8000/health
```

API 提供 `POST /tasks`、`GET /tasks/{task_id}`、`DELETE /tasks/{task_id}` 和按 `tool_call_id` 回复的澄清接口。请求、响应、Docker Compose 命令和状态语义见[使用指南](docs/USAGE.md)。服务状态保存在进程内存中，重启后旧任务无法恢复；`terminate(success)` 不代表答案已独立验证。

## Architecture

TaskPilot 复用 OpenManus 的 Agent Loop 和 ToolCollection。在每一步，Agent 根据任务与最新 Observation 选择工具；路由只改变本回合发送给模型的 schema，执行仍经过参数校验、澄清、超时/重试和 Trace。[架构文档](docs/TASKPILOT_ARCHITECTURE.md)解释完整调用链与 MCP 边界。

## Benchmark

固定的 **21 条独立 Holdout** 每组各选定一条正式评分记录；上游 MCP 初始化复测和后续可复现性复跑另行保留，不混入该分母。自主模型工具选择正确步数为原始 OpenManus **2/15**、TaskPilot 全量工具 **6/16**、K=3 **7/16**。冻结预期结果达标为上游 **4/12 supported**（另有 **9/21 unsupported**）、两组 TaskPilot 均 **18/21**；两组正向调查均为 **8/11**。18/21 包含安全拒绝与故障正确处理，不能当作正向业务成功率。

同工具池对照中，K=3 的 Provider token 总量较全量工具低，但 **9/21** 条任务更慢；单次小样本不支持整体提升或统计显著性结论。完整的 Slot、Token、Latency、失败、限制及可复算入口见[正式 Benchmark](docs/BENCHMARK.md)。

## Documentation

从[公开文档索引](docs/INDEX.md)进入[使用指南](docs/USAGE.md)、[架构](docs/TASKPILOT_ARCHITECTURE.md)、[部署验收](docs/T062_DEPLOYMENT.md)、[测试](docs/TESTING.md)和[Benchmark](docs/BENCHMARK.md)。这些公开入口在 `core-mvp-v1` 冻结后整理，应从后续 `main` 阅读；冻结 Tag 保留原始源码与证据。原始脱敏运行记录、评分脚本及冻结数据保留在 `eval/`；真实 GitHub/MCP/Docker 证据链接也在文档索引中。

## Acknowledgements

TaskPilot 基于 [FoundationAgents/OpenManus](https://github.com/FoundationAgents/OpenManus) 的源码与祖先历史开发，固定上游基线为 [`3309bf4`](https://github.com/FoundationAgents/OpenManus/commit/3309bf4e416fb1c74b008f3e86494439a31bad53)。感谢 OpenManus 原作者及贡献者；上游项目的完整介绍和多语言说明请见其[官方仓库](https://github.com/FoundationAgents/OpenManus)。本仓库保留的上游组件指南不代表 TaskPilot 专用入口已交付这些能力。

## License

本项目保留上游的 [MIT License](LICENSE)，TaskPilot 的二次开发内容在此基础上提供。
