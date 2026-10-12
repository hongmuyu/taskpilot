# T069 — frozen Holdout accuracy and success

This is a single-run, 21-task evaluation of frozen Core Holdout v1.0.0. Recompute the exact per-sample result with:

```bash
.venv/bin/python -m eval.t069_score_accuracy_success --output /tmp/t069-recomputed.json
```

The scorer verifies the frozen dataset/scoring hashes, the three raw runs and adjudication files, unique sample IDs, selected upstream MCP retest pointers, and TaskPilot Trace/cleanup validity. [Machine-readable 63 sample judgments](../eval/results/t069_accuracy_success_v1.json) include each gold step, acceptable tool set, model-selected tool names/call IDs, original evidence pointer, source review outcome, business result and reason. No raw arguments or prompt contents are copied into this result. T066's initial MCP setup failures remain in the original record; the already selected successful retests are used for scoring.

## Frozen scoring application

- **Autonomous Tool Selection Accuracy:** Only `selection_eligible=true` steps on supported, attempted tasks with a real model turn enter the denominator. Gold step `index=N` aligns to actual assistant turn `N`; detours are not realigned after seeing outcomes. Every tool in a parallel selection must be in that step's multivalue `acceptable_tools`; one wrong extra tool makes the step incorrect. No selection is incorrect. Repeated selection of an acceptable tool still counts once for that step. Clarify/reject/no-match and nine TaskPilot controlled, zero-LLM tasks are excluded. This is the model's final selection, not routing candidate recall.
- **Task-level selection diagnostic:** A supported task with eligible steps counts once, and passes only when every eligible step passes. This supplements, but does not replace, the frozen step-level primary metric. Later unlabelled tool turns are retained in each sample's `unlabelled_followup_turns`, without inventing new gold labels or denominator entries.
- **Task Success Rate:** A supported attempted task counts once if all frozen business evidence is verified with no failure condition. Failure and NOT VERIFIED remain in the denominator as non-success. Unsupported is shown separately and excluded from the supported denominator. The 21-task coverage ratio is also shown so unsupported cases remain visible. `terminate(success)` is never sufficient business evidence.
- **Source of judgment:** Existing T066–T068 evidence reviews provide case-level adjudications. The scorer checks source hashes/IDs and Trace integrity. A discrepancy against a frozen criterion is identified below and corrected openly in the T069 result without changing those review files, the Holdout, rubric or raw runs.

| Group | Autonomous steps | Complete eligible tasks | Supported task success | All-21 coverage | Business outcomes |
| --- | ---: | ---: | ---: | ---: | --- |
| Original OpenManus | 2/15 (13.3%) | 2/10 | 4/12 (33.3%) | 4/21 | 4 success, 8 failure, 9 unsupported |
| TaskPilot Full-tools | 6/16 (37.5%) | 5/11 | 18/21 (85.7%) | 18/21 | 18 success, 3 NOT VERIFIED |
| TaskPilot K=3 | 7/16 (43.8%) | 7/11 | 18/21 (85.7%) | 18/21 | 18 success, 3 NOT VERIFIED |

Each cell below is `business outcome; correct/eligible autonomous steps`. A dash means no eligible autonomous selection step. All 21 frozen IDs are retained.

| Sample | Original OpenManus | Full-tools | K=3 |
| --- | --- | --- | --- |
| core-001 | success; 0/1 | success; 0/1 | success; 1/1 |
| core-002 | failure; 0/1 | success; 0/1 | success; 1/1 |
| core-003 | success; 0/1 | success; 1/1 | success; 1/1 |
| core-004 | failure; 0/1 | success; 1/1 | success; 1/1 |
| core-005 | failure; 0/2 | NOT VERIFIED; 1/2 | NOT VERIFIED; 0/2 |
| core-006 | failure; 0/2 | NOT VERIFIED; 0/2 | NOT VERIFIED; 0/2 |
| core-007 | unsupported; — | success; — | success; — |
| core-008 | unsupported; — | success; — | success; — |
| core-009 | unsupported; — | success; — | success; — |
| core-010 | unsupported; — | success; — | success; — |
| core-011 | unsupported; — | NOT VERIFIED; 1/1 | success; 1/1 |
| core-012 | unsupported; — | success; — | success; — |
| core-013 | success; 1/1 | success; 1/1 | success; 1/1 |
| core-014 | unsupported; — | success; — | success; — |
| core-015 | success; 1/1 | success; 1/1 | success; 1/1 |
| core-016 | failure; — | success; — | success; — |
| core-017 | unsupported; — | success; — | success; — |
| core-018 | failure; — | success; — | success; — |
| core-019 | unsupported; — | success; — | success; — |
| core-020 | failure; 0/2 | success; 0/2 | NOT VERIFIED; 0/2 |
| core-021 | failure; 0/3 | success; 0/3 | success; 0/3 |

## Manual audit against raw evidence

- **core-005/006:** Both TaskPilot groups fetched issue evidence. Full-tools fetched the pinned files, but the relevant code fell after file truncation and Code Search returned 403; K=3 lacked sufficient pinned code evidence as recorded in its review. Required code/citation evidence remains NOT VERIFIED. The original six-step runs ended without linked issue-to-code proof.
- **core-011:** K=3 has actual routing events, candidates/scores and selected tool results. Full-tools has zero routing events by design, although its pinned search/read results support the answer. T067's existing review marked success, but the frozen `trace` criterion expressly requires a routing candidate and score. T069 transparently records that discrepancy and scores Full-tools as NOT VERIFIED; neither prior review nor frozen labels were changed. This is an intrinsic cross-baseline comparability ambiguity for the routing-specific sample.
- **core-016/017:** Each TaskPilot run has one real stdio MCP server dispatch. The `isError` sample has failure Tool execution/Observation; the slow sample has unknown timeout, one attempt and no asserted remote cancellation. Original `core-016` had a false success declaration after `isError`; its review remains failure.
- **core-018/019:** Both TaskPilot 404 samples have one real downstream GitHub GET with HTTP 404 and failure Observation. Each 503 sample has one controlled transient 503 followed by one real 200, with two attempts under one tool call. No extra retry was scored as success.
- **core-020:** Full-tools' cited pinned issue and code support the answer. K=3 read the pinned file but the final code URL and comment citation are incomplete, so business success is NOT VERIFIED despite source access. Both groups' early parallel/detour selections miss the gold step sequence. The original run ended without the required evidence.
- **core-021:** Both TaskPilot groups reached the required linked evidence despite early parallel selections and detours, illustrating why task success and strict step selection are separate metrics.

The audit used the original HTTP status/paths, MCP dispatch audit, Trace statuses/attempt IDs, assistant tool choices and saved review reasons. The compact result file points to each original assistant turn; raw files retain the detailed Trace and response evidence.

## Interpretation limits

Only one run per Holdout task is available. No significance claim or overall performance uplift follows from these values. The original upstream has a different tool pool and six-step budget, while the TaskPilot groups share the same tool pool and eight-step budget; only the latter pair isolates candidate filtering. Exact public-name selection scoring does not credit upstream `python_execute` as a GitHub Tool even when it retrieved useful evidence. The frozen gold does not label every later assistant turn, so those calls are disclosed but cannot be scored as correct or wrong selections without post hoc labels. Nine controlled TaskPilot tasks establish boundary behavior, not autonomous model choice. The `core-011` routing-specific business criterion cannot be satisfied by a true Full-tools baseline; its discrepancy is retained, not normalized away. Token, latency and slot metrics are outside T069.
