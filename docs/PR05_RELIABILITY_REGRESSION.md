# PR-05 Core P0 reliability regression matrix (T037)

This matrix covers deterministic and isolated integration behavior on the
`codex/pr-05-t031` branch. The real external failure E2E remains T038.

- **Result status and Observation:**
  `tests/taskpilot/test_tool_result_status.py` checks Local `ToolResult`, string,
  dict and exception results; MCP `isError=true/false/absent`, multiple content
  blocks, truncation, status headers and the original tool call ID. A failed
  control tool does not finish the Agent.
- **Timeout, cancellation, cleanup and budget:**
  `tests/taskpilot/test_tool_timeout_policy.py` checks Local/MCP timeout as
  `unknown`, cancellation, partial MCP connection cleanup, bounded disconnect
  and Agent cleanup, and no new dispatch after the run budget expires.
  `tests/taskpilot/test_real_mcp_lifecycle.py` checks a real stdio fixture's
  connection timeout, slow call and crashed server cleanup.
- **Retry classification and deadline:**
  `tests/taskpilot/test_tool_retry_policy.py` checks transient 429/5xx and
  temporary connection recovery, the three-attempt cap, permanent 401/403/404
  and schema failures without retry, unknown timeout, non-idempotent/MCP tools
  without automatic replay, Retry-After, cancellation and Agent/Tool deadlines.
- **MCP failure and lifecycle:**
  `tests/taskpilot/test_real_mcp_lifecycle.py` checks real FastMCP stdio
  connect/discovery/call/disconnect/reconnect, `isError` failure Observation,
  stale calls as `failure` with zero dispatch, bounded cleanup, no duplicate or
  stale registration, and schema/index changes after reconnect.
- **GitHub read-only failure and recovery:**
  `tests/taskpilot/test_github_tools.py` checks API failures across the five
  Local tools. Its Agent regression checks 404 failure without replay,
  corrected-path recovery with one 503 retry, preserved Observations and call
  IDs; a repeated 403 exits at `max_steps` without reporting success.
- **Cross-feature execution boundary:**
  `tests/taskpilot/test_control_tool_preservation.py` checks K=1 business
  selection with `terminate` retained, missing required input forcing
  clarification before dispatch, revalidation, transient read retry and the
  original call ID. `tests/taskpilot/test_clarification_resume.py` checks
  invalid replies, duplicate/late replies, cancellation and parallel calls;
  `tests/taskpilot/test_tool_schema_validation.py` checks zero dispatch for
  invalid schema arguments.

The scripted LLM responses in Agent tests verify wiring and bounded behavior,
not model judgment. T034 non-idempotent writes and T036 fallback are P1 and
outside this matrix. Uncontrolled real GitHub/MCP failures remain **NOT
VERIFIED** until T038; deterministic fixtures are not presented as that E2E.

Verification on 2026-10-10:

- `pytest -q tests/taskpilot/test_tool_result_status.py
  tests/taskpilot/test_tool_timeout_policy.py
  tests/taskpilot/test_tool_retry_policy.py
  tests/taskpilot/test_real_mcp_lifecycle.py
  tests/taskpilot/test_github_tools.py
  tests/taskpilot/test_control_tool_preservation.py
  tests/taskpilot/test_clarification_resume.py`: 99 passed.
- `pytest -q tests/taskpilot tests/baseline/test_runtime.py
  tests/tools/test_browser_use_mcp.py`: 272 passed.
- `pytest -q --maxfail=1`: 9 passed, 1 failed at unrelated
  `tests/sandbox/test_client.py::test_sandbox_creation`; it expects Python
  3.10 while the configured sandbox runs Python 3.12.14. The remainder of the
  full suite is **NOT VERIFIED**.
