# Baseline Runtime Validation — PR-01

固定 OpenManus 上游：`3309bf4e416fb1c74b008f3e86494439a31bad53`（`openmanus-baseline-3309bf4`）。本轮只验证原始 Agent / Tool Calling 主链路，没有修改 `app/` 产品代码或实现 TaskPilot Feature。

## Environment

- OS：Ubuntu 22.04.5 LTS，Linux x86_64（运行内核 `6.8.0-138-generic`）。
- 上游 Python 要求：`setup.py` 声明 `python_requires >=3.12`，README 推荐 Python 3.12。
- 宿主 Conda base：Python 3.13.5、pip 25.1.1。项目隔离环境：`.venv`，Python 3.12.13、pip 26.2.1。
- 本次关键版本：uv 0.12.19、litellm 1.63.14、openai 1.66.5、torch 2.12.1+cpu、pytest 8.3.5、pydantic 2.10.6、browsergym-core 0.13.3、playwright 1.51.0、mcp 1.5.0。
- 依赖输入为固定 baseline 的原始 `requirements.txt`，本次 180 个解析依赖的精确版本固定在 `requirements-baseline.lock.txt`。`litellm==1.63.14` 是满足 `crawl4ai` 下限且符合上游 `openai~=1.66.3` 的窄传递依赖约束；使用官方 CPU PyTorch wheel 避免安装本轮不需要的 CUDA 组件。原始 requirements 未修改。
- 安装成功，`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。初始 PyPI 下载遇到 TLS handshake EOF；取消代理环境变量后完整安装成功。
- 最小文本与 PlanningTool 验证不需要 Docker daemon 或 Playwright 浏览器下载。上游的其他工具可能需要额外系统运行条件，本轮未逐一验证。
- 真实 provider 使用 DeepSeek OpenAI-compatible Chat Completions（模型 `deepseek-flash`，Base URL `https://api.deepseek.com`）。运行配置从被 Git 忽略的 `config/config.toml` 读取；本报告、模板和测试不包含 API Key。

## Configuration

- `app/config.py::Config._get_config_path()` 优先读取 `config/config.toml`，否则读取 `config/config.example.toml`；通过 `tomllib` 直接解析 TOML。
- 当前代码不加载 `.env`、不展开环境变量，也不识别 `api_key_env` 或 `provider` 字段。有效配置使用 `[llm]` 下的 `model`、`base_url`、`api_key`、`api_type`、`api_version`。
- 缺少 `[daytona]` 时，`Config._load_initial_config()` 仍会构造要求 `daytona_api_key` 的设置项；因此最小配置模板提供本地无效占位值。配置加载不建立 Daytona 网络连接。
- 本次实测配置状态：模型与地址可读、API Key 已配置（只检查布尔状态）、Daytona 占位值存在、`sandbox.use_sandbox=false`、MCP server 数为 0。
- 无 `config/mcp.json` 时，`Manus.create()` 默认仍会尝试自动启动 Browser Use MCP。通过已有环境变量 `OPENMANUS_DISABLE_BROWSER_USE=1` 禁用它；没有删除或修改 Browser Use 实现。
- 可复制 `config/config.example-baseline.toml` 为私有的 `config/config.toml` 并填入 Key。`.gitignore` 忽略真实配置。

## Startup

从仓库根目录执行。需要 Python 3.12 和 uv：

```bash
uv venv --python 3.12.13 --seed .venv
uv pip install --python .venv/bin/python --torch-backend cpu -r requirements.txt -c requirements-baseline.lock.txt
.venv/bin/python -m pip check
```

准备私有配置：

```bash
cp config/config.example-baseline.toml config/config.toml
# 在本地编辑 config/config.toml，填入 API Key；不要提交该文件
chmod 600 config/config.toml
```

真实运行原始 CLI 的安全 terminate smoke：

```bash
OPENMANUS_DISABLE_BROWSER_USE=1 PYTHONPATH=. .venv/bin/python main.py --prompt "请立即调用 terminate(status=failure) 结束本次验证。"
```

