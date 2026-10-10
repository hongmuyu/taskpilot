"""Opt-in real stdio MCP and public GitHub failure E2E for T038.

Run with ``PYTHONPATH=. .venv/bin/python tests/taskpilot/run_t038_real_failure_e2e.py``.
Only allowlisted, credential-free evidence is written to docs/evidence.
"""

import asyncio
import importlib.metadata
import json
import os
import signal
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx

from app.agent.repository_investigation import RepositoryInvestigationAgent
from app.agent.toolcall import ToolCallAgent
from app.llm import LLM
from app.logger import logger
from app.schema import AgentState, Function, ToolCall
from app.taskpilot.github_tools import GitHubClient, RepositoryContext
from app.tool.mcp import MCPClients


SERVER = Path(__file__).parent / "fixtures" / "stdio_mcp_server.py"
EVIDENCE = (
    Path(__file__).parents[2] / "docs" / "evidence" / "t038_real_failure_e2e.json"
)


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def call(name: str, arguments: dict, call_id: str) -> ToolCall:
    return ToolCall(
        id=call_id,
        function=Function(name=name, arguments=json.dumps(arguments)),
    )


def audit_entries(path: Path) -> list[dict]:
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def status(observation: str) -> str:
    first = observation.splitlines()[0] if observation else ""
    return first.removeprefix("Status: ") if first.startswith("Status: ") else "missing"


def observation_field(observation: str, name: str) -> str | None:
    prefix = f"{name}: "
    return next(
        (
            line.removeprefix(prefix)
            for line in observation.splitlines()
            if line.startswith(prefix)
        ),
        None,
    )


async def connect(clients: MCPClients, audit: Path) -> None:
    await clients.connect_stdio(
        sys.executable,
        [str(SERVER)],
        server_id="fixture",
        env={
            "TASKPILOT_MCP_AUDIT_PATH": str(audit),
            "TASKPILOT_MCP_VARIANT": "v1",
        },
    )


async def mcp_is_error(directory: Path) -> dict:
    audit = directory / "mcp_error.jsonl"
    clients = MCPClients(connect_timeout_seconds=5, cleanup_timeout_seconds=1)
    case = {"scenario": "mcp_isError_true", "fault": "fixture raises ValueError"}
    try:
        await connect(clients, audit)
        started = time.perf_counter()
        raw = await clients.sessions["fixture"].call_tool("fail_read", {})
        case["raw_protocol_elapsed_ms"] = elapsed_ms(started)
        agent = ToolCallAgent(available_tools=clients)
        agent.tool_calls = [call("mcp_fixture_fail_read", {}, "mcp-error-call")]
        started = time.perf_counter()
        observation = await agent.act()
        case["agent_elapsed_ms"] = elapsed_ms(started)
        entries = audit_entries(audit)
        case.update(
            raw_isError=raw.isError,
            observation_status=status(observation),
            observation_has_error="controlled fixture failure" in observation,
            tool_call_id_preserved=(
                agent.memory.messages[-1].tool_call_id == "mcp-error-call"
            ),
            intentional_client_calls=2,
            server_dispatch_count=len(entries),
            server_requests=entries,
            automatic_retry_count=max(0, len(entries) - 2),
            observation_attempts=observation_field(observation, "Attempts"),
            agent_finished_after_failure=(agent.state == AgentState.FINISHED),
        )
        case["result"] = (
            "PASS"
            if (
                raw.isError is True
                and status(observation) == "failure"
                and case["observation_has_error"]
                and case["tool_call_id_preserved"]
                and entries
                == [
                    {"tool": "fail_read", "arguments": {}},
                    {"tool": "fail_read", "arguments": {}},
                ]
                and agent.state != AgentState.FINISHED
            )
            else "FAIL"
        )
    finally:
        started = time.perf_counter()
        await clients.disconnect()
        case["cleanup_elapsed_ms"] = elapsed_ms(started)
    return case


