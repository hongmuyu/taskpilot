# T065 — Core final benchmark dataset v1.0.0

This is a **frozen dataset and scoring contract**, not a benchmark result. No
original OpenManus, full-tools, or TaskPilot benchmark run has been performed
for this holdout. The T029 experiment and T030 evaluation results remain
routing-stage evidence only.

## Artifacts and split

- [Holdout](../eval/datasets/core_benchmark_v1_holdout.json): 21 new tasks,
  `taskpilot_core_final` v1.0.0, `independent_holdout`.
- [JSON Schema](../eval/datasets/core_benchmark_v1.schema.json): required
  fields, types, enumerations and one schema for all eight categories.
- [Source catalog](../eval/datasets/core_benchmark_v1_sources.json): captured
  2026-10-11 UTC, immutable GitHub commit and file blob IDs, mutable Issue
  title/body hash and `updated_at`, five Local Tool schema hashes, actual MCP
  stdio discovery for v1 and v2.
- [Scoring contract](../eval/datasets/core_benchmark_v1_scoring.json): frozen
  before runs; its SHA-256 is referenced by the holdout.
- [Manual label review](T065_BENCHMARK_REVIEW.json): one review decision per
  sample, performed by the implementing agent; it is not an independent
  external review.

The holdout records the exact SHA-256, version and role of T028's 28
`routing_v1_experiment` samples (used to choose K) and 12
`routing_v1_evaluation` samples (used in T030). Those 40 are **provenance**, not
new holdout samples. The new tasks use `encode/httpx`, `pydantic/pydantic`,
`Textualize/rich`, and a disposable read-only MCP server. T028 used other
repositories; exact task prompts and repository IDs do not overlap. Do not
retune K, rewrite labels, or select cases after seeing results. A change
requires a new dataset/scoring version and a recorded reason.

| Category | Samples |
| --- | ---: |
| Repository Understanding | 2 |
| Issue Investigation | 2 |
| Issue → Code | 2 |
| Missing/Invalid Parameters | 4 |
| Semantic Routing | 2 |
| Real MCP | 3 |
| Failure/Timeout/Retry | 4 |
| Multi-step Investigation | 2 |

Each sample states the task, confirmed repository/ref or MCP context, tool
profile, exact step, acceptable public tool names (possibly multiple),
critical slot values and their source, expected action/dispatch/attempt/status,
business evidence, failure conditions and unsupported conditions. A
`controlled_tool_call` or `controlled_fault` is marked explicitly. These are
validation and reliability probes, **not** evidence that the model naturally
omitted a parameter or an external service spontaneously failed. No-match and
clarification steps have `selection_eligible=false`.

## Source stability and tool profiles

GitHub file evidence must be read at the catalog's 40-character commit SHA;
the recorded blob SHA is checked before scoring. Issue endpoints are mutable
even when the repository ref is pinned. The captured `updated_at` and body
SHA-256 define the Issue snapshot. If either drifts, mark the affected sample
stale/`NOT VERIFIED` and report it; do not silently use the new body or change
the gold label. A future recapture requires a new version. Code search is a
discovery call against GitHub's live index, not a pinned content source; final
code evidence must come from a file read at the pinned commit. Its
authentication requirement is a preflight capability condition.

The Local profile is the current five GitHub read tools with their original
`BaseTool.parameters` schema hashes. The MCP profiles use the actual public
tool names, original names and `inputSchema` discovered through FastMCP 1.5.0
stdio, with the fixture source SHA-256 and `TASKPILOT_MCP_VARIANT` v1/v2.
The fixture is a real disposable server, not a mock response. The nominal
control tool `terminate` is outside the business tool pool; forced
clarification runs in the execution layer. Original OpenManus may not support
all profiles. Predeclare and report that limitation per baseline rather than
pretending the tools existed.

## Frozen scoring interpretation

The JSON scoring contract is authoritative. Candidate recall and final Tool
Selection Accuracy use supported, attempted `selection_eligible=true`
`expected_action=tool` **steps** as denominator. A selected tool is correct
if it matches **any** acceptable label. Wrong or absent selection counts as
incorrect. Clarification, rejection and no-match do not enter that
denominator; their safety and task outcomes are assessed separately.

Task Success Rate uses supported, attempted **tasks** as denominator,
including failures and `NOT VERIFIED` as non-success. All specified business
evidence for the task must be independently verified against actual Tool,
HTTP, MCP, Trace and cited output. `terminate(success)` is only an Agent
declaration. Negative tasks succeed only when their expected safe behavior
and evidence hold. A predeclared unsupported case is excluded from the
comparable denominator and counted separately; an unattempted case is also
reported, never silently dropped. Comparison between baselines must show both
the common supported subset and per-baseline coverage.

Slot precision/recall/F1 use critical `(name, normalized value)` pairs on
supported attempted steps. Explicit `missing` slots before reply and
`invalid` supplied values are excluded from the value-pair gold set; safe
clarification or rejection and zero dispatch are scored as execution
conditions. The slot's provenance is checked separately. Query gold labels
contain identifying terms; case-insensitive matching accepts them in any
order with additional informative terms, and actual relevance still requires
source evidence.
Undefined precision/recall is `NOT_APPLICABLE` with raw TP/FP/FN counts,
not an invented zero or one.

Only provider-reported input/output/total usage is measured usage. Missing
fields are `unknown`, and estimates are kept separate. Report each LLM call
once, including paid failed/retried calls when usage exists. End-to-end
latency runs from accepted request to terminal status **and cleanup**, using
a monotonic clock. It includes LLM, embedding, Tool, retry/backoff, failures,
timeouts, cancellation and human clarification wait. Component timers may
overlap, so they are not simply summed into an artificial end-to-end total.

## Pre-run checks and limitations

Run the isolated validation before any benchmark:

```bash
.venv/bin/python -m pytest -q tests/taskpilot/test_final_benchmark_dataset.py
```

This validates references/hashes, Local schemas and live stdio discovery;
it does **not** execute benchmark tasks. At benchmark time, additionally
record the TaskPilot revision, baseline revision, model/config, K, tool pool,
timeouts, fixed runner version, real GitHub/MCP responses and every failed
attempt. If sources or capabilities differ, apply the frozen status rules.
The controlled 503 case needs a runner capable of injecting only the first
response, then issuing a real pinned GitHub read. That runner and all three
formal baseline runs remain **NOT VERIFIED** until later tasks.
