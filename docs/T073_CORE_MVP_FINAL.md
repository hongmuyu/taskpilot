# TaskPilot Core MVP — final exit review and benchmark

**Scope:** local or access-restricted, single-agent, read-only GitHub repository
investigation. This is a Core MVP candidate, not a public service or a claim
of statistically significant improvement. The candidate is frozen at the
T073 commit, identified by local tag `core-mvp-candidate-v1`; PR-09 remains
unmerged at this review.

## Evidence and scoring provenance

- The independent [21-sample holdout](../eval/datasets/core_benchmark_v1_holdout.json)
  is `taskpilot_core_final` v1.0.0, SHA-256
  `8251f53ac812684507bfe85086214393bcbb3dd955d19a54d5df12a0d3affafe`.
  Its [frozen scoring contract](../eval/datasets/core_benchmark_v1_scoring.json)
  has SHA-256
  `289db810fda451dee8a6d9aa2765e79995eae2b38401c1512da249b547608c2a`.
  The 28 T028 experiment and 12 T030 evaluation samples are recorded as prior
  provenance, not counted as new holdout tasks. [Source catalog](../eval/datasets/core_benchmark_v1_sources.json)
  pins code commits/blobs and records mutable Issue snapshots. The 21 tasks
  cover eight frozen scenario categories; [label review](T065_BENCHMARK_REVIEW.json)
  was performed by the implementing agent, not an independent reviewer.
- [Original OpenManus](../eval/results/t066_original_openmanus_v1.jsonl) is
  pinned to upstream `3309bf4e416fb1c74b008f3e86494439a31bad53`.
  [Full-tools](../eval/results/t067_full_tools_v1.jsonl) and
  [K=3](../eval/results/t068_k3_v1.jsonl) use the same TaskPilot product app
  tree `c9c64263f7070141bab3dfb9d7df6a8aa7871bb3`, deepseek-flash,
  temperature 0, max_tokens 1024, thinking disabled, the same profile tools,
  normalization and 8-step/180-second budget. The full-tools group exposes
  all allowed schemas with **no** semantic candidate filtering. K=3 limits
  business schemas per step and retains control tools. K=3 embedding revision
  is `4ca70771034acceecb2e72475f72050fcdde4ddc`; Provider revision is
  **NOT AVAILABLE**. Upstream has its original tool pool and a different
  six-step/90-second budget: it is a product baseline, not a same-tool-pool
  causal routing control.
- The committed [T069 selection/outcome](../eval/results/t069_accuracy_success_v1.json),
  [T070 cost](../eval/results/t070_cost_latency_v1.json), and
  [T071 slot](../eval/results/t071_slot_metrics_v1.json) results are computed
  by their corresponding `eval/t069_score_accuracy_success.py`,
  `eval/t070_cost_latency.py`, and `eval/t071_slot_metrics.py` scripts.
  Each records input hashes and per-sample source references. The
  [T072 failure index](../eval/results/t072_failure_audit_v1.json) and
  [reproducibility audit](T072_FAILURE_REPRODUCIBILITY.md) retain all
  failures and `NOT VERIFIED` outcomes. None of these frozen inputs or
  historical outputs was changed for T073.

## Formal benchmark results

**Tool Selection Accuracy** scores only gold `selection_eligible` steps where
a model actually chose tools; every parallel selected tool must be acceptable.
Original OpenManus: **2/15** steps (13.3%; 2/10 all-step-correct tasks).
TaskPilot full-tools: **6/16** (37.5%; 5/11 tasks). TaskPilot K=3: **7/16**
(43.8%; 7/11 tasks). The nine TaskPilot controlled cases make zero LLM calls
and are absent from these denominators. Candidate recall is not this metric.
The one-step full/K=3 difference in one run does not establish a routing gain.

**Frozen expected-outcome success:** Original OpenManus **4/12 supported**
(33.3%), with **9/21 unsupported** reported separately; all-holdout coverage
is 4/21, not a comparable 21-task attempt rate. Full-tools and K=3 each
reach **18/21** (85.7%), with three `NOT VERIFIED` each. Their 12 natural
model tasks are **9/12** each; of 11 positive investigation tasks, **8/11**
each satisfy frozen evidence, while `core-012` is a successful no-match
handling task. Their nine zero-LLM controlled parameter/fault tasks are
**9/9** each. These counts represent expected scenario outcomes, including
correct refusal and fault containment; **18/21 is not a positive
investigation success rate**. `terminate(success)` is never sufficient
business evidence. The FastAPI service deliberately leaves independent
`business_outcome=not_verified` after an Agent finish declaration.

