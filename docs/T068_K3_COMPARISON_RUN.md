# T068 — TaskPilot K=3 comparison run

> Historical raw-run record. T069–T071 scoring was completed later; see the
> [final Benchmark](BENCHMARK.md) for current aggregate results and limits.

This is the **raw K=3 execution group** over the unchanged
[T065 v1.0.0 holdout](../eval/datasets/core_benchmark_v1_holdout.json). It is
separate from the [original OpenManus T066 baseline](T066_ORIGINAL_OPENMANUS.md)
and the [same-tool-pool T067 full-tools group](T067_FULL_TOOL_BASELINE.md).
The [frozen scoring contract](../eval/datasets/core_benchmark_v1_scoring.json)
was not changed. T069–T071 formal Accuracy, Task Success Rate, slot, token and
latency comparisons have **not** been calculated.

## Pinned execution contract

- Product application tree: `c9c64263f7070141bab3dfb9d7df6a8aa7871bb3`,
  identical to T067. The T068 checkout HEAD was
  `d65e303a10375802dcb1b0cfa11bab525177a3a2`; the difference from T067
  HEAD consists of the already committed T067 evaluator, tests and evidence,
  not changes to `app/`.
- Real provider: `deepseek-flash` at `api.deepseek.com`, temperature `0`,
  `max_tokens=1024`, thinking disabled. Provider revision: **NOT AVAILABLE**.
  Python 3.12.13, MCP SDK 1.5.0 and HTTPX 0.28.1 match T067's environment.
- Profiles: the same five GitHub read-only Local Tools or the same five real
  stdio MCP fixture tools for v1/v2, plus `terminate`. The exact current Tool
  names and schema hashes match T067 for **21/21 cases**. Each case has its
  own Agent, LLM client, Memory, Repository Context, ToolCollection and Trace.
- Budget and execution policy: 8 steps, 180-second Agent deadline, 45-second
  model request timeout, 20-second GitHub HTTP timeout, 5-second MCP connect
  and Agent cleanup, 2-second MCP cleanup. `core-017` alone has the frozen
  50 ms MCP tool deadline. The same task text/hash, confirmed repo/ref,
  Observation normalization, controlled reply, 503 injection and validation/
  dispatch boundary as T067 are checked before and after the run.
- **Only candidate filtering changes:** `routing_top_k=3` and the existing
  `SemanticToolRetriever`/`InMemoryToolIndex` are enabled for actual model
  turns. The pinned local embedding backend is
  `sentence-transformers/paraphrase-MiniLM-L3-v2` at revision
  `4ca70771034acceecb2e72475f72050fcdde4ddc`, vector version
  `tool-metadata-json-v1/cosine-v1/mean-pool-128-v1`. One in-memory index and
  embedding backend are shared sequentially across isolated case Agents; the
  first natural case includes model load/index build cost. Tool-pool switches
  invalidate and rebuild the index. Every routing event records its exact
  metadata fingerprint and model/vector version.
- Frozen source snapshot was checked before this run: **8/8 Issue**
  body/update markers and **11/11 pinned file** blob hashes matched T065.
  Source-check HTTP traffic is separate from case execution traffic.

## Actual 21-case run

All **21/21 holdout cases** were attempted: 12 natural tasks through the real
provider and nine explicitly controlled parameter/fault cases through the
actual TaskPilot execution boundary. There were no preflight unsupported
cases. The [per-sample evidence review](../eval/results/t068_k3_review.json)
records **18 success and 3 NOT VERIFIED** against the frozen case criteria.
Expected safe rejection, MCP failure, timeout and GitHub 404 behavior count
as success only for their corresponding safety cases. This is a disposition
count, not a formal benchmark rate or a claim of K=3 improvement.

All **48/48 actual LLM Tool Calling requests** sent three business schemas
and `terminate`, and each has a corresponding routing event with candidate
identities, similarity scores, index version, selected Tool names/call IDs
and K business/total. The complete ToolCollection remained available to
validation and dispatch. There were 48 query embeddings and four actual
index rebuilds as the sequential run moved between GitHub, MCP v1, MCP v2
and GitHub profiles. Provider usage, every embedding phase and elapsed time,
Trace, raw failures and cleanup are preserved per case. All 21 Traces
validated; all 21 resource cleanups completed. The nine controlled cases
made **no LLM request or semantic retrieval** and are labelled
`NO_LLM_TOOL_CALL`; their correct execution is not claimed as autonomous
model selection.

Real GitHub REST, real FastMCP stdio and boundary behavior are visible in the
[raw run](../eval/results/t068_k3_v1.jsonl):

- `core-007`, `008`, `014`: zero Tool/server dispatch before the frozen
  clarification reply; the existing merge/revalidation path kept the original
  `tool_call_id` and then dispatched the original call exactly once.
  `core-009/010` rejected malformed JSON/boolean `issue_number` with zero
  GitHub requests.
