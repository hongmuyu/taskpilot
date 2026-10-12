# T072 — Failure analysis and benchmark reproducibility

This audit uses the frozen Core holdout v1.0.0 (`8251f53a…`), frozen scoring
contract (`289db810…`), and the committed T066–T071 run/review/metric files.
The [machine-readable failure index](../eval/results/t072_failure_audit_v1.json)
contains **every** non-success case (14), all upstream unsupported cases (9),
eight expected-fault probes, runtime `run_id` where available, Trace event and
tool-call IDs, source pointers, HTTP status, and the exact T069 review reason.
`eval/t072_failure_audit.py` refuses stale classifications or changed T069
results. The original upstream runner has no TaskPilot Trace or native runtime
`run_id`; its `t066:<sample>:<file>:<line>` identifier is an audit locator,
explicitly **not** a fabricated runtime ID.

## Failure classification and evidence

Categories identify observed contributing factors, not a proof that routing
caused the final outcome. Model decision causality is **UNKNOWN** where the
single run cannot establish it. The index carries precise `run_id`, Trace and
call references for each case; these examples show how to follow them:

- **Routing:** K=3 `core-005/006` chose `github_code_search` where the frozen
  next step expected `github_read_file`; full-tools `core-006` did likewise.
  Compare T069 `selection_steps` with `routing_events`, selected calls, and
  `github_http` in the indexed T067/T068 raw rows. A wrong selected tool does
  not by itself prove the retriever caused the decision.
- **Parameter / Slot:** T071 shows 10 FP/4 FN for full-tools and 12 FP/5 FN for
  K=3 on 16 natural resolved steps. The index lists all 16 error steps and
  field origins. `core-006/020/021` include extra `path` or `query` fields
  from `model_tool_call` while frozen steps expect another field; `core-005`
  adds code-search `query`. These are step-label mismatches, not confirmed
  wrong gold values. Confirmed `repository/ref` came from `confirmed_context`.
  Whether an unlabelled extra value was guessed without any user/Observation
  basis is **NOT VERIFIED**; it cannot be inferred from an FP count.
- **Tool / MCP:** Original `core-016` selected retest
  `t066_original_openmanus_mcp_retest_2.jsonl` has a real `isError=true`
  server response followed by an Agent success declaration: failure status
  was lost. TaskPilot full/K=3 `core-016` each have one failed `fail_read`
  attempt and failure Observation, no retry or false business success. In
  `core-017`, one real `slow_read` dispatch reaches the 50 ms client deadline:
  Trace status is `unknown`, cleanup completes, and remote completion remains
  **NOT VERIFIED**. The `core-019` controlled first 503 plus real GitHub 200
  uses two attempts and one call ID in both TaskPilot groups. This is an
  expected fault handled correctly, not a benchmark failure.
- **Context / Evidence:** Original `core-002/004/005/006/018/020/021` ended
  without the required pinned evidence within the six-step budget. TaskPilot
  `core-005/006` lack decisive code evidence; `core-020` K=3 read issue and
  code but its final answer lacks a complete pinned code URL and directly
  supported comment citation. K=3 `core-020` run
  `55e7c86d75954560946dadb9015ca90f` has successful read Trace events;
  the missing final citation, rather than tool execution, blocks success.
- **Permission / Network:** Actual GitHub Code Search 403 responses occur in
  `core-005/006` TaskPilot runs; the `core-005` pinned file response was also
  truncated before `chop_cells`, while `core-006` full-tools file content ended
  before `decimal_places_validator` and K=3 did not read the validator file.
  The root of Code Search 403 (authorization versus rate/policy) is
  **UNKNOWN** from these responses. `core-018` TaskPilot groups each have one
  actual GitHub 404, classified permanent with zero retry and failure
  Observation; original OpenManus `core-018` never obtained an actual 404.
- **Timeout / Retry / Budget:** Original `core-002/004/005/006/018/020/021`
  consumed its six-step allowance; K=3 `core-005` consumed eight steps,
  `core-011` also reached eight despite an answer with pinned sources. For
  `core-011`, a failed termination attempt preceded final termination. The
  50 ms `core-017` timeout is an `unknown` tool result, never reported as
  confirmed cancellation. `core-019` retry backoff is bounded at one extra
  attempt in both TaskPilot runs.

Upstream unsupported `core-007`–`012`, `014`, `017`, `019` are recorded
separately and are **not** failures scored as zero-token attempts. Upstream
used `python_execute` for GitHub work, had no equivalent typed GitHub tools,
and did not receive TaskPilot's validation, routing, retry or deadline policy.
Its nine unstructured GitHub runs have no comparable typed-slot denominator.

## Frozen-score consistency ruling