**Full-tools `core-011` construct limit:** the frozen criterion asks for a
routing candidate/score, which a true unfiltered baseline must not emit.
T067's human review found pinned business evidence, but T069's **formal**
frozen-criterion score is `NOT VERIFIED`; it remains in the 18/21 count.
A diagnostic view that omits only the inapplicable routing criterion would
count 19/21 full-tools; this is **not** a formal rescore, metric correction,
or a basis for claiming K=3 caused a business-success change. See the
[T072 ruling](T072_FAILURE_REPRODUCIBILITY.md).

**Parameter/Slot Precision, Recall, F1** uses field/value pairs, checking
the frozen value and provenance rather than schema validity alone. On natural
resolved typed steps: Original OpenManus TP/FP/FN **3/0/0**, P/R/F1 **100/100/100%**
on only **two comparable MCP steps**; its nine unstructured GitHub Python
tasks have no typed-slot denominator. Full-tools **41/10/4**, P/R/F1
**80.4/91.1/85.4%** across 16 steps. K=3 **40/12/5**, **76.9/88.9/82.5%**
across 16 steps. These TaskPilot resolved figures include trusted repository
context and scripted reply sources, so model-explicit-only results are shown
separately: full **13/10/4**, **56.5/76.5/65.0%**; K=3 **12/12/5**,
**50.0/70.6/58.5%**. Critical-value accuracy on natural resolved fields is
full **41/45**, K=3 **40/45**. Each TaskPilot group's six controlled
value-bearing steps contribute **14/0/0** separately. Missing detection
was **3/3**, scripted clarification yielded valid parameters **3/3**, and
error type recognition was **2/2** in each group. These zero-LLM probes do
not measure natural model omission or unscripted human replies. No confirmed
wrong gold value was observed; whether extra unlabelled fields lacked any
user/Observation basis remains **NOT VERIFIED**.

**Provider usage and end-to-end latency** include failures, retries and
cleanup. Every selected Provider attempt reported input/output/total usage
(original 60 attempts; full 44; K=3 48); missing usage is never estimated.
Original OpenManus, on 12 supported attempts: input/output/total tokens
**150,459 / 12,384 / 162,843**; end-to-end total **154.970 s**, mean
**12.914 s**, median **13.953 s**. Full-tools, on all 21: **231,765 / 11,543 /
243,308**; total **110.592 s**, mean **5.266 s**, median **1.930 s**.
K=3, on all 21: **227,438 / 9,698 / 237,136**; total **107.669 s**,
mean **5.127 s**, median **1.746 s**. The TaskPilot all-21 means/medians
include nine zero-token controlled cases; on the 12 natural model tasks,
full-tools mean/median tokens are **20,275.7 / 12,784**, K=3 **19,761.3 /
9,503**; mean/median latency is full **8.748 / 8.950 s**, K=3 **8.510 /
8.981 s**. Original OpenManus's supported set, tool pool and budget differ,
so its totals are not used as a routing-effect estimate.

On the same 21 TaskPilot IDs, K=3 minus full-tools is **−6,172 provider
tokens** and **−2.923 s** end-to-end in aggregate, but K=3 is slower in
**9/21** cases; it uses more tokens in **3/12** natural model cases. Three
clear negative pairs are `core-004` **+27,994 tokens/+5.121 s**,
`core-011` **+61,085/+6.846 s**, and `core-020` **+14,064/+1.868 s**.
K=3 performed **52 embedding calls/2.508 s**; full-tools performed none.
Each TaskPilot group has one controlled 503 followed by one extra successful
read attempt and about **100 ms** retry waiting; no extra LLM retry occurred.
Three immediate scripted clarification replies per group have no measured
human waiting time (**NOT AVAILABLE**, not zero). Upstream tool/retry/human
phase timings are also **NOT AVAILABLE**. LLM, Tool, Embedding and Retry
phase clocks can overlap and must not be summed into end-to-end time.

## Failure analysis and limits

