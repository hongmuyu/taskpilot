# PR-03 / T021 validation evidence

Run date: 2026-10-04. Product checkout: `codex/pr-03-t013` at
`f4d4a6bf3314c2c0675dadac1f68caf2474d7ccc` before the T021 commit.
The model was `deepseek-flash` through the configured DeepSeek OpenAI-compatible
endpoint, with thinking disabled as in the PR-02 live baseline. The provider
did not expose a separate model revision. Repository Context was explicitly
confirmed as `FoundationAgents/OpenManus`, `ref=None` (GitHub default branch,
reported as `main`), with `source="user_input"`. The process inherited
`GITHUB_TOKEN`; its value and request headers were not recorded.

The live checks used the production `RepositoryInvestigationAgent`, five local
GitHub tools, `ToolCollection` validator, `LLM`, and `GitHubClient` against
`api.github.com`. A response hook counted HTTP requests and recorded only URL
paths and statuses. A tool execution probe recorded validator outcomes and
call IDs. Human replies were scripted at the existing CLI `AskHuman` boundary.
Negative tool-call cases changed **only the arguments of a call selected by the
real model** immediately before the execution boundary. These are fault
injection checks, not claims that the model naturally emitted bad arguments.

## Parameter cases

- **Missing repository at CLI entry:** Omit `--repository`; one `AskHuman`
  prompt asked for `owner/repo`. A scripted reply of
  `FoundationAgents/OpenManus` passed `RepositoryContext.parse` with
  `source="user_input"`. Only then did the real model call
  `github_repository_info`, `github_read_file(path=README.md)`, and `terminate`.
  GitHub returned 200 for metadata and README (two requests). The final answer
  summarized the repository. Cancel, empty, and invalid replies had zero Agent
  creation and zero tool dispatch in deterministic tests.
- **Missing path, clarification, merge, revalidation:** The model selected
  `github_read_file`; fault injection replaced its arguments with `{}` while
  preserving `tool_call_id=call_00_H181JM7mu6WH0rmHaQv54817`. The first
  validation returned `MissingParameterFailure` with zero GitHub requests.
  `AskHuman` asked once for `path` and did not ask for the confirmed repository.
  The scripted reply `README.md` merged into this pending call. The same
  `ToolCollection.execute` validator saw `{"path":"README.md"}` and returned
  `ToolResult`; one real GET to
  `/repos/FoundationAgents/OpenManus/contents/README.md` returned HTTP 200.
  The tool observation retained the original call ID and the model finished
  with `terminate(success)`. Validation attempts: two; real GitHub executions:
  one.
- **Valid direct call:** The real model selected
  `github_read_file({"path":"README.md"})` with
  `tool_call_id=call_00_C6kS0AbMqPIomIAhSRPY3249`. No clarification occurred.
  Validation returned `ToolResult`; exactly one README GET returned HTTP 200,
  the observation retained that call ID, and the model called
  `terminate(success)`.
- **Malformed JSON:** The model selected `github_read_file`; fault injection
  changed its arguments to `{"path":` while preserving
  `tool_call_id=call_00_eAixk2W2QPBsq2mOJB0B1935`. Parsing produced an
  error observation with that ID. `ToolCollection.execute` calls: zero;
  GitHub HTTP requests: zero. The run was intentionally capped at one step.
- **Wrong basic type:** The model selected `github_read_file`; fault injection
  supplied `{"path":7}` with
  `tool_call_id=call_00_gubPPgUSk5FM8kH6kVFX3531`. The validator returned
  `ToolValidationFailure`; GitHub HTTP requests: zero. The observation kept
  the original call ID. The run was intentionally capped at one step.
