# T071 — Parameter / Slot metrics

This report recomputes only parameter metrics from the frozen Core holdout
v1.0.0 and the committed T066–T068 runs. It does not rerun a model or change
T069/T070. Reproduce with:

```bash
.venv/bin/python -m eval.t071_slot_metrics --check
.venv/bin/python -m pytest -q tests/taskpilot/test_t071_slot_metrics.py
```

The [scorer](../eval/t071_slot_metrics.py) checks the frozen dataset and
scoring hashes, the three 21-row source groups, selected upstream MCP retest
pointers, and unique sample IDs. The [result](../eval/results/t071_slot_metrics_v1.json)
contains every gold step, its critical slot value and declared source, the
observed critical arguments (including rejected input) and channel, TP/FP/FN, and a raw file/sample/turn
reference. Selected source file hashes are included. An Assistant finish status
does not enter these metrics.

## Scoring contract

- The unit is a distinct critical `(field, normalized value)` pair on a
  supported, typed, attempted `tool` step. A matching pair is TP; an emitted
  pair absent from the frozen gold is FP; an unmatched gold pair is FN. A wrong
  value for a gold field is both FP and FN. Parallel calls may provide a gold
  field once; two queries satisfying the frozen identifier-term rule do not
  create a false FP merely because their extra terms differ.
- `repository` is compared case-insensitively; pinned `ref`, repository path
  and MCP text are exact; an integer cannot be a Python `bool`. A search query
  must contain all frozen identifier terms, in any order, and may contain
  informative extra terms. No schema validity or Tool success substitutes for
  this comparison.
- Precision is TP/(TP+FP), recall TP/(TP+FN), and F1 is their harmonic mean.
  A zero denominator produces `null` (`NOT APPLICABLE`), with raw counts kept.
  `clarify`, `reject`, and `no_match` steps do not enter value-pair denominators;
  missing values become eligible after the frozen scripted reply. The
  zero-gold `core-016` Tool step has no value-pair denominator.
- `natural_resolved` includes explicit model arguments plus `owner/repo/ref`
  already bound to the Tool from confirmed Repository Context. This measures
  parameters available at the execution boundary, including context reuse.
  `natural_model_explicit` restricts its gold denominator to `task` and
  `observation` fields and counts only model-emitted arguments. These two
  results must not be conflated. Confirmed Context values are **available to**
  the Tool; this is not a claim that every GitHub endpoint uses `ref` in HTTP.
- `controlled_resolved` counts the nine zero-LLM TaskPilot cases separately.
  In particular, `core-007/008/014` use frozen scripted replies, not model
  extraction or unscripted human answers. The result records the matched
  channels: `confirmed_context`, `model_tool_call`, `controlled_injection`, or
  `scripted_user_reply`. A value match does not reveal the model's cognitive
  source; channel provenance is the auditable claim.

## Results and denominators

**Original OpenManus:** 21 rows, 12 supported and 9 unsupported. Nine supported
GitHub investigations used unstructured `python_execute` code, which has no
comparable typed critical-slot interface; one supported MCP failure has zero
gold fields. Its **two typed MCP natural steps** have TP=3, FP=0, FN=0,
Precision=1.000, Recall=1.000, F1=1.000. This narrow subset must not be used
as an overall comparison with TaskPilot's GitHub plus MCP pool. Missing-field,
clarification and parameter-error denominators are zero / NOT APPLICABLE.

**TaskPilot Full-tools:** 12 natural tasks, 9 controlled zero-LLM tasks, no
unsupported rows. Natural resolved: **16 steps**, TP=41, FP=10, FN=4;
Precision=0.804, Recall=0.911, F1=0.854. Natural model-explicit extraction:
17 task/Observation gold fields, TP=13, FP=10, FN=4; Precision=0.565,
Recall=0.765, F1=0.650. Controlled resolved: **6 value-bearing steps**,
TP=14, FP=0, FN=0. All typed resolved combined: TP=55, FP=10, FN=4;
Precision=0.846, Recall=0.932, F1=0.887. The combined figure includes
scripted control and is **not** an autonomous model result.