async def mcp_timeout(directory: Path) -> dict:
    audit = directory / "mcp_timeout.jsonl"
    clients = MCPClients(connect_timeout_seconds=5, cleanup_timeout_seconds=1)
    case = {
        "scenario": "mcp_timeout",
        "fault": "fixture slow_read waits 500 ms; tool deadline is 50 ms",
    }
    try:
        await connect(clients, audit)
        clients.tool_map["mcp_fixture_slow_read"].timeout_seconds = 0.05
        agent = ToolCallAgent(available_tools=clients)
        agent.tool_calls = [
            call("mcp_fixture_slow_read", {"wait_ms": 500}, "mcp-timeout-call")
        ]
        started = time.perf_counter()
        observation = await agent.act()
        case["elapsed_ms"] = elapsed_ms(started)
        entries = audit_entries(audit)
        case.update(
            observation_status=status(observation),
            observation_error_kind_timeout=("Error kind: timeout" in observation),
            remote_outcome="unknown",
            server_dispatch_count=len(entries),
            server_requests=entries,
            automatic_retry_count=max(0, len(entries) - 1),
            observation_attempts=observation_field(observation, "Attempts"),
            tool_call_id_preserved=(
                agent.memory.messages[-1].tool_call_id == "mcp-timeout-call"
            ),
            agent_finished_after_timeout=(agent.state == AgentState.FINISHED),
        )
        case["result"] = (
            "PASS"
            if (
                status(observation) == "unknown"
                and case["observation_error_kind_timeout"]
                and case["tool_call_id_preserved"]
                and entries == [{"tool": "slow_read", "arguments": {"wait_ms": 500}}]
                and agent.state != AgentState.FINISHED
            )
            else "FAIL"
        )
    finally:
        started = time.perf_counter()
        await clients.disconnect()
        case["cleanup_elapsed_ms"] = elapsed_ms(started)
    return case


async def mcp_disconnect_reconnect(directory: Path) -> dict:
    audit = directory / "mcp_disconnect.jsonl"
    clients = MCPClients(connect_timeout_seconds=5, cleanup_timeout_seconds=1)
    case = {
        "scenario": "mcp_disconnect_reconnect",
        "fault": "SIGKILL only the PID returned by the disposable stdio fixture",
    }
    try:
        await connect(clients, audit)
        pid_result = await clients.execute(name="mcp_fixture_server_pid", tool_input={})
        if pid_result.status != "success":
            raise RuntimeError("fixture PID probe failed")
        pid = int(pid_result.output.strip())
        if SERVER.name.encode() not in Path(f"/proc/{pid}/cmdline").read_bytes():
            raise RuntimeError("fixture PID identity check failed")
        os.kill(pid, signal.SIGKILL)

        agent = ToolCallAgent(available_tools=clients)
        agent.tool_calls = [
            call("mcp_fixture_echo_read", {"text": "after crash"}, "broken-call")
        ]
        started = time.perf_counter()
        broken = await agent.act()
        case["broken_call_elapsed_ms"] = elapsed_ms(started)
        broken_dispatches = len(audit_entries(audit))

        started = time.perf_counter()
        await asyncio.wait_for(clients.disconnect("fixture"), timeout=3)
        case["disconnect_elapsed_ms"] = elapsed_ms(started)
        cleared = clients.sessions == clients.exit_stacks == clients.tool_map == {}

        agent.tool_calls = [
            call("mcp_fixture_echo_read", {"text": "stale"}, "stale-call")
        ]
        stale = await agent.act()
        stale_dispatches = len(audit_entries(audit))

        await connect(clients, audit)
        agent.tool_calls = [
            call("mcp_fixture_echo_read", {"text": "recovered"}, "recovered-call")
        ]
        started = time.perf_counter()
        recovered = await agent.act()
        case["recovered_call_elapsed_ms"] = elapsed_ms(started)
        entries = audit_entries(audit)
        case.update(
            pid_probe_status=pid_result.status,
            broken_status=status(broken),
            stale_status=status(stale),
            recovered_status=status(recovered),
            broken_error_kind=observation_field(broken, "Error kind"),
            broken_attempts=observation_field(broken, "Attempts"),
            stale_attempts=observation_field(stale, "Attempts"),
            recovered_attempts=observation_field(recovered, "Attempts"),
            broken_call_id_preserved=(
                agent.memory.messages[-3].tool_call_id == "broken-call"
            ),
            stale_call_id_preserved=(
                agent.memory.messages[-2].tool_call_id == "stale-call"
            ),
            recovered_call_id_preserved=(
                agent.memory.messages[-1].tool_call_id == "recovered-call"
            ),
            state_cleared_after_disconnect=cleared,
            server_dispatch_before_reconnect=broken_dispatches,
            server_dispatch_after_stale_call=stale_dispatches,
            server_dispatch_after_reconnect=len(entries),
            server_requests=entries,
            automatic_retry_count=0,
            agent_continued_after_failure=(agent.state != AgentState.FINISHED),
        )
        case["result"] = (
            "PASS"
            if (
                status(broken) != "success"
                and status(stale) == "failure"
                and status(recovered) == "success"
                and all(
                    case[name]
                    for name in (
                        "broken_call_id_preserved",
                        "stale_call_id_preserved",
                        "recovered_call_id_preserved",
                        "state_cleared_after_disconnect",
                    )
                )
                and broken_dispatches == stale_dispatches == 0
                and entries
                == [{"tool": "echo_read", "arguments": {"text": "recovered"}}]
            )
            else "FAIL"
        )
    finally:
        started = time.perf_counter()
        await clients.disconnect()
        case["final_cleanup_elapsed_ms"] = elapsed_ms(started)
    return case