- **Wrong type then corrected:** A fault-injected `{"path":7}` call
  (`call_00_NajAxpeXfo42caWoKSoQ1734`) failed validation with zero HTTP
  requests. The real model then issued a new valid
  `github_read_file({"path":"README.md"})` call
  (`call_00_eFGcTu3cRs85JH6XlUHW2350`); one README GET returned HTTP 200.
  The model's final answer acknowledged the type error and successful read.
  This was a model correction through a new call, separate from the pending
  clarification resume case above.

An additional natural-language attempt to induce a missing `path` caused the
model to choose `terminate(failure)` without a GitHub request. Thus spontaneous
model emission of a missing required tool argument is **NOT VERIFIED**. The
fault-injected case verifies the production execution, clarification, merge,
revalidation, and real API path when such a call reaches the boundary.
An exploratory retry with tool choice required produced
`github_read_file({"path":""})`, followed by `terminate(failure)`: existing
file-path business validation rejected the empty string with zero HTTP
requests and no clarification. This was an invalid value, not an absent JSON
field, and the retry did not use the product's default `AUTO` tool choice.

## PR-02 investigation regression rerun

The following are new live runs on the checkout above, separate from the
historical [PR-02 evidence](REPOSITORY_INVESTIGATION.md). Each used the real
model and the same explicitly confirmed repository context; all listed HTTP
responses were 200 and each run produced a nonempty final answer.

- **A — Repository Understanding:** Model calls:
  `github_repository_info` → `github_read_file` → `terminate`. HTTP:
  `/repos/FoundationAgents/OpenManus`, then
  `/repos/FoundationAgents/OpenManus/contents/README.md` (two requests).
  Final answer began “OpenManus — Repository Summary”.
- **B — Issue Investigation:** Model calls: `github_issue_detail` → two
  `github_code_search` → two `github_read_file` → `terminate`. HTTP: Issue
  `#1427`, its comments, two `/search/code` requests, and reads of
  `app/tool/mcp.py` and `app/tool/base.py` (six requests). Final answer
  summarized Issue `#1427` and the MCP `isError` problem.
- **C — Issue→Code:** Model calls: `github_issue_detail` →
  `github_code_search` → two `github_read_file` → `terminate`. HTTP: Issue
  `#1427`, its comments, `/search/code`, and reads of `app/tool/mcp.py` and
  `app/tool/base.py` (five requests). Final answer connected Issue `#1427`
  with `MCPClientTool` code.

## Deterministic and environment checks

The T013–T020 tests cover JSON/schema zero-dispatch, required-field
clarification, pending reply merge, revalidation, exactly-once resume,
Repository Context provenance/reuse, and Local/MCP-style schema compatibility.
T021 adds CLI entry tests for repository clarification and non-execution on
cancel, empty, or invalid replies. Final counts are also recorded in
[T021](TASKS.md#t021--parameter-regression-tests--real-e2e-validation).
Checks were run with `.venv/bin/python -m pytest -q --disable-warnings` and
these targets:

- The eight T013–T020 parameter test modules plus
  `tests/taskpilot/test_parameter_entry.py`.
- `tests/taskpilot`.
- `tests/taskpilot/test_github_tools.py tests/baseline/test_runtime.py
  tests/tools/test_browser_use_mcp.py`.
- Repository-wide `--maxfail=1` (with the same `-q --disable-warnings` options).

The T013–T021 targeted matrix passed **107/107**, all TaskPilot tests passed
**120/120**, and the PR-02 local regression passed **26/26**. The
repository-wide `pytest --maxfail=1` stopped after **9 passed, 1 failed** at
`tests/sandbox/test_client.py::test_sandbox_creation`: its expected Python
3.10 did not match the sandbox's Python 3.12.14. No sandbox code was changed.

**NOT VERIFIED:** Real MCP connect/discovery/server call/disconnect/reconnect
and real MCP parameter E2E remain T035/T038. Default provider thinking mode
and spontaneous model emission of invalid/missing tool arguments were not
proved. The repository-wide pytest suite is not fully verified because the
unrelated sandbox test asserts Python 3.10 while this environment runs 3.12.14.