The [T072 audit](T072_FAILURE_REPRODUCIBILITY.md) classifies 14 formal
non-success cases, nine upstream unsupported cases, and eight expected-fault
probes. `core-005/006` lack decisive code evidence after truncation and
GitHub Code Search 403; K=3 `core-020` read the issue and code but omitted
the required pinned code citation. MCP `isError`, client timeout with unknown
remote completion, a real GitHub 404, and a controlled 503→real 200 are
retained as actual attempt/Observation evidence. Their safe handling can
satisfy a negative scenario without proving a positive investigation.
Original `core-016` falsely declared success after `isError=true`, which
the TaskPilot result-status path preserves as failure.

The holdout is small (21), each system's original result is a single run,
and mutable GitHub Issue bodies, Code Search permissions, proxy/network
conditions and unknown Provider revision limit replay equivalence. No
statistical significance or general superiority is claimed. Other MCP
transports, real human waiting, a naturally omitted `path` from the model,
default thinking-mode multi-turn compatibility, remote completion after
timeout, unrelated upstream sandbox full-suite behavior, non-Linux Docker,
and public deployment remain **NOT VERIFIED** or out of Core scope. The
normal Docker service is local/restricted and read-only. P1/P2 write safety,
approval, advanced Memory, fallback, UI and public deployment remain deferred.

## Core MVP Exit Criteria — 28/28 evidence review

