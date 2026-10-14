# T072 — 失败分析与评测复现

本审计使用冻结 Core Holdout v1.0.0（`8251f53a…`）、评分契约（`289db810…`）及已提交的 T066–T071 运行/复核/指标文件。[机器可读失败索引](../eval/results/t072_failure_audit_v1.json)包含**全部**14 条非成功、9 条上游 unsupported、八条预期故障探针，以及可用的运行 `run_id`、Trace/调用 ID、来源指针、HTTP 状态和 T069 原始原因。`eval/t072_failure_audit.py` 拒绝过期分类或已改变的 T069 结果。上游没有 TaskPilot Trace 或原生 `run_id`，其 `t066:<sample>:<file>:<line>` 只是审计定位符，**不是伪造运行 ID**。

## 失败分类与证据

分类表示观察到的影响因素，不证明路由导致最终结果。单次运行不能确定模型决策因果时记 **UNKNOWN**。索引保留精确 run/Trace/call 引用，以下说明追查方式：

- **路由：**K=3 `core-005/006` 在标准下一步期望 `github_read_file` 时选择 `github_code_search`，全量 `core-006` 也如此。对照 T069 `selection_steps` 与 T067/T068 的 `routing_events`、选择、`github_http`。工具选错不能单独证明检索器导致决策。
- **参数/Slot：**T071 自然解析后 16 步，全量 10 FP/4 FN，K=3 12 FP/5 FN；索引列全部 16 个错误步骤及字段来源。`core-006/020/021` 出现 `model_tool_call` 的额外 path/query，`core-005` 增加代码查询。这是步骤标签不匹配，不是已确认标准值错误。repository/ref 来自 `confirmed_context`；额外值是否无用户/Observation 依据为 **NOT VERIFIED**，不能从 FP 推断。
- **工具/MCP：**上游 `core-016` 的选定 `t066_original_openmanus_mcp_retest_2.jsonl` 中真实 Server 返回 `isError=true`，随后 Agent 宣称成功，失败状态丢失。TaskPilot 两组各一次 `fail_read` 失败和失败 Observation，无重试或虚假业务成功。`core-017` 真实 `slow_read` 一次执行触及 50 ms 截止时间，Trace 为 unknown，清理完成，远端完成为 **NOT VERIFIED**。`core-019` 两组均同 ID 下受控 503 后真实 200、两次尝试；这是正确处理预期故障，不是评测失败。
- **上下文/证据：**上游 `core-002/004/005/006/018/020/021` 在六步内未获所需固定证据。TaskPilot `005/006` 缺关键代码；K=3 `020` 读取 Issue/代码却缺完整固定 URL 和直接支持的评论引用。其 run `55e7c86d75954560946dadb9015ca90f` 有成功读取 Trace，阻止成功的是最终引用，而非执行失败。
- **权限/网络：**TaskPilot `005/006` 有真实 Code Search 403；`005` 固定文件在 `chop_cells` 前截断，`006` 全量内容在 `decimal_places_validator` 前截断，K=3 未读 validator 文件。403 是权限还是限流/策略，凭响应为 **UNKNOWN**。TaskPilot `018` 各一次真实 GitHub 404，永久失败、零重试和失败 Observation；上游 `018` 未实际取得 404。
- **超时/重试/预算：**上游 `002/004/005/006/018/020/021` 耗尽六步，K=3 `005` 耗尽八步，`011` 虽有固定来源回答也达到八步，且先有失败终止尝试再最终终止。`017` 的 50 ms 超时为 unknown，不是确认取消。`019` 两组均有界增加一次尝试。

上游 unsupported `core-007`–`012`、`014`、`017`、`019` 单列，**不能当作零 Token 已尝试失败**。它用 `python_execute` 调查 GitHub，没有等价类型化 GitHub 工具，也没有 TaskPilot 校验、路由、重试或截止时间策略；9 条无结构 GitHub 运行无可比 Slot 分母。

## 冻结评分一致性裁定

`core-011` 的冻结 `business_evidence` 要求候选、选中工具和分数；真实全量基线为 `routing_top_k=None`、零路由事件、全 Schema 请求。T067 人工复核认为固定来源回答成功，T069 按**正式冻结条件**改为 NOT VERIFIED（正式总数 18/21）。要求全量组提供路由分数是**评测构造不匹配**，不是产品漏记事件。只看业务证据的诊断口径会计 19/21，但**不是正式重评分**，不能替代 T069–T071 的 18/21。未发现评分实现错误或指标勘误，冻结数据、旧复核、原结果和脚本不变；未来修改标准需要新版本和独立结果系列。

