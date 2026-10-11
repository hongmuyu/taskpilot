# T067 — same-tool-pool full-tools baseline

This is the TaskPilot **full-tools execution group** over the frozen
[T065 v1.0.0 holdout](../eval/datasets/core_benchmark_v1_holdout.json). It is
separate from the [original OpenManus T066 group](T066_ORIGINAL_OPENMANUS.md).
It records raw runs and per-case evidence judgments; T069–T071 comparative
metrics have not been computed. The holdout, source catalog, and
[scoring contract](../eval/datasets/core_benchmark_v1_scoring.json) are
unchanged.

## Fixed run configuration

- TaskPilot application tree: `021f23ce7db4e24fb65c16fd82716c2c9467631f:app`.
  Each of the 21 samples used a fresh Agent, LLM client, task Memory,
  Repository Context, ToolCollection and Trace. The runner validates the
  frozen tool names and schemas before execution.
- Model: real `deepseek-flash` at `api.deepseek.com`, temperature `0`,
  `max_tokens=1024`, thinking disabled. Provider revision is **NOT AVAILABLE**.
  The execution environment was Linux, Python 3.12.13, MCP SDK 1.5.0 and
  HTTPX 0.28.1. Credential presence was checked without recording values.
- Budget: 8 Agent steps, 180-second task deadline, 45-second model request
  timeout, 20-second GitHub HTTP timeout, 5-second MCP connect and Agent
  cleanup, and 2-second MCP cleanup. The frozen `core-017` fault overrides
  its one MCP tool deadline to 50 ms.
- GitHub profile: five current read-only Local Tools plus `terminate`, with
  the frozen repository/ref as confirmed context. MCP v1/v2 profiles: their
  five discovered stdio read tools plus `terminate`; the fixture source and
  discovered `inputSchema` match T065. There is no write tool. The same
  profile, model settings, task/Observation inputs, control tool and budget
  are the fixed comparison contract for T068.
- Semantic routing is disabled via `routing_top_k=None` and
  `routing_retriever=None`. Neither embedding index nor retriever is built.
  The audit intercepts **every actual `LLM.ask_tool` call** and compares the
  exact serialized schema list to the complete current ToolCollection. All
  **44/44 actual LLM calls** sent six schemas, including `terminate`; zero
  routing events and zero embedding events were recorded. Nine controlled
  boundary cases intentionally made no LLM call and are labelled
  `NO_LLM_TOOL_CALL`, not counted in the 44-call claim.
- Frozen inputs were rechecked immediately before the run: **8/8 Issue**
  body/update markers and **11/11 pinned file** blob hashes matched the T065
  source catalog. The source check's HTTP calls are separate from the sample
  execution HTTP audit. Each sample's request, Trace, provider-reported usage,
  elapsed time and resource cleanup are retained in the raw file.

## Actual execution and review

All **21/21 holdout samples** were attempted, with no preflight unsupported
sample. The [per-sample review](../eval/results/t067_full_tools_review.json)
finds **19 success, 2 NOT VERIFIED** against the frozen behavior and business
evidence criteria. This is a disposition count, not a formal Task Success Rate
or a claim of improvement over another group. All 21 Traces validated, all
21 cleanups completed, and all samples returned within the recorded budget.

The 12 natural tasks used the real provider; the nine frozen controlled
parameter/fault cases injected a labelled call at `ToolCallAgent`'s execution
boundary. They did **not** test natural model omission or a live human wait.
For `core-007`, `008` and `014`, the frozen reply was submitted through the
existing clarification merge/revalidation path: zero dispatch before reply,
one original Tool dispatch afterward, original call ID preserved. `core-009`
and `010` produced validation errors and zero GitHub requests.

