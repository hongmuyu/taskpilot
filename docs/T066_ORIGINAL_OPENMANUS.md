# T066 — original OpenManus baseline

This is a **raw product baseline**, not the T069–T071 scored comparison. The
frozen [T065 holdout](../eval/datasets/core_benchmark_v1_holdout.json) and
[scoring contract](../eval/datasets/core_benchmark_v1_scoring.json) were not
changed. All 21 samples have an entry in the
[per-sample review](../eval/results/t066_original_openmanus_review.json).

## Pinned environment and inputs

- Upstream checkout: detached, unmodified
  `3309bf4e416fb1c74b008f3e86494439a31bad53` in an independent worktree.
  The runner rejects another HEAD or tracked changes.
- Runtime: Linux Docker, `taskpilot-service:pr08c-nocache` dependency image,
  Python 3.12.14, MCP SDK 1.5.0, OpenAI SDK 1.66.5; the entire upstream source tree is bind-mounted read-only
  over `/srv/taskpilot`. The image provides locked dependencies, **not**
  TaskPilot Agent or Tool code. Original OpenManus default Manus assembly is
  `ask_human`, `python_execute`, `str_replace_editor`, `terminate`. A read-only
  FastMCP stdio fixture is attached for the MCP cases using upstream
  `Manus.connect_mcp_server`; then five `mcp_fixture_*` tools appear. There are
  no TaskPilot GitHub Tools, semantic routing, validator, or reliability
  wrapper in the execution path.
- Model: the existing `deepseek-flash` configuration, `temperature=0`,
  `max_tokens=1024`, non-thinking request mode. The provider's model revision
  was not reported. A local one-use proxy injects the real key into provider
  requests; the container receives only a disposable proxy token. Raw
  requests/responses and keys are not saved. Provider-reported usage and HTTP
  status are saved for every request.
- Budget: one fresh Agent and container per sample, `max_steps=6`, 90-second
  Agent budget, 120-second container budget; same frozen task and confirmed
  repository/ref context in each prompt. One common instruction prohibits
  writes and credential inspection. Container root and upstream mount are
  read-only; capabilities are dropped; only bounded `/tmp` and logs tmpfs are
  writable. Browser Use is disabled because its separate key is unavailable.
  The host's existing `tiktoken` cache is mounted read-only to avoid a cold
  download during import. The Python tool itself has an upstream five-second
  limit, which explains several observed timeouts.
- T065 source snapshot was checked after the runs: 8/8 Issue body and update
  markers, 11/11 pinned file blob hashes still matched. The actual runtime's
  GitHub reads did not use TaskPilot's authenticated GitHub adapter.

## Run and review

The first run contains all 21 rows: 12 preflight supported, 9 unsupported.
Nine supported GitHub cases ran through the real provider; three MCP cases
initially failed during fixture attachment. The first MCP retest failed because
the worker redirected stdio into an object without a file descriptor. Both
setup failures are preserved. After redirecting to `/dev/null`, the final MCP
retest ran all three via the real provider and real stdio transport.

The reviewed outcome is **4 supported success, 8 supported failure, 9
unsupported**. This is a count of case dispositions, not a final benchmark
metric. Successful cases are `core-001` (pinned README), `core-003` (Issue
#895), `core-013` (v1 MCP echo), and `core-015` (v2 MCP echo). `core-002`,
`004`–`006`, `018`, `020`, and `021` ended without required business evidence;
most exhausted the six-step budget. `core-016` reached the fixture once and
the server returned `isError=true`, but upstream `MCPClientTool` converted
that to an output string without failure status. The Agent then called
`terminate(success)`. The frozen failure criterion requires status and
Observation evidence, so this case is a failure despite the final prose
acknowledging the controlled error. An independent real MCP SDK check also
observed `isError=true` and one server dispatch.

Unsupported cases are `core-007`–`012`, `014`, `017`, `019`. Each has an
individual capability or safety reason in the review. In particular,
`core-012` could expose process environment through original Python execution
and was intentionally not dispatched. Unsupported rows have zero provider and
tool calls. No GitHub tool was secretly added to make these cases run.

Raw evidence, including failed attempts, per-request provider usage and HTTP
status, tool-call IDs/arguments, truncated observations, MCP server audit,
elapsed time, environment, and cleanup:

- [Initial 21 rows](../eval/results/t066_original_openmanus_v1.jsonl)
- [First MCP retest, setup failures](../eval/results/t066_original_openmanus_mcp_retest.jsonl)
- [Final MCP retest](../eval/results/t066_original_openmanus_mcp_retest_2.jsonl)
- [Reviewed case index](../eval/results/t066_original_openmanus_review.json)

To reproduce, check out the pinned upstream commit independently, create empty
`config/config.toml` and `config/mcp.json` mountpoints and `logs/` there,
prepare the pinned Docker dependency image and a local ignored provider
config, then run:

```bash
.venv/bin/python -m eval.t066_original_openmanus \
  --upstream /path/to/independent/upstream-checkout \
  --config config/config.toml \
  --image taskpilot-service:pr08c-nocache \
  --output /path/to/new/raw.jsonl
.venv/bin/python -m eval.t066_review_results
```

The runner creates output exclusively; a rerun needs a new path. The raw
files listed above are the actual first run and two MCP retests, not
postprocessed simulated results. The review script only indexes those rows and
records a separate human evidence judgment. It does not calculate final Tool
Selection Accuracy, Task Success Rate, slot scores, or comparative costs.

## Remaining limits

- Provider model revision: **NOT VERIFIED** (not exposed in these responses).
- Original OpenManus with a pristine upstream dependency image rather than the
  pinned TaskPilot dependency image: **NOT VERIFIED**. The mounted source and
  original Agent/Tool assembly are pinned and verified.
- A fully clean `tiktoken` cache download in this restricted network:
  **NOT VERIFIED**; the existing tokenizer cache was mounted read-only.
- Original baseline's GitHub error/status handling for cases that never
  reached the intended endpoint, including the intended 404: **NOT VERIFIED**.
- Browser Use and unsafe environment-disclosure actions were not enabled.
  They do not count as successful or attempted tasks.
