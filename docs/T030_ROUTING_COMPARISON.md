# T030 — routing v1.1.0 evaluation comparison

This is a **routing-stage, single-step comparison**, not the Phase 8 task-level
benchmark. T029 chose K=3 using the separate 28-sample experiment split. T030
used all 12 evaluation samples once per group and did not retune K. Raw paired
runs are in [`eval/results/t030_evaluation_v1_1_0.jsonl`](../eval/results/t030_evaluation_v1_1_0.jsonl).

## Fixed setup and evidence

- Evaluation version `1.1.0`, SHA-256
  `31bd9377edd126d3a4ce1910c477b8b57792e508e41efc33db85f697d87a81a6`;
  eight `tool`, three `clarify`, one `no_match` samples. The original task and
  synthetic Observation were identical within each K=3/full pair. These
  Observations are fixtures, not live GitHub responses.
- Real model `deepseek-flash` at `api.deepseek.com`, temperature `0`, maximum
  output tokens `1024`, tool choice `auto`, request timeout `45s`. The provider
  returned `deepseek-flash` as the response model, but no distinct server-side
  revision. The same five read-only GitHub Local Tools and `terminate` control
  tool were in the registry for both groups. K=3 exposed three business tools
  plus `terminate`; full tools exposed all five business tools plus `terminate`.
- K=3 used the pinned local embedding model
  `sentence-transformers/paraphrase-MiniLM-L3-v2`, revision
  `4ca70771034acceecb2e72475f72050fcdde4ddc`, vector version
  `mean-pool-128-v1`. The full group set both `routing_top_k` and
  `routing_retriever` to `None`: 12/12 full runs sent the complete registry and
  produced no routing event. K=3 produced 12/12 raw T027 events with candidates,
  scores, selected calls and index versions. Each paired run had the same
  initial-message hash and Tool Registry. Group order alternated by sample.
- Each run called the actual Agent `think()` and real model exactly once, with
  one decision step and **zero Tool dispatches**. This isolates selection and
  provider cost from GitHub execution. The manifest records the runner hash,
  base HEAD, dataset hash, model configuration, embedding version and budget;
  run rows retain candidate/sent tools, selected names and original call IDs,
  routing events, provider-reported usage, embedding calls, errors and elapsed
  time. Credentials and tool arguments are not recorded.
- The manifest's runner SHA-256 identifies the exact script used for the live
  runs. Black formatting happened afterward; the executed Git blob was
  recovered and its Python AST matched the committed formatted script.