运行确定性 characterization tests（不访问 provider）：

```bash
.venv/bin/python -m pytest tests/baseline/test_runtime.py tests/tools/test_browser_use_mcp.py -q
```

显式运行真实 provider case：

```bash
OPENMANUS_DISABLE_BROWSER_USE=1 PYTHONPATH=. .venv/bin/python tests/baseline/run_live.py plain
OPENMANUS_DISABLE_BROWSER_USE=1 PYTHONPATH=. .venv/bin/python tests/baseline/run_live.py tool --disable-thinking
OPENMANUS_DISABLE_BROWSER_USE=1 PYTHONPATH=. .venv/bin/python tests/baseline/run_live.py failure --disable-thinking
OPENMANUS_DISABLE_BROWSER_USE=1 PYTHONPATH=. .venv/bin/python tests/baseline/run_live.py consecutive --disable-thinking
```

脚本只装配上游已有的内存型 `PlanningTool` 和 `Terminate`，并限制每次 `agent.run()` 时长；它没有装配默认 Python / 文件编辑器工具池。Case A 限制为一步，其他 case 最多六步。DeepSeek 多轮 tool-call 使用官方支持的 non-thinking 模式；脚本只通过现有 `LLM.ask_tool(..., **kwargs)` 传 `extra_body`，不改产品源码。脱敏运行证据写入 Git 忽略的 `logs/baseline/live-*.json`。

## Verified Behaviors

证据文件位于本机 Git 忽略目录 `logs/baseline/`；离线测试结果来自本轮实际 pytest 输出。真实模型运行使用了本机配置，不代表其他账号、模型或网络环境也必然成功。

| Case | Result | Evidence |
| --- | --- | --- |
| A — 普通文本 | VERIFIED | `live-plain.json`；一次真实 DeepSeek 请求返回 MCP 一句话说明，token usage 被记录。限制 `max_steps=1` 后返回文本和 `Terminated: Reached max steps (1)`；Agent state 回到 `IDLE`、`current_step` 重置为 0、Memory 保留本轮消息。 |
| B — Local Tool Calling | VERIFIED | `live-tool.json`；真实模型选中 `planning(command=create)`，日志明确记录 `Manus selected 1 tools`、`Activating tool: 'planning'`、创建结果，再下一轮调用 terminate。`run()` 返回两个 step 的工具 observation。 |
| C — Tool Failure | VERIFIED | `live-failure.json`；真实调用不存在的 plan，PlanningTool 返回 `Error: No plan found...`；错误以 tool observation 进入下一轮，Agent 继续并调用 `terminate(status=failure)`，run 正常返回而非抛异常。 |
| D — Terminate / run / CLI | VERIFIED | `cli-terminate.log` 和 Case B/C 的真实 terminate 记录。CLI 日志显示 `The interaction has been completed with status: failure`，随后 `Request processing completed.`；进程成功结束。`BaseAgent.run()` 返回 step summary，但 CLI 不打印该返回值。terminate 的 success/failure status 都只结束交互，本身不是对任务真实成功的独立判定。 |
| E — 同一 Agent 连续运行 | VERIFIED | `live-consecutive.json`；同一实例两次 `run()`，第一轮写入 Planning Memory 标记 `baseline-round-one`，第二轮模型能读回。第一轮执行 2 steps 后 `current_step=2`，第二轮从 Step 3 开始并以 `current_step=3` 结束；Memory 跨轮保留，state 每轮回到 `IDLE`，`_initialized=false`，连接集合为空。 |
| Offline characterization | VERIFIED | `13 passed`，包含 `tests/baseline/test_runtime.py` 和 `tests/tools/test_browser_use_mcp.py`。覆盖文本直到步数上限、真实 StrReplaceEditor 本地读文件及错误 observation、terminate 的两种 status、terminate 同批后续工具仍执行、连续运行 Memory/step、异常 cleanup、CLI 不输出 run return、Browser Use 工具消息。 |
| MCP lifecycle | NOT VERIFIED | 本轮明确禁用自动 Browser Use；配置中无 MCP servers。真实 MCP server 的连接、调用与断开未验证。 |