- `core-013/015`: the model chose a real MCP echo tool from K=3 candidates;
  each produced one real stdio server dispatch and the expected echo value.
  `core-016`: actual MCP `isError=true` became failure without retry.
  `core-017`: the slow server was dispatched once; client timeout was recorded
  as `unknown`, with bounded cleanup and no claimed remote cancellation.
- `core-018`: real pinned GitHub 404, one attempt, permanent failure.
  `core-019`: first 503 was **controlled**, second attempt was a genuine
  GitHub 200 under the same Tool call. The case HTTP audit contains 49 200,
  seven 403, one real 404 and one controlled 503; the 403s remain failures.
- `core-012`: low-similarity business candidates were exposed, but the model
  chose `terminate` and made no business dispatch or environment disclosure.
  The existing Top-K implementation has no threshold that rejects every
  positive-score weak match; this run does not claim such a feature.

The retained non-success rows are `core-005`, `core-006` and `core-020`.
`core-005` exhausted eight steps: `rich/cells.py` was truncated before the
relevant loop and Code Search returned 403. `core-006` fetched its Issue but
three Code Search calls returned 403 and no pinned validator file was read.
`core-020` read the real Issue and pinned `rich/ansi.py`; its final answer
explained the parsing path but omitted a full pinned code URL and referred to
Issue comments without direct comment citations, so the frozen answer-citation
criterion remains NOT VERIFIED. `core-011` reached the step cap and had one
failed `terminate` attempt before a second terminate; its scored routing
events and cited pinned code/test evidence are retained, so the case evidence
review still marks it successful. Extra Tool calls, 403s, and failed attempts
were not removed to favor K=3.

## Three-group integrity and reproduction

- [T066 original OpenManus raw 21 rows](../eval/results/t066_original_openmanus_v1.jsonl)
  and [its review](../eval/results/t066_original_openmanus_review.json):
  12 supported attempts, nine unsupported, different upstream Tool pool.
- [T067 full-tools raw manifest + 21 rows](../eval/results/t067_full_tools_v1.jsonl)
  and [its review](../eval/results/t067_full_tools_review.json):
  same TaskPilot application tree/profile/budget, semantic filter disabled.
- [T068 K=3 raw manifest + 21 rows](../eval/results/t068_k3_v1.jsonl),
  [five-case real preflight](../eval/results/t068_k3_preflight.jsonl),
  [per-sample review](../eval/results/t068_k3_review.json),
  [runner](../eval/t068_k3_baseline.py), and
  [integrity tests](../tests/taskpilot/test_t068_k3_baseline.py).

All three groups contain the same 21 frozen sample IDs. T067/T068 use the
same application tree, environment, model/sampling settings, Tool profiles,
task hashes, contexts, controlled injection inputs, normalization and
budgets; only the semantic candidate filter and its embedding/index cost
differ. T066 is an original-upstream product baseline with a different Tool
pool and budget and must not be interpreted as a Top-K causal comparison.
The T068 manifest pins its runner hash, T067 manifest hash, source/fixture
hashes, model/vector version and observed environment. Every case row retains
actual tool calls, routing events, provider usage, embedding attempts,
elapsed time, Trace and cleanup. The raw JSONL was written incrementally;
failed and NOT VERIFIED cases were not discarded.

To run an independent repetition from a configured checkout, with valid
provider credentials supplied only through the existing secret mechanism:

```bash
.venv/bin/python -m eval.t068_k3_baseline \
  --output /path/to/new/t068_k3.jsonl
```

The runner creates output exclusively, validates the unchanged T065 and
T067 contracts, rechecks live GitHub source markers, and rejects a changed
Tool schema, model setting, application tree, budget or execution environment
before recording cases. Use a new output path for every repetition.

## Remaining limits

- Only one complete 21-case K=3 run was performed, plus a five-case preflight.
  Statistical repeatability and the provider's unreported model revision are
  **NOT VERIFIED**. GitHub Code Search rate limits changed between T067 and
  T068; raw HTTP statuses show the resulting environmental difference.
- `core-005`, `006` and `020` are **NOT VERIFIED** as described above. Do not
  convert Agent finish declarations into verified business success.
- Natural model omission and a live human clarification wait remain
  **NOT VERIFIED** by the controlled boundary cases. The real Tool execution
  and clarification merge/revalidation paths were exercised.
- The T068 index is in memory and shared only within this sequential run;
  other scheduling/order patterns and embedding cold-download behavior were
  not benchmarked here. No K choice was retuned after viewing Holdout results.
- T066 differs in original Tool capability and budget. Common-subset scoring
  and formal Accuracy/Success/Token/Latency comparison are reserved for
  T069–T071, not inferred from these raw disposition counts.