Real MCP stdio v1/v2 execution is recorded for `core-013`–`017`: the two
natural echo requests and one clarified echo returned the expected values.
The fixture's `isError=true` in `core-016` became a failure Observation after
one dispatch. `core-017` ran the real slow server behind a 50 ms client
deadline and produced `unknown`/timeout with one server dispatch, no retry and
bounded cleanup; remote completion was not inferred. The real pinned GitHub
404 in `core-018` was a permanent one-attempt failure. In `core-019`, the first
HTTP 503 was **controlled**, and the second attempt was a genuine GitHub 200
read under the same Tool call. Across sample execution, the HTTP audit records
56 HTTP 200, one 404, one controlled 503, four 403 and one 422; the 403/422
responses are retained, not treated as successes.

Two Issue→Code tasks are **NOT VERIFIED**: `core-005` reached the 8-step
budget after the read tool truncated `rich/cells.py` before the relevant
`chop_cells` loop; `core-006` read `pydantic/_internal/_validators.py` but
truncation hid `decimal_places_validator`. GitHub Code Search also returned
403 during these investigations. Both fetched their issues and cited pinned
files, but the required implementation evidence was absent. `core-006` even
contains an Agent success declaration; the review does not count that as
business success. `core-011` has a valid issue/code result; its routing score
criterion is not applicable in the full-tools group because no candidate
ranking is performed. `core-012` safely refused a shell/environment request
with no business dispatch; shell execution itself is unsupported by the
read-only profile. `core-020` obtained its required issue/code evidence despite
one extra failed tool call, which remains in the raw Trace.

An initial MCP preflight sequence exposed a **runner-only** issue: a prior
controlled case closed the shared OpenManus LLM singleton client, so natural
`core-013` failed before reaching the provider. The first run, isolated
successful retest, reproduced failure and fixed sequence are all preserved.
The runner now creates a distinct LLM client per sample with identical model
settings; the final 21-case run has no setup or LLM-client reuse error. No
product runtime or frozen dataset was changed to address this harness issue.

## Evidence and reproduction

- [Final 21-case raw run](../eval/results/t067_full_tools_v1.jsonl)
- [Per-sample evidence review](../eval/results/t067_full_tools_review.json)
- [Initial preflight](../eval/results/t067_full_tools_preflight.jsonl),
  [isolated MCP retest](../eval/results/t067_full_tools_preflight_retry_1.jsonl),
  [reproduced failure](../eval/results/t067_full_tools_preflight_retry_2.jsonl),
  [fixed preflight](../eval/results/t067_full_tools_preflight_fixed.jsonl)
- [Runner](../eval/t067_full_tool_baseline.py) and
  [integrity tests](../tests/taskpilot/test_t067_full_tool_baseline.py)

From a configured TaskPilot checkout with the project's locked Python
environment, a valid provider key and optional GitHub read token in the
existing secret mechanism, run:

```bash
.venv/bin/python -m eval.t067_full_tool_baseline \
  --output /path/to/new/t067_full_tools.jsonl
```

The runner creates the output exclusively. A new run uses a new path. It
rechecks the frozen dataset/scoring/source hashes, MCP fixture and live source
snapshot, and records failures instead of omitting rows. Raw Trace IDs and
tool arguments are sanitized before saving. Formal cross-group accuracy,
slot, token and latency aggregation is deliberately left to T069–T071.

## Remaining limits

- Provider model revision and deterministic replay of an external provider:
  **NOT VERIFIED**. Temperature and request settings are fixed, but remote
  model behavior may still change.
- `core-005`/`006` implementation evidence: **NOT VERIFIED** in this run due
  to read truncation and Code Search 403. These rows remain non-success in
  later denominators.
- Natural model omission and interactive human response: **NOT VERIFIED** by
  the controlled clarification cases. The genuine merge/revalidation and
  HTTP/MCP execution paths were exercised.
- A real spontaneous 503 was not observed. `core-019` explicitly distinguishes
  the controlled first response from the real second GitHub read.
- Candidate scores are not available in this routing-disabled group; this is
  expected for a true full-tools baseline, not evidence of a retrieval run.