**TaskPilot K=3:** same 12 natural and 9 controlled tasks. Natural resolved:
**16 steps**, TP=40, FP=12, FN=5; Precision=0.769, Recall=0.889,
F1=0.825. Natural model-explicit extraction: 17 task/Observation gold fields,
TP=12, FP=12, FN=5; Precision=0.500, Recall=0.706, F1=0.585.
Controlled resolved: **6 value-bearing steps**, TP=14, FP=0, FN=0.
All typed resolved: TP=54, FP=12, FN=5; Precision=0.818, Recall=0.915,
F1=0.864. K=3 is lower than Full-tools on these single-run slot metrics;
no overall improvement or statistical significance follows from 21 tasks.

Critical-field accuracy (matched gold fields / gold fields) is 3/3 for the
typed upstream subset; Full-tools natural resolved **41/45**, model-explicit
**13/17**, controlled **14/14**; K=3 natural resolved **40/45**,
model-explicit **12/17**, controlled **14/14**. Strict step accuracy requiring
all gold fields and no extra critical pair is 2/2 upstream typed,
9/16 Full-tools natural and 7/16 K=3 natural. The full per-step values are in
the result JSON rather than omitted from the aggregate.

## Missing fields, clarification, errors, and guesses

For each TaskPilot group, `core-007/008/014` detects **3/3** frozen missing
fields with no extra field and **3/3** zero dispatch before reply. All **3/3**
scripted replies merge, revalidate and produce exactly one Tool execution with
the original `tool_call_id` preserved. `core-009` malformed JSON and
`core-010` boolean-as-integer are recognized as the expected parameter-error
type **2/2**, each with zero Tool dispatch. Original OpenManus did not support
these five controlled boundary cases, so no zero score is imputed.

There are **0 confirmed wrong values on a frozen gold field** in either
TaskPilot natural group or its controlled group. Full-tools has 10 and K=3 has
12 FP pairs on natural steps, primarily extra search queries or a tool for a
later step (for example `core-006/020/021`). An extra pair is a scoring FP,
but its lack of a *basis* cannot be proven solely by absence from this step's
gold: some terms appear in the task or live Observation. Accordingly the
confirmed unsupported-value count is zero and a broader unsupported-guess
count is **NOT VERIFIED**, rather than claiming all FP pairs were guesses.

## Manual audit and limits

- `core-001/003` compare task path/issue number and confirmed repo/ref against
  actual call arguments plus the recorded context. `core-013/015` compare MCP
  text/suffix in the selected upstream retest and both TaskPilot runs.
- `core-007/008/014` inspect `run.pending_missing_fields`, scripted reply
  source, validation pending→success, clarification requested→merged, one Tool
  execution, and the original Tool message ID. `core-009/010` inspect raw
  injected input and validation failure with no execution.
- `core-005/006/020/021` retain missed path/query fields and extra calls as
  FP/FN even when other parts of the investigation succeeded. Failure and
  NOT VERIFIED task outcomes were not removed from applicable slot steps.
- The holdout has **no natural multi-round parameter correction** and no
  unscripted human reply. A deterministic wrong→correct value scorer test is
  not evidence of a real correction run. Both are **NOT VERIFIED**. A model's
  precise use of task versus Observation text is likewise unobservable from
  matching values alone.

## Regression-count reconciliation

The T070 commit added nine tests and removed none. Before T071, a fresh
`tests/taskpilot` collection returned **359** tests; adding T071's eight tests
raises it to **367**. The earlier reported **363** and **359 passed** used
commands not retained in the frozen benchmark evidence, so the exact four-test
selection difference cannot be reconstructed as a historical fact. Current
verification explicitly runs the entire TaskPilot suite plus baseline and
Browser MCP tests; no T070 test was deleted or excluded by T071.