**预期结果**含负向任务的正确拒绝/控制；**业务成功**要求冻结证据和适当最终回答；**可靠性正确**表示工具失败被正确分类、有界处理，不证明正向调查成功。因此 `016/017/018/019` 因满足安全行为而记场景成功，同时保留失败/unknown 尝试。`terminate(success)` 从不单独提供业务证据；正式任务指标混合正向、无匹配和受控故障，不能称为 18/21 正向调查成功率。

T069 自主选择分母保持上游 **2/15**、全量 **6/16**、K=3 **7/16**；并行额外工具会使步骤错误，候选召回另算，9 条零 LLM 不进入自主分母。预期结果达标为上游 **4/12 supported**（全量覆盖 4/21，另有 9 unsupported），两组 TaskPilot 均 **18/21**。T070 包括失败尝试和全部支持且已尝试任务：上游 12，TaskPilot 各 21，并拆成 12 自然+9 受控。T071 自然解析 TP/FP/FN 为上游两条 MCP **3/0/0**、全量 **41/10/4**、K=3 **40/12/5**；受控各 **14/0/0**。缺参检测、脚本回复后有效均各 **3/3**，不衡量自然漏参或真人等待。

## K=3 成本负收益样本

T070 配对差值为 K=3−全量：

- `core-004`：**+27,994 Provider Token / +5,120.8 ms**，Provider 7 次对 3 次；7 次查询 Embedding 为 59.3 ms。
- `core-011`：**+61,085 / +6,846.1 ms**，Provider 8 次对 4 次；8 次查询为 79.5 ms。
- `core-020`：**+14,064 / +1,867.7 ms**，Provider 5 次对 4 次；索引和 5 次查询共 70.1 ms；虽读取成功，最终引用不足。

这三条额外回合及 LLM/工具时间高于实测 Embedding 成本，是观察而非路由因果估计。21 对中 9 条 K=3 更慢，12 条自然任务中 3 条 Token 更多。总计 −6,172 Token、−2,923.1 ms 不抹去逐任务退步；阶段时间可重叠，不相加为端到端。

## 复现清单与独立检查

- 数据：[留出集](../eval/datasets/core_benchmark_v1_holdout.json)、[评分](../eval/datasets/core_benchmark_v1_scoring.json)、[来源固定](../eval/datasets/core_benchmark_v1_sources.json)，均 v1.0.0；原运行/复核 SHA-256 在 T069 结果中。
- 上游：`3309bf4e416fb1c74b008f3e86494439a31bad53`，Docker Python 3.12.14，原工具加受控 stdio 服务，六步/90 秒；保留复测。
- TaskPilot：T067 HEAD `021f23ce…`，T068 `d65e303a…`，相同 `product_app_tree=c9c64263…`，八步/180 秒。GitHub/MCP 各为相同 5 个业务工具加 terminate；T067 无筛选，T068 K=3。完整 manifest 包含配置、Schema 暴露及选定尝试。模型 deepseek-flash、温度 0、输出 1024、thinking 关闭，Provider revision **NOT AVAILABLE**；MCP/FastMCP 1.5.0 stdio。Embedding 为 `sentence-transformers/paraphrase-MiniLM-L3-v2`，revision `4ca70771034acceecb2e72475f72050fcdde4ddc`，向量版本 `tool-metadata-json-v1/cosine-v1/mean-pool-128-v1`。
- 离线复算：干净源码导出，固定 `taskpilot-service:pr08c-nocache` 镜像（`sha256:98326c8c…`），**无网络**、无宿主 `.venv`；临时副本将跟踪的 `config.example-daytona.toml` 复制为 `config.toml`，密钥字段为空，只满足上游配置要求。T069/T070 输出按 JSON 比较相同，T071 `--check` 通过。这是对**同一冻结原证据**的独立依赖/运行时检查，不是新模型运行。

仓库根目录的历史离线复算步骤：