真实工具链的实测证据为 Agent 日志中模型选出的函数调用、ToolCollection dispatch、工具执行日志、下一轮输入中带有 `tool_call_id` 的 observation（离线 characterization）及真实 run return。离线 tests 使用 scripted LLM 只固定 Agent 基线行为，不作为 provider 证据。

## Known Issues

### Reproduced this run

- 默认自动 Browser Use MCP 会影响最小启动；设置 `OPENMANUS_DISABLE_BROWSER_USE=1` 后，真实 Manus/CLI 启动完成且没有 MCP 连接。
- 普通文本响应不会自动结束 Agent。本次真实单步 Case A 是由 `max_steps=1` 结束；离线双步 characterization 也确认它会继续请求，直到步数上限。
- terminate 的 `status=failure` 仍使 run / CLI 正常结束；它是 Agent 的终止信号，不等价于进程错误或独立成功判定。
- `BaseAgent.run()` 返回按 step 汇总的字符串；原始 CLI 仅写 `Request processing completed.`，不把该返回值打印给用户。
- 工具返回的业务错误（不存在 plan）被包装为 observation；Agent 可以继续下一个 step 并自行决定后续动作，没有内建自动 Retry。
- 同批 tool calls 中，即使先调用 terminate，后续工具调用仍会被执行；离线 characterization 固定了这一实际行为。
- 安装成功但运行 import/pytest 时会出现上游依赖警告：Requests 对 `urllib3 2.8.0` 的兼容范围警告，以及 Pydantic v2 对上游 class-based config / `__fields__` 的弃用警告。本轮未升级或改写上游依赖/代码。
- 首次依赖解析遇到 PyPI TLS handshake EOF；清除代理变量后安装完整成功。CPU wheel 是针对本地无 GPU 验证环境的安装选择。

### Static findings not reproduced

- DeepSeek thinking mode 下多轮 tool calls 对 `reasoning_content` 的协议兼容问题：本次 Case B/C/E 使用官方 non-thinking 请求参数，因此没有复现默认 thinking 模式的多轮协议失败。默认 thinking 模式多轮 tool call 兼容性为 **NOT VERIFIED**。参考 [DeepSeek Thinking Mode / Tool Calls](https://api-docs.deepseek.com/guides/thinking_mode/)。
- 配置里有 MCP server 时的真实连接/断线行为、Daytona 真实请求、Docker sandbox、Playwright 浏览器运行时，以及自动 Browser Use MCP 运行：均未在本轮启用，**NOT VERIFIED**。
- 未遍历或执行默认危险性更高的 Python、文件编辑等工具；Local Tool 真实集成验证只使用内存型 PlanningTool。

## Runtime Architecture Notes

- 真实执行确认 `Manus.create()` → `BaseAgent.run()` → `ToolCallAgent.think/act()` → `ToolCollection.execute()` → Tool → tool observation 的主链路可通过 DeepSeek Chat Completions 工作。planning 的 observation 被追加到消息历史，并由后续 LLM 请求继续处理。
- 普通 Assistant 文本不自动改变 Agent 完成状态；达到 max steps 才产生步数上限终止。`terminate` 能立刻结束 Agent Loop，但 status 文本不阻止 CLI 报告 request processing completed。
- 每次 run 的 `finally` 清理 Manus MCP 连接并恢复 `IDLE`。同一实例仍保留 Memory 与累计 `current_step`；只有到达 max steps 的路径会将 step 计数归零。本次未连接 MCP，故真实 MCP cleanup 细节仍未验证。
- DeepSeek 多轮工具调用在本次 non-thinking 模式实测成功；默认 thinking 模式的兼容性仍待单独验证，不据此修改源码。
