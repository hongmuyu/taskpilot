# TaskPilot 开发指南

本指南面向 Fork 后修改 TaskPilot 的开发者。当前实现边界见[架构文档](TASKPILOT_ARCHITECTURE.md)，首次启动见 [README](../README.md)。项目已交付只读 Core MVP；后续写入、审批、高级 Memory 和 UI 不在当前功能内。

## Fork 与分支

1. 在 TaskPilot 的 GitHub 页面点 **Fork**，将仓库复制到自己的账号。
2. 从自己的 Fork 页面复制 HTTPS 或 SSH 地址并克隆；不要把原作者仓库的 `origin` 当成自己的可写远程。
3. 进入仓库后检查远程，再创建开发分支：

```bash
git remote -v
git switch -c feat/my-change
```

如需跟踪原 TaskPilot，可添加独立远程名 `taskpilot`；已存在时先核对地址，不重复添加：

```bash
git remote add taskpilot https://github.com/hongmuyu/taskpilot.git
git fetch taskpilot
```

使用 `taskpilot` 这个名字是为了与原始 OpenManus 的 `upstream` 区分。同步前先处理自己的未提交改动，查看差异，再选择合并或变基；不要直接覆盖自己的开发成果。完成改动后推到自己的 Fork，并在 GitHub 发起面向原项目 `main` 的 Pull Request：

```bash
git push -u origin feat/my-change
```

PR 说明用中文交代问题、改动和实际验证结果。本文中的命令是开发操作指南，不会自动替你创建分支、提交或推送。

## 准备开发环境

使用 Python 3.12 和 uv，按 README 安装 `requirements-service.lock.txt` 并填写私有配置。已有环境不要重复创建。安装当前文档记录的测试工具：

```bash
uv pip install --python .venv/bin/python --torch-backend cpu pytest==8.3.5 pytest-asyncio==0.25.3
```

不要把上游 `requirements.txt` 与服务锁文件混作同一安装步骤，也不要为了文档或局部功能随意升级上游版本。配置与 GitHub Token 的用法同[使用指南](USAGE.md#本地配置)。

## 目录与修改入口

- `taskpilot.py`：TaskPilot 命令行入口；`main.py` 是保留的上游入口。
- `app/agent/repository_investigation.py`：只读仓库 Agent、工具装配及仓库上下文。
- `app/agent/toolcall.py`：工具选择、调用、参数澄清与恢复；复用上游动态 Agent Loop。
- `app/taskpilot/github_tools.py`：GitHub REST 只读工具与仓库参数。
- `app/tool/tool_collection.py`：统一执行边界、Schema 校验、超时与重试。
- `app/tool/mcp.py`：MCP 连接、发现及工具代理。
- `app/taskpilot/tool_embedding_index.py`、`semantic_tool_retrieval.py`：Embedding 索引及当前步骤的语义检索。
- `app/taskpilot/execution_trace.py`：执行事件、脱敏与成本归因。
- `app/taskpilot/service.py`、`container_start.py`：FastAPI 与容器启动。
- `tests/taskpilot/`：TaskPilot 回归与标明用途的真实集成运行脚本。
- `eval/datasets/`、`eval/results/`：冻结数据、评分规则和原始证据；不要覆盖历史结果。
- `docs/`：公开的使用、部署、架构、测试与历史评测说明。

## 修改流程

1. 确认当前分支和未提交改动，阅读相关源码、测试及本次 Issue/需求；不要覆盖他人工作。
2. 在独立分支只实现当前授权变更，优先复用已有接口；不要把任务意图变成固定工具调用流程。
3. 行为改变时补充对应测试，先验证目标场景，再按风险扩大回归。
4. 同步中文使用说明、配置和错误处理描述。命令、API、路径、状态值保留真实标识，说明文字使用中文。
5. 记录实际执行过的检查、失败和未验证项；真实外部调用与确定性测试分开汇报。

实现时优先复用 OpenManus 的现有接口，只修改当前需求相关内容；不把外部 Issue、网页或 MCP 返回内容当作执行授权。当前入口保持只读，不随意接入 Shell 或写工具；未来新增副作用能力需单独设计参数校验、风险检查、路径边界及人工确认。不要提交模型配置、Token 或包含凭据的日志。

常用只读检查：

```bash
git status --short
git branch --show-current
git diff --stat
git diff --check
```

## 调试与验证命令

局部测试示例：

```bash
.venv/bin/python -m pytest -q tests/taskpilot/test_service.py tests/taskpilot/test_service_clarification.py
```

已交付范围回归：

```bash
.venv/bin/python -m pytest -q tests/taskpilot tests/baseline/test_runtime.py tests/baseline/test_t066_original_baseline.py tests/tools/test_browser_use_mcp.py
```

这是命令说明，不表示每次文档更新都会运行。冻结版本历史结果为 390 项通过；依赖警告和上游全套测试限制见[测试说明](TESTING.md)。普通回归中的测试替身不等于真实 Provider/GitHub 验收；`run_*live*` 和评测执行脚本可能访问外部服务并产生费用，运行前阅读脚本及相应说明。

仓库的格式检查使用 `.pre-commit-config.yaml`：

```bash
uvx pre-commit run --all-files
```

首次使用可能下载检查工具；部分检查会修改格式，执行后查看 `git diff`。CI 中的 pre-commit 通过不等于 pytest 已通过。

## 本地开发服务

```bash
OPENMANUS_DISABLE_BROWSER_USE=1 .venv/bin/python -m uvicorn \
  app.taskpilot.service:app --host 127.0.0.1 --port 8000 --reload
```

`--reload` 只适合开发；源码变更触发重启后，内存中的任务会丢失。不要以多个 worker 运行当前进程内任务服务。接口调用、澄清与取消见[使用指南](USAGE.md)。

## 文档和证据维护

面向使用者的入口是 README、使用、部署、开发、测试及架构文档，统一中文。`Txxx` / `PRxx` 文档保留历史时点、版本、失败、分母和未验证标识；翻译不改变其结论。上游组件资料和许可证保留来源，原始 JSON、Trace、字段名、命令及错误消息不做语言替换。

评测分清原始 OpenManus 产品基线与同工具池全量/K=3 对照，不把受控故障处理记成模型自主业务成功。复算命令见[评测报告](BENCHMARK.md)和[失败与复现记录](T072_FAILURE_REPRODUCIBILITY.md)。