Reproduction command (requires the project's existing provider configuration):

```bash
PYTHONPATH=. .venv/bin/python eval/t030_routing_comparison.py --output eval/results/t030_evaluation_v1_1_0.jsonl
```

## Results and denominators

`candidate recall` is the number of `expected_action=tool` samples with at
least one acceptable tool in the sent schemas, divided by **8 tool samples**.
`Tool Selection Accuracy` is the number of those samples where the model
selected at least one tool and **every** selected tool is acceptable, divided
by the same **8**. Multi-label acceptable sets are respected; a repeated call
to the same acceptable tool does not reduce this particular score. The three
`clarify` and one `no_match` samples are reported separately and do not enter
either denominator.

| Measure | K=3 | True full tools |
| --- | ---: | ---: |
| Candidate recall | 8/8 | 8/8 (all tools exposed) |
| Final Tool Selection Accuracy | 8/8 | 7/8 |
| No-match selected only `terminate` | 1/1 | 0/1 |
| Provider attempts / errors / retries | 12 / 0 / 0 | 12 / 0 / 0 |
| Provider input tokens | 8,392 (12/12 responses reported) | 9,922 (12/12) |
| Provider output tokens | 2,950 (12/12) | 2,137 (12/12) |
| Provider total tokens | 11,342 (12/12) | 12,059 (12/12) |
| Embedding calls / input texts | 13 / 17 | 0 / 0 |
| Embedding elapsed | 3,214.8 ms | 0 ms |
| End-to-end decision latency, sum | 23,823.0 ms / 12 | 16,264.7 ms / 12 |
| End-to-end decision latency, mean | 1,985.2 ms / 12 | 1,355.4 ms / 12 |
| End-to-end decision latency, median | 1,083.0 ms / 12 | 895.2 ms / 12 |

Provider usage is taken from each successful provider response, not estimated
from text. All three usage fields were available in all 24 responses. Failed
provider attempts and retries were zero, so observed retry time and reported
retry tokens were zero. Embedding elapsed is separately measured and included
in K=3's end-to-end decision latency; the first K=3 run built the index and
made two embedding calls, and subsequent runs reused it. End-to-end latency
includes retrieval and the model decision, but not Tool execution, because
this harness has a zero-dispatch budget.

Against full tools, K=3 used **1,530 fewer input** and **717 fewer total**
provider tokens, but **813 more output** tokens. Its measured latency was
**7,558.3 ms greater in aggregate** and **629.8 ms greater per sample on
average**. It was slower in 7/12 pairs (`eva-001`, `eva-002`, `eva-004`,
`eva-005`, `eva-006`, `eva-008`, `eva-012`) and used more total tokens in 3/12
(`eva-006`, `eva-008`, `eva-012`). No latency improvement is claimed.

## Failures and exceptional selections

- `eva-004`: full tools selected acceptable `github_code_search` together with
  extra `github_repository_info`; K=3 selected only the acceptable tool. This
  is the full group's sole tool-selection error under the stated metric.
- `eva-008` (`no_match`): K=3 selected `terminate`; full tools selected
  `github_repository_info`. The no-match result is separate from the 8/8 score.
- `eva-012` (`clarify`): both groups selected business tools instead of only
  requesting missing information. K=3 selected `github_issue_search` and
  `github_read_file`; full selected `github_repository_info` and
  `github_issue_search`. The model decision is a clarification failure in this
  selection-only harness. No business Tool was dispatched here; the existing
  execution-layer validation/clarification was not exercised by this sample.
- `eva-006` and `eva-011` (`clarify`) selected no tools in either group and
  generated public text. The raw responses are retained; the one-step harness
  does not establish a completed clarification/merge/execution loop.
- `eva-002`: both groups selected `github_issue_search` twice in parallel.
  The metric treats each as acceptable but preserves both call IDs. There were
  no provider/transport errors, timeouts or missing usage fields in 24 runs.

## Real GitHub investigation regression

[`eval/results/t030_investigation_regression_verified.jsonl`](../eval/results/t030_investigation_regression_verified.jsonl)
records three independent runs of the real Repository Investigation Agent with
K=3 routing, the real model, and read-only GitHub REST. The confirmed context
was `FoundationAgents/OpenManus` at the default branch, sourced from explicit
user input. An inherited `GITHUB_TOKEN` enabled authenticated Code Search.

| T021 chain | Real GitHub HTTP | Business tool sequence | Outcome |
| --- | --- | --- | --- |
| Repository Understanding | 2 × 200 | repository info, README read | Answer contained repository evidence/URL; successful `terminate` |
| Issue Investigation | 4 × 200 | issue detail, source read, issue search | Answer included issue #1427 and GitHub URL; successful `terminate` |
| Issue → Code | 10 × 200 | issue detail, code search, source reads | Answer included issue #1427, source path and GitHub URL; successful `terminate` |

All three returned nonempty answers without a run exception or step limit.
The raw record retains HTTP method/path/status, selected Tool names and IDs,
routing events and a credential-redacted answer excerpt. The original
[`eval/results/t030_investigation_regression.jsonl`](../eval/results/t030_investigation_regression.jsonl)
is also retained: its `finished=False` field was a **measurement bug**, because
`BaseAgent.run()` restores its previous state after the run. The verified
rerun derives completion from the successful `terminate` Tool observation
matched to its original `tool_call_id`; both runs had successful real GitHub
requests. No product/runtime code was changed to fix the measurement bug.

## Credential audit and limits

The user confirmed manual rotation, and a real provider authentication smoke
succeeded. Exact-value scans found neither active model nor GitHub credentials
in tracked files, T029/T030 results or project logs. The active local config
remains ignored by Git. No credential values are included in this report or
its run artifacts.

NOT VERIFIED: provider's distinct model revision; repeated-run stability;
full-task token/latency and final Phase 8 benchmark; successful
clarification/merge/execution for the three evaluation `clarify` samples;
real MCP parameter E2E (T035/T038); and default thinking-mode multi-turn
compatibility. The separate three-chain live regression confirms repository
investigation behavior with routing, not every PR-03 clarification scenario.
