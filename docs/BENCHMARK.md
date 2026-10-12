# TaskPilot Core MVP Benchmark

This is the public, reproducible report for the frozen `core-mvp-v1` revision.
TaskPilot is a single-agent, read-only GitHub investigation system built on
OpenManus. The 21-task holdout was run once per group. The results show
measured outcomes and limits, without a general performance claim.

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
  historical outputs was changed after the Core MVP freeze.

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

## Reproduce the published metrics

From a clean checkout with Python 3.12 and the locked dependencies in
`requirements-service.lock.txt`, run the commands below. They read committed
raw evidence and write T069/T070 recomputations outside the repository;
`--check` compares T071/T072 directly with committed results.

```bash
.venv/bin/python -m eval.t069_score_accuracy_success --output /tmp/taskpilot-t069-recomputed.json
.venv/bin/python -m eval.t070_cost_latency --output /tmp/taskpilot-t070-recomputed.json
.venv/bin/python -m eval.t071_slot_metrics --check
.venv/bin/python -m eval.t072_failure_audit --check
```

For exact clean-checkout setup, Docker replay, source drift checks, separate
real GitHub/MCP replays and known environment limits, use the
[T072 reproducibility record](T072_FAILURE_REPRODUCIBILITY.md). Real reruns
need the existing model configuration and network access, may incur provider
charges and must use new output paths. The evaluation runner records Git HEAD
and therefore needs `git`; the deployed service image omits it. Historical
GitHub/MCP/Docker integration evidence is indexed in [the public docs](README.md).

Provider/GitHub/MCP/Docker E2E evidence was recorded in its named earlier
tasks, with separate T072 replays. The publication of this report did not
perform new live E2E or change the frozen data, raw runs, or scoring rules.