```sh
replay_dir=$(mktemp -d)
mkdir -p "$replay_dir/src" "$replay_dir/out"
git archive HEAD | tar -x -C "$replay_dir/src"
cp "$replay_dir/src/config/config.example-daytona.toml" "$replay_dir/src/config/config.toml"
docker run --rm --network none -v "$replay_dir/src:/srv/taskpilot" -v "$replay_dir/out:/out" -w /srv/taskpilot --entrypoint python taskpilot-service:pr08c-nocache -m eval.t069_score_accuracy_success --output /out/t069.json
docker run --rm --network none -v "$replay_dir/src:/srv/taskpilot" -v "$replay_dir/out:/out" -w /srv/taskpilot --entrypoint python taskpilot-service:pr08c-nocache -m eval.t070_cost_latency --output /out/t070.json
docker run --rm --network none -v "$replay_dir/src:/srv/taskpilot" -w /srv/taskpilot --entrypoint python taskpilot-service:pr08c-nocache -m eval.t071_slot_metrics --check
```

将 `/out/t069.json`、`/out/t070.json` 按解析后的 JSON 与提交结果对比，空白不是指标差异。

新的 [K=3 复跑文件](../eval/results/t072_replay_k3_v1.jsonl)独立于 T068，使用 `e5f7f54…`、相同产品树/数据 hash/K/模型/预算。当时命令为 `.venv/bin/python -m eval.t068_k3_baseline --ids core-003,core-013,core-016,core-017,core-018,core-019 --output eval/results/t072_replay_k3_v1.jsonl`，预检 8 个可变 Issue、11 个固定文件均 HTTP 200，但不消除外部漂移风险。

复跑 `003` 两次路由模型调用、两次真实 GitHub 200；`013` 两次路由模型调用、一次真实 `echo_read` stdio，均 Trace 有效、清理完成。受控 `016` 一次 isError 失败、`017` 一次 unknown 超时、`018` 一次真实 404 不重试、`019` 同 ID 一次受控 503 后真实 200；四条均明确**零 LLM**，不算模型自主决策。每行保留自己的 run/call ID、状态、Observation 和清理。`017` Server 在客户端关闭慢流后记录 `ClosedResourceError`，客户端有界清理，远端完成仍 **NOT VERIFIED**。复跑不替代或重评分 T066–T071。

额外的[干净容器全量复跑](../eval/results/t072_replay_clean_full_v1.jsonl)使用新本地 Git clone、相同 `e5f7f54…`、固定镜像、只读配置和只按名称传入的 `GITHUB_TOKEN`，不用宿主 `.venv` 或 Embedding 缓存。`003` 两次模型/两次 GitHub 200；`013` 两次模型/一次 stdio echo；`018` 一次真实 404；`019` 同 ID 受控 503 后真实 200。四条 Trace 有效、清理完成；真实全量 manifest 零路由，是集成复现，**不替代冻结 K=3 对照**。

首次容器尝试因镜像无评测脚本记录 HEAD 所需的 `git`，在样本前停止；第二次临时挂载宿主 Git 后成功，这是复现工具约束，未改产品或评测源码。Linux 上独立复跑示例（配置只读挂载，Token 仅传名称）：

```sh
git clone --no-hardlinks --local . /tmp/taskpilot-t072-live-checkout
docker run --rm --network host \
  -v /tmp/taskpilot-t072-live-checkout:/srv/taskpilot \
  -v "$PWD/config/config.toml:/srv/taskpilot/config/config.toml:ro" \
  -v "$(command -v git):/usr/bin/git:ro" \
  -e GITHUB_TOKEN -e HTTPS_PROXY -e HTTP_PROXY -e NO_PROXY \
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory \
  -e GIT_CONFIG_VALUE_0=/srv/taskpilot \
  -w /srv/taskpilot --entrypoint python taskpilot-service:pr08c-nocache \
  -m eval.t067_full_tool_baseline --ids core-003,core-013,core-018,core-019 \
  --output /srv/taskpilot/eval/results/t072_replay_clean_full_v1.jsonl
```

复跑需使用不存在的 clone 目录和新输出路径；上面的固定历史文件名若已存在，改为新的文件名。新记录有新 run ID，必须独立审阅，不能覆盖正式结果。非 Linux、不同代理或缺少宿主 Git 的等价环境在此未验证。

## 限制

每系统正式留出集只运行一次，不能证明显著性或 Provider 整体收益。可变 Issue、Code Search 权限/策略、代理、网络时间及未知 revision 可改变复跑。上游工具池和预算不同，只说明能力覆盖，不能作同工具池因果比较。脚本澄清无真人等待分布；MCP 超时后远端完成、模型自然漏参后澄清及重复运行方差均为 **NOT VERIFIED**。