The 53 Core P0 task cards comprise T001–T033, T035, T037–T038, T053–T057,
T059–T060, T062, and T065–T073. All must be `DONE` before marking this
review complete. The following checks correspond in order to
[the ledger's 28 exit items](TASKS.md#core-mvp-exit-criteria):

1. **Three real investigations and dynamic steps:** [T021 GitHub E2E](PR03_PARAMETER_E2E.md), [T068 runs](T068_K3_COMPARISON_RUN.md) and multi-step `core-020/021` raw Trace. Individual holdout failures remain in the denominator.
2. **Parameter validation:** T013–T015/T020 deterministic matrices and T021 invalid-input zero real HTTP.
3. **Clarification:** [T021](PR03_PARAMETER_E2E.md), [T057 service](evidence/t057_service_clarification.json) and [T062 HTTP](evidence/t062_clarification_http.json): forced pending, original call ID, zero pre-reply dispatch, one post-validation real GitHub call. Model omission was controlled.
4. **Semantic routing:** T022–T028 metadata/index/retrieval/control tests and K=3 routing events in [T068 raw runs](../eval/results/t068_k3_v1.jsonl).
5. **Top-K experiment origin:** [T029](T029_TOPK_EXPERIMENT.md), [T030](T030_ROUTING_COMPARISON.md); K=3 was selected before this frozen holdout.
6. **Real GitHub Tool:** [T021](PR03_PARAMETER_E2E.md), [T068](T068_K3_COMPARISON_RUN.md) and [T072 replay](../eval/results/t072_replay_clean_full_v1.jsonl) contain actual REST status/call evidence.
7. **Real MCP lifecycle:** [T035](evidence/t035_real_mcp_e2e.json) and [T038](evidence/t038_real_failure_e2e.json) use FastMCP 1.5.0 stdio connect/discover/call/disconnect/reconnect; fixture-only T020 evidence is not substituted.
8. **Result status:** T031 regression and T035/T038 `isError=true` failure Observation.
9. **Bounded timeout:** T032/T037 regression and T038 real stdio timeout with unknown remote result.
10. **Read retry:** T033/T037 classification tests and T068 `core-018/019` permanent 404 versus controlled 503→200 attempts.
11. **No false success on real failures:** [T038](evidence/t038_real_failure_e2e.json), [T062 404](evidence/t062_not_found_http.json) and T069 evidence-based scoring.
12. **Trace/cost:** Trace Schema 1.1 T053–T055, [real Provider/GitHub Trace](evidence/t055_provider_github_trace.json), T070 attempt-level cost.
13. **FastAPI/non-blocking clarification:** T056–T057 API and concurrency regression plus [real service trace](evidence/t057_service_clarification.json); finish declaration remains separate from business success.
14. **Docker startup:** [T059 guide](T059_DOCKER.md), [image/startup evidence](evidence/t059_docker_delivery.json) and [real HTTP smoke](evidence/t059_docker_github_smoke.json).
15. **README/architecture:** [README](../README.md) and [source-grounded architecture](TASKPILOT_ARCHITECTURE.md) checked against actual API/config and this final report.
16. **Reproducible restricted deployment:** [T062 record](T062_DEPLOYMENT.md), [three real HTTP cases](evidence/t062_reproducible_deployment.json), [restart/cleanup](evidence/t062_restart.json), [isolated no-cache build](evidence/t062_no_cache_builder_review.json).
17. **Frozen dataset:** [T065 contract and review](T065_BENCHMARK_DATASET.md), 21 independent samples, pinned refs/source snapshots.
18. **Original baseline:** [T066 environment/results](T066_ORIGINAL_OPENMANUS.md), original pool and nine unsupported cases kept separate.
19. **Same-tool-pool full baseline:** [T067](T067_FULL_TOOL_BASELINE.md), manifest/44 LLM schema checks and zero routing events.
20. **K=3 comparison:** [T068](T068_K3_COMPARISON_RUN.md), 21 retained outcomes, real GitHub/MCP and controlled probes labelled.
21. **Selection/success scoring:** [T069](T069_ACCURACY_SUCCESS.md), frozen evidence and explicit denominators; core-011 limitation retained.
22. **Token/latency scoring:** [T070](T070_TOKEN_LATENCY.md), provider usage, embedding/retry/phase overlap and unavailable waiting reported.
23. **Slot scoring:** [T071](T071_PARAMETER_SLOT_METRICS.md), model/context/scripted channels separated.
24. **Failure/reproduction audit:** [T072](T072_FAILURE_REPRODUCIBILITY.md), clean offline metric replay and separate real integration reruns.
25. **Resume traceability:** conservative claims below cite these frozen sources and explicit denominators.
26. **All Core P0 DONE:** task-card count and Dashboard are checked after updating T073; deferred P1/P2 do not gate Core.
27. **Scope tests:** `tests/taskpilot tests/baseline/test_runtime.py tests/baseline/test_t066_original_baseline.py tests/tools/test_browser_use_mcp.py` **390 passed** on this branch: 372 TaskPilot tests, 13 runtime/Browser MCP tests and five T066 original-baseline tests. T071 documented that historical 363-versus-359 command selection cannot be reconstructed; no T070 test was deleted, and the current explicit suite avoids that ambiguity. Historical live evidence is separately linked. The unrelated upstream full-suite sandbox assertion remains outside this passing claim.
28. **No fabricated metrics:** no mock labelled live, no unsupported as zero-token attempt, no finish declaration as independent business success, no statistical or overall improvement claim.

## Clean-checkout evaluation route and candidate freeze

Use the [README](../README.md) for locked local/Docker setup and
[T072's exact offline metric commands](T072_FAILURE_REPRODUCIBILITY.md) to
recalculate T069–T071 from committed raw records without host `.venv` or
network. The no-network Docker replay of T069/T070 matched committed JSON;
T071 `--check` passed. The clean-checkout real full-tools replay needed a
Git executable for the runner's HEAD capture because the deployment image
does not bundle Git; [T072](T072_FAILURE_REPRODUCIBILITY.md) records the
mount-only workaround, exact command, failure and separate results. A fresh
real run also needs the existing ignored model config and safe GitHub token
injection; never put secret values in arguments or output. The pinned
deployment image and lockfile are verified on Linux amd64; Docker bridge
proxy restrictions, embedding cold download and isolated-builder no-cache
build are documented in [T059](T059_DOCKER.md) and [T062](T062_DEPLOYMENT.md).
The candidate tag points to this final review commit; no merge or public
release is implied.

## Resume-safe wording

The following may be adapted for a resume **with its sample-size context**:

> 基于 OpenManus 实现只读 GitHub 仓库调查 Agent，集成参数校验与澄清、语义 Top-K、真实 stdio MCP、有界超时/读取重试、结构化 Trace、FastAPI 和 Docker；建立 21 条冻结 Core Holdout 与三组可复算评测。单次同工具池对照中，K=3 与全量工具的模型选工具正确步数分别为 7/16 与 6/16，冻结预期结果均为 18/21；K=3 的 Provider token 总量为 237,136（全量 243,308），但 9/21 条任务更慢，未证明统计显著提升。

Shorter variants should retain **21 tasks, one run, same-tool-pool**, and
the negative latency result if they quote a K=3 comparison. Do not quote
18/21 as positive investigation success; the positive investigation subset
is 8/11 for both TaskPilot groups. Original OpenManus's 4/12 supported
outcome is a different-tool-pool capability baseline, not a causal routing
improvement. Do not present controlled missing-parameter/fault probes as
natural model autonomy or an independently verified human production study.
