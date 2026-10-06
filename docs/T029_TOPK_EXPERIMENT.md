# T029 — Top-K experiment on routing v1.1.0

This is a **single-step tool-selection experiment**, not a task-completion or
token/latency benchmark. The sealed evaluation split was not opened or used to
choose K. Raw, per-run evidence is in
[`eval/results/t029_experiment_v1_1_0.jsonl`](../eval/results/t029_experiment_v1_1_0.jsonl).

## Fixed setup

- Dataset: `routing_v1_experiment.json`, version `1.1.0`, SHA-256
  `38fa5d0f48c7e6159adff464c20f417d43b928fbad85028fbe3b0f1ff1cf87eb`;
  28 samples, with the original task and synthetic Observation kept verbatim
  across groups. Synthetic Observations are fixtures, not live GitHub evidence.
- Groups: K=1, K=3, K=5, and full tools. Each sample ran once per group (112
  real-model calls). Group order rotated by sample. The full group set
  `routing_top_k=None` and `routing_retriever=None`: it sent all five business
  schemas in the original registry order plus `terminate`, with no routing
  event. It did not emulate full tools with K=5.
- Model: `deepseek-flash` through the configured OpenAI-compatible endpoint at
  `api.deepseek.com`; temperature `0`, maximum output tokens `1024`, tool choice
  `auto`, request timeout `45s`. The provider did not expose a model revision
  through this call path. All groups used the same model configuration.
- Embedding: `sentence-transformers/paraphrase-MiniLM-L3-v2`, pinned revision
  `4ca70771034acceecb2e72475f72050fcdde4ddc`, vector version
  `mean-pool-128-v1`.
- Tool pool: the current five read-only GitHub Local Tools
  (`github_repository_info`, `github_issue_search`, `github_issue_detail`,
  `github_code_search`, `github_read_file`) and control tool `terminate`.
  The runner used the real Tool objects and Agent `think()` path. It did not
  call `act()` or a GitHub HTTP endpoint: each run had one decision step and
  zero Tool dispatches. This isolates routing from tool execution.
- The JSONL manifest records the runner hash, base HEAD, model and embedding
  versions, dataset hash, pool, controls, normalization, and budget. Every run
  records message hashes, sent schemas, final model-selected tool names and
  call IDs, public response text, the raw T027 routing event (including query,
  candidates, scores, run ID and index version), errors, and dispatch count.
  Model arguments and credentials are not recorded.

Run command:

```bash
.venv/bin/python -m eval.t029_topk_experiment --output eval/results/t029_experiment_v1_1_0.jsonl
```

The output is append-only JSONL; `--resume` may continue an interrupted run
only with the same dataset and model configuration. The evaluation file is not
read by this runner.

## Paired-run checks and selection observations

All 28 samples have four runs. Per sample, the original input-message hash and
full Tool Registry matched across groups. All 112 model calls completed with
no transport/provider exception, all 112 kept `terminate` available, and all
112 had zero dispatch. The 28 full runs bypassed retrieval; all 84 routed runs
recorded one routing event with five scored business candidates. A scan of the
raw artifact found no token-shaped values.

The 23 samples labeled `expected_action=tool` give these **descriptive counts**.
“First acceptable” means the model's first selected tool is in that sample's
multi-label `acceptable_tools`. “Any acceptable” accepts any selected parallel
call. “Only acceptable” means no selected tool falls outside the labels; it
does not penalize repeated calls to the same tool.

| Group | First acceptable | Any acceptable | Only acceptable | Samples with extra tool |
| --- | ---: | ---: | ---: | ---: |
| K=1 | 14/23 | 14/23 | 14/23 | 9/23 |
| K=3 | 21/23 | 21/23 | 19/23 | 4/23 |
| K=5 | 19/23 | 23/23 | 13/23 | 10/23 |
| Full | 17/23 | 23/23 | 8/23 | 15/23 |

First-tool misses, including every tool-label failure in this experiment:

- K=1: `exp-007`, `exp-013`, `exp-014`, `exp-015`, `exp-016`, `exp-018`,
  `exp-019`, `exp-024`, `exp-028` (9; none of the parallel calls recovered a
  labeled tool).
- K=3: `exp-019`, `exp-028` (2; no parallel recovery).
- K=5: `exp-015`, `exp-017`, `exp-020`, `exp-028` (4; a later parallel call
  included a labeled tool in each).
- Full: `exp-010`, `exp-011`, `exp-015`, `exp-017`, `exp-019`, `exp-024` (6; a
  later parallel call included a labeled tool in each).

The no-match sample `exp-026` selected `terminate` in all four groups. The four
`expected_action=clarify` samples did **not** demonstrate a completed
clarification in this selection-only harness:

- `exp-021` (missing issue): K=1/K=3 selected `terminate` with a public request
  for an issue identifier; K=5/full selected repository metadata and issue
  search.
- `exp-022` (missing path): K=1/K=3 selected `github_read_file` and K=1's
  public text guessed README; K=5/full selected `github_repository_info`.
- `exp-023` (missing repository): every group selected
  `github_repository_info`. The no-repository fixture uses a non-dispatched
  placeholder Tool context and a prompt stating that no repository is confirmed.
- `exp-027` (missing issue): K=1 selected `github_issue_detail`; K=3 selected
  `github_issue_search` twice; K=5/full selected repository metadata and issue
  search.

These are selection/clarification failures, not API errors. The T016–T019
execution-layer clarification path was not invoked by this one-step experiment.
Some tool samples also generated repeated parallel calls; the raw record keeps
every selected name and call ID rather than collapsing them into one hit.

## Recommendation and limits

**Recommend K=3 for the next controlled comparison.** It had the strongest
first-call alignment with the current-step labels (21/23), while K=5 and full
more often mixed additional tools into parallel calls. K=1 excluded useful
alternatives on nine tool samples. This is a choice from the experiment split,
not a claim of production superiority. The evaluation split remains sealed for
T030; no formal accuracy, token, or latency result is asserted here.

NOT VERIFIED: model revision, repeated-trial stability, actual task completion
or GitHub HTTP outcomes in this selection-only experiment, and successful
clarification for missing-parameter samples. The no-repository fixture does
not establish production execution behavior without a confirmed repository.