`core-011` has a frozen `business_evidence` criterion requiring a routing
candidate and selected tool with score. A **true** full-tools baseline has
`routing_top_k=None`, zero routing events, and full-schema LLM calls by design.
The T067 human review called its pinned-source answer successful; T069
correctly changed the **formal frozen-criterion outcome** to `NOT VERIFIED`
(18/21 formal success). Requiring a routing score from full-tools is a
**benchmark construct mismatch**, not a missing product routing event.
The diagnostic business-evidence-only view would count this one answer
(19/21 for full-tools), but is **not a formal rescore** and must not replace
18/21 in T069–T071. No scorer implementation error or metric erratum was found;
the frozen dataset, reviews, original results and scripts remain unchanged.
If a future benchmark changes the criterion, it needs a new dataset/scoring
version and separate result series.

**Expected Outcome** is the frozen scenario action, including correct
rejection/containment for negative tasks. **Business Success** requires the
frozen evidence and an adequate final answer where one is requested.
**Reliability Correctness** means a tool failure is classified and bounded;
it does not prove a positive investigation succeeded. Thus TaskPilot's
`core-016/017/018/019` count as scenario success because safe handling is
the frozen requirement; their failed/unknown tool attempts are retained.
`terminate(success)` alone never supplies business evidence. The formal
Task Success Rate mixes positive, no-match and controlled fault outcomes;
it must not be described as an 18/21 positive investigation success rate.

T069 selection denominators remain original 2/15 autonomous gold steps,
full-tools 6/16 and K=3 7/16. Parallel extra tools make a gold step wrong;
candidate recall is a different metric. The nine zero-LLM TaskPilot controlled
samples have no autonomous selection denominator. T069 Task Success Rate is
original 4/12 **supported** (4/21 coverage with nine unsupported), full
18/21, K=3 18/21. T070 costs include failed attempts and all attempted
supported samples: original 12, TaskPilot 21 each, with the latter split into
12 natural model and nine controlled zero-LLM samples. T071 natural resolved
slot counts are original 3/0/0 on two comparable MCP steps, full 41/10/4,
K=3 40/12/5 (TP/FP/FN); the TaskPilot controlled counts are separately
14/0/0 each. Missing detection is 3/3 and scripted clarification validity
3/3 for each TaskPilot group. They do not measure natural missing-parameter
omission or real human wait time.

## K=3 negative cost pairs

The paired T070 evidence gives K=3 minus full-tools:

- `core-004`: **+27,994 provider tokens / +5,120.8 ms**, seven versus three
  provider attempts; seven embedding queries cost 59.3 ms.
- `core-011`: **+61,085 tokens / +6,846.1 ms**, eight versus four provider
  attempts; eight embedding queries cost 79.5 ms.
- `core-020`: **+14,064 tokens / +1,867.7 ms**, five versus four provider
  attempts; index plus five queries cost 70.1 ms. K=3 final citation was
  insufficient despite successful reads.

In these three runs the larger LLM/tool work time and extra turns dominate
measured embedding time. This is an observation, **not** a causal estimate
of routing. Nine of 21 paired tasks were slower with K=3; three of 12 natural
tasks used more tokens. Overall paired total was −6,172 provider tokens and
−2,923.1 ms, which does not erase the per-case regressions. Phase totals
can overlap and are not added to end-to-end duration.

## Reproduction manifest and independent checks

- Dataset: [holdout](../eval/datasets/core_benchmark_v1_holdout.json),
  [scoring contract](../eval/datasets/core_benchmark_v1_scoring.json), and
  [source pins](../eval/datasets/core_benchmark_v1_sources.json), version
  1.0.0. The raw/review SHA-256 checks are embedded in T069 output.
- Upstream: OpenManus `3309bf4e416fb1c74b008f3e86494439a31bad53`,
  Docker Python 3.12.14, original tools plus controlled stdio fixture,
  six steps/90-second Agent allowance. Original retest attempts are retained.
- TaskPilot: T067 recorded HEAD `021f23ce…`, T068 `d65e303a…`; both have
  identical `product_app_tree=c9c64263…` and 8 steps/180 seconds. Each
  GitHub profile used five read-only business tools plus `terminate`; the MCP
  profile used the same five fixture tools plus `terminate` in both groups.
  T067 filtering is absent; T068 Top-K=3.
  Actual full manifests carry full config, schema exposure and selected
  attempts. `deepseek-flash`, temperature 0, max_tokens 1024, thinking off;
  Provider revision **NOT AVAILABLE**. MCP/FastMCP SDK 1.5.0 over stdio.
  K=3 uses `sentence-transformers/paraphrase-MiniLM-L3-v2` revision
  `4ca70771034acceecb2e72475f72050fcdde4ddc`, vector version
  `tool-metadata-json-v1/cosine-v1/mean-pool-128-v1`.