async def github_missing_resource() -> dict:
    missing_path = f"__taskpilot_t038_missing__/{uuid4().hex}.txt"
    http_events = []

    async def on_response(response: httpx.Response) -> None:
        http_events.append(
            {
                "method": response.request.method,
                "path": response.request.url.path,
                "status": response.status_code,
            }
        )

    http_client = httpx.AsyncClient(
        base_url="https://api.github.com",
        timeout=15,
        event_hooks={"response": [on_response]},
    )
    github = GitHubClient(token=os.getenv("GITHUB_TOKEN"), http_client=http_client)
    calls = [
        call("github_repository_info", {}, "github-repository-call"),
        call("github_read_file", {"path": missing_path}, "github-missing-call"),
        call("terminate", {"status": "failure"}, "github-finish-call"),
    ]
    llm = object.__new__(LLM)
    selections = []

    async def ask_tool(**kwargs):
        selections.append(len(kwargs["messages"]))
        return SimpleNamespace(content=None, tool_calls=[calls[len(selections) - 1]])

    llm.ask_tool = AsyncMock(side_effect=ask_tool)
    agent = RepositoryInvestigationAgent.create(
        RepositoryContext.parse("FoundationAgents/OpenManus", source="user_input"),
        client=github,
        llm=llm,
        max_steps=4,
    )
    agent.run_timeout_seconds = 40
    agent.cleanup_timeout_seconds = 2
    try:
        started = time.perf_counter()
        result = await agent.run("Check the repository and read a missing file")
        elapsed = elapsed_ms(started)
    finally:
        await github.aclose()

    observations = {
        message.tool_call_id: message.content
        for message in agent.memory.messages
        if message.role == "tool"
    }
    repo = observations.get("github-repository-call", "")
    missing = observations.get("github-missing-call", "")
    finish = observations.get("github-finish-call", "")
    missing_http = [item for item in http_events if item["path"].endswith(missing_path)]
    case = {
        "scenario": "github_missing_resource",
        "fault": "unique synthetic nonexistent path in a confirmed public repository",
        "selection_source": "scripted Agent tool choices; real GitHub HTTP",
        "repository": "FoundationAgents/OpenManus",
        "missing_path": missing_path,
        "http_requests": http_events,
        "http_request_count": len(http_events),
        "missing_resource_attempt_count": len(missing_http),
        "repository_observation_status": status(repo),
        "missing_observation_status": status(missing),
        "missing_observation_attempts": (
            "1:permanent(404)" if "Attempts: 1:permanent(404)" in missing else "other"
        ),
        "final_agent_termination": (
            "failure" if "completed with status: failure" in finish else "other"
        ),
        "tool_call_ids_preserved": set(observations)
        == {
            "github-repository-call",
            "github-missing-call",
            "github-finish-call",
        },
        "agent_step_count": len(selections),
        "elapsed_ms": elapsed,
    }
    case["result"] = (
        "PASS"
        if (
            [event["status"] for event in http_events] == [200, 404]
            and [event["method"] for event in http_events] == ["GET", "GET"]
            and status(repo) == "success"
            and status(missing) == "failure"
            and len(missing_http) == 1
            and case["missing_observation_attempts"] == "1:permanent(404)"
            and case["final_agent_termination"] == "failure"
            and case["tool_call_ids_preserved"]
            and len(selections) == 3
            and "completed with status: failure" in result
        )
        else "FAIL"
    )
    return case


async def main() -> int:
    logger.remove()
    cases = []
    with tempfile.TemporaryDirectory(prefix="taskpilot-t038-") as directory:
        root = Path(directory)
        for name, action in (
            ("mcp_isError_true", lambda: mcp_is_error(root)),
            ("mcp_timeout", lambda: mcp_timeout(root)),
            ("mcp_disconnect_reconnect", lambda: mcp_disconnect_reconnect(root)),
            ("github_missing_resource", github_missing_resource),
        ):
            try:
                cases.append(await action())
            except Exception as exc:
                cases.append(
                    {
                        "scenario": name,
                        "result": "NOT VERIFIED",
                        "error_type": type(exc).__name__,
                    }
                )

    passed = all(case["result"] == "PASS" for case in cases)
    evidence = {
        "run_date": date.today().isoformat(),
        "result": "PASS" if passed else "NOT VERIFIED",
        "mcp_server": "local FastMCP read-only fixture",
        "mcp_sdk_version": importlib.metadata.version("mcp"),
        "mcp_transport": "stdio",
        "github_transport": "real HTTPS via httpx; no MockTransport",
        "cases": cases,
        "not_verified": [
            "other MCP transports",
            "uncontrolled external service failures",
            "real model judgment after failure",
        ],
    }
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"result": evidence["result"], "cases": cases}, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