- Offline metric commands from a clean source export, with the pinned
  `taskpilot-service:pr08c-nocache` image (`sha256:98326c8c…`), **no network**,
  no host `.venv`, and the tracked `config.example-daytona.toml` copied to
  `config.toml` in the temporary copy (its key field is empty):
  `python -m eval.t069_score_accuracy_success --output /out/t069_accuracy_success_v1.json`,
  `python -m eval.t070_cost_latency --output /out/t070_cost_latency_v1.json`,
  `python -m eval.t071_slot_metrics --check`, then compare the first two
  outputs as parsed JSON to the committed results. T069/T070 were identical;
  T071 check passed. This example config is only needed because the upstream
  config loader requires the unused Daytona field; no credential is supplied.
  These are independent dependency/runtime checks over the **same frozen raw
  evidence**, not new model runs.

One concrete offline replay from the repository root is:

```sh
replay_dir=$(mktemp -d)
mkdir -p "$replay_dir/src" "$replay_dir/out"
git archive HEAD | tar -x -C "$replay_dir/src"
cp "$replay_dir/src/config/config.example-daytona.toml" "$replay_dir/src/config/config.toml"
docker run --rm --network none -v "$replay_dir/src:/srv/taskpilot" -v "$replay_dir/out:/out" -w /srv/taskpilot --entrypoint python taskpilot-service:pr08c-nocache -m eval.t069_score_accuracy_success --output /out/t069.json
docker run --rm --network none -v "$replay_dir/src:/srv/taskpilot" -v "$replay_dir/out:/out" -w /srv/taskpilot --entrypoint python taskpilot-service:pr08c-nocache -m eval.t070_cost_latency --output /out/t070.json
docker run --rm --network none -v "$replay_dir/src:/srv/taskpilot" -w /srv/taskpilot --entrypoint python taskpilot-service:pr08c-nocache -m eval.t071_slot_metrics --check
```

Compare `/out/t069.json` and `/out/t070.json` as parsed JSON with the
committed T069/T070 results; whitespace is not a metric difference.
- The new [replay file](../eval/results/t072_replay_k3_v1.jsonl) is separate
  from T068 and uses current branch `e5f7f54…` with the same product app tree,
  dataset hash, K=3, model settings and budgets. The command was
  `.venv/bin/python -m eval.t068_k3_baseline --ids core-003,core-013,core-016,core-017,core-018,core-019 --output eval/results/t072_replay_k3_v1.jsonl`.
  Its preflight checked eight mutable issues and 11 pinned files with HTTP
  200; these checks do not remove external-service drift risk.

Replay `core-003` made two routed model calls and two real GitHub HTTP 200
requests; `core-013` made two routed model calls and one real `echo_read`
stdio dispatch. Both have valid Trace and complete cleanup. Controlled
`core-016` has one `isError` failure, `core-017` one `unknown` timeout,
`core-018` one real 404 with no retry, and `core-019` one controlled 503
followed by a real 200 under the same call. All four are explicitly **zero
LLM calls**, not autonomous model decisions. Each new row keeps its own
runtime `run_id`, call IDs, status, Observation and cleanup. The `core-017`
fixture server logged `ClosedResourceError` after the client closed the slow
stdio stream; the client recorded bounded cleanup, while remote completion
is still **NOT VERIFIED**. The reruns do not replace or re-score T066–T071.

An additional [clean-checkout container replay](../eval/results/t072_replay_clean_full_v1.jsonl)
used a fresh local Git clone at the same `e5f7f54…` HEAD, the pinned Docker
image, the existing config mounted read-only, and the `GITHUB_TOKEN` variable
passed without a value on the command line. It did not use the host `.venv`
or embedding cache. `core-003` made two actual GitHub 200 requests with two
model calls; `core-013` made one real stdio `echo_read` dispatch with two model
calls; `core-018` returned a real 404 once; `core-019` made a controlled 503
then real 200 under one call ID. All four have valid Trace and complete
cleanup. Its true full-tools manifest has no routing events, so this replay
is an integration reproduction, **not** a replacement for the frozen K=3
comparison. The first container attempt stopped before any sample because
the deployment image lacks the `git` binary required by the benchmark runner
to record HEAD. The successful second attempt temporarily mounted the host
Git executable into the clean container; that tooling dependency is a
reproduction constraint. No product or benchmark source was changed.

For a new clean full-tools replay on Linux, use a fresh local clone and the
same fixed image. The local config file is mounted read-only and the GitHub
variable is inherited by **name**, so no key value appears in the command:

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

The generated replay has new run IDs and must be reviewed separately from
the committed result. A non-Linux host, a different proxy, or an absent
host Git binary needs an equivalent local setup and remains untested here.

## Limits

Each original holdout system was run once; this audit cannot establish
statistical significance or a provider-wide improvement. Mutable GitHub
issue data, Code Search authorization/policy, proxy behavior, network timing,
and unknown Provider revision can change live reruns despite pinned code
refs. Original OpenManus has a different tool pool and execution budget, so
its results describe capability coverage rather than a same-tool-pool causal
comparison. Scripted clarification has no real human wait distribution.
Actual MCP remote completion after timeout, natural model omission followed
by clarification, and repeat-run variance remain **NOT VERIFIED**.
