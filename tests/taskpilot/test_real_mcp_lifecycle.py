import json
import os
import signal
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

with patch(
    "tomllib.load",
    return_value={
        "llm": {
            "model": "test",
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": "test",
        },
        "daytona": {"daytona_api_key": "unused-test"},
    },
):
    from app.agent.toolcall import ToolCallAgent
    from app.schema import Function, ToolCall
    from app.taskpilot.tool_embedding_index import InMemoryToolIndex
    from app.tool.mcp import MCPClients


SERVER = Path(__file__).parent / "fixtures" / "stdio_mcp_server.py"


class FixedVectors:
    model_id = "fixture-vectors"
    revision = "fixture-1"
    vector_version = "v1"

    def __init__(self):
        self.calls = []

    async def embed(self, texts):
        self.calls.append(tuple(texts))
        return [[1.0, 0.0] for _ in texts]


async def connect(clients, audit_path, variant="v1", startup_delay_ms=0):
    await clients.connect_stdio(
        sys.executable,
        [str(SERVER)],
        server_id="fixture",
        env={
            "TASKPILOT_MCP_AUDIT_PATH": str(audit_path),
            "TASKPILOT_MCP_VARIANT": variant,
            "TASKPILOT_MCP_STARTUP_DELAY_MS": str(startup_delay_ms),
        },
    )


def audit_entries(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.asyncio
async def test_real_stdio_lifecycle_validation_and_reconnect(tmp_path):
    audit = tmp_path / "calls.jsonl"
    clients = MCPClients(connect_timeout_seconds=5, cleanup_timeout_seconds=2)
    backend = FixedVectors()
    index = InMemoryToolIndex(backend)
    try:
        await connect(clients, audit)
        assert set(clients.tool_map) == {
            "mcp_fixture_echo_read",
            "mcp_fixture_legacy_read",
            "mcp_fixture_fail_read",
            "mcp_fixture_server_pid",
            "mcp_fixture_slow_read",
        }
        assert clients.tool_map["mcp_fixture_echo_read"].original_name == "echo_read"
        assert clients.tool_map["mcp_fixture_echo_read"].metadata.source == "mcp"
        assert await index.refresh(clients) is True
        v1 = index.version

        invalid = await clients.execute(
            name="mcp_fixture_echo_read", tool_input={"text": 123}
        )
        missing = await clients.execute(name="mcp_fixture_echo_read", tool_input={})
        assert invalid.status == "failure"
        assert missing.status == "failure"
        assert audit_entries(audit) == []

        agent = ToolCallAgent(available_tools=clients)
        agent.tool_calls = [
            ToolCall(
                id="real-mcp-call",
                function=Function(
                    name="mcp_fixture_echo_read",
                    arguments=json.dumps({"text": "hello"}),
                ),
            )
        ]
        observation = await agent.act()
        assert observation.startswith("Status: success\n")
        assert "echo:hello" in observation
        assert agent.memory.messages[-1].tool_call_id == "real-mcp-call"
        valid = await clients.execute(
            name="mcp_fixture_echo_read", tool_input={"text": "again"}
        )
        assert valid.status == "success"
        assert "echo:again" in valid.output
        assert audit_entries(audit) == [
            {"tool": "echo_read", "arguments": {"text": "hello"}},
            {"tool": "echo_read", "arguments": {"text": "again"}},
        ]
        failure = await clients.execute(name="mcp_fixture_fail_read", tool_input={})
        assert failure.status == "failure"
        assert "controlled fixture failure" in failure.error
        assert len(audit_entries(audit)) == 3
        assert clients.tool_map["mcp_fixture_echo_read"].retry_safe_read is False

        await clients.disconnect("fixture")
        assert clients.sessions == clients.exit_stacks == clients.tool_map == {}
        assert clients.tools == ()
        unavailable = await clients.execute(
            name="mcp_fixture_echo_read", tool_input={"text": "stale"}
        )
        assert unavailable.status == "failure"
        assert len(audit_entries(audit)) == 3
        await index.refresh(clients)
        assert index.version.content_fingerprint != v1.content_fingerprint

        await connect(clients, audit, variant="v2")
        assert "mcp_fixture_legacy_read" not in clients.tool_map
        assert "mcp_fixture_new_read" in clients.tool_map
        assert len(clients.tool_map) == 5
        assert (
            "suffix" in clients.tool_map["mcp_fixture_echo_read"].parameters["required"]
        )
        assert await index.refresh(clients) is True
        assert index.version.content_fingerprint != v1.content_fingerprint
        assert len(backend.calls) == 2

        old_shape = await clients.execute(
            name="mcp_fixture_echo_read", tool_input={"text": "hello"}
        )
        assert old_shape.status == "failure"
        assert len(audit_entries(audit)) == 3
        new_shape = await clients.execute(
            name="mcp_fixture_echo_read",
            tool_input={"text": "hello", "suffix": "world"},
        )
        assert new_shape.status == "success"
        assert "echo:hello:world" in new_shape.output
        assert len(audit_entries(audit)) == 4
    finally:
        await clients.disconnect()


@pytest.mark.asyncio
async def test_real_stdio_broken_server_cleanup_is_bounded(tmp_path):
    audit = tmp_path / "calls.jsonl"
    clients = MCPClients(connect_timeout_seconds=5, cleanup_timeout_seconds=1)
    try:
        await connect(clients, audit)
        pid_result = await clients.execute(name="mcp_fixture_server_pid", tool_input={})
        assert pid_result.status == "success"
        pid = int(pid_result.output.strip())
        os.kill(pid, signal.SIGKILL)
        clients.tool_map["mcp_fixture_echo_read"].timeout_seconds = 0.2
        failed = await clients.execute(
            name="mcp_fixture_echo_read", tool_input={"text": "after crash"}
        )
        assert failed.status != "success"
        assert audit_entries(audit) == []
        await clients.disconnect("fixture")
        assert clients.sessions == clients.exit_stacks == clients.tool_map == {}
        await connect(clients, audit)
        result = await clients.execute(
            name="mcp_fixture_echo_read", tool_input={"text": "after reconnect"}
        )
        assert result.status == "success"
    finally:
        await clients.disconnect()


@pytest.mark.asyncio
async def test_real_stdio_tool_timeout_is_unknown_and_not_retried(tmp_path):
    audit = tmp_path / "calls.jsonl"
    clients = MCPClients(connect_timeout_seconds=5, cleanup_timeout_seconds=1)
    try:
        await connect(clients, audit)
        tool = clients.tool_map["mcp_fixture_slow_read"]
        tool.timeout_seconds = 0.05

        result = await clients.execute(
            name=tool.name, tool_input={"wait_ms": 500}
        )

        assert result.status == "unknown"
        assert result.error_kind == "timeout"
        assert audit_entries(audit) == [
            {"tool": "slow_read", "arguments": {"wait_ms": 500}}
        ]
    finally:
        await clients.disconnect()


@pytest.mark.asyncio
async def test_real_stdio_missing_parameter_clarifies_before_server_dispatch(tmp_path):
    audit = tmp_path / "calls.jsonl"
    clients = MCPClients(connect_timeout_seconds=5, cleanup_timeout_seconds=1)
    try:
        await connect(clients, audit)
        agent = ToolCallAgent(available_tools=clients)
        agent.tool_calls = [
            ToolCall(
                id="clarify-real-mcp",
                function=Function(name="mcp_fixture_echo_read", arguments="{}"),
            )
        ]

        async def answer(self, *, inquire):
            assert "text" in inquire
            assert audit_entries(audit) == []
            return json.dumps({"text": "confirmed answer"})

        with patch("app.agent.toolcall.AskHuman.execute", new=answer):
            observation = await agent.act()

        assert observation.startswith("Status: success\n")
        assert "echo:confirmed answer" in observation
        assert agent.memory.messages[-1].tool_call_id == "clarify-real-mcp"
        assert (
            agent.tool_call_sources["clarify-real-mcp"]["text"]
            == "user_clarification"
        )
        assert audit_entries(audit) == [
            {"tool": "echo_read", "arguments": {"text": "confirmed answer"}}
        ]
    finally:
        await clients.disconnect()


@pytest.mark.asyncio
async def test_real_stdio_cancelled_clarification_never_calls_server(tmp_path):
    audit = tmp_path / "calls.jsonl"
    clients = MCPClients(connect_timeout_seconds=5, cleanup_timeout_seconds=1)
    try:
        await connect(clients, audit)
        agent = ToolCallAgent(available_tools=clients)
        command = ToolCall(
            id="cancel-real-mcp",
            function=Function(name="mcp_fixture_echo_read", arguments="{}"),
        )
        with patch(
            "app.agent.toolcall.AskHuman.execute",
            new=AsyncMock(return_value="cancel"),
        ):
            observation = await agent.execute_tool(command)

        assert "cancel" in observation.lower()
        assert audit_entries(audit) == []
    finally:
        await clients.disconnect()


@pytest.mark.asyncio
async def test_real_stdio_connect_timeout_clears_partial_state(tmp_path):
    clients = MCPClients(connect_timeout_seconds=0.05, cleanup_timeout_seconds=0.2)

    with pytest.raises(TimeoutError, match="connection.*timed out"):
        await connect(clients, tmp_path / "calls.jsonl", startup_delay_ms=1000)

    assert clients.sessions == clients.exit_stacks == clients.tool_map == {}
    assert clients.tools == ()


@pytest.mark.asyncio
async def test_disconnected_tool_has_failure_observation_and_reconnects(tmp_path):
    audit = tmp_path / "calls.jsonl"
    clients = MCPClients(connect_timeout_seconds=5, cleanup_timeout_seconds=1)
    agent = ToolCallAgent(available_tools=clients)
    try:
        await connect(clients, audit)
        agent.tool_calls = [
            ToolCall(
                id="server-error",
                function=Function(name="mcp_fixture_fail_read", arguments="{}"),
            )
        ]
        failed = await agent.act()
        assert failed.startswith("Status: failure\n")
        assert "controlled fixture failure" in failed
        assert agent.memory.messages[-1].tool_call_id == "server-error"
        assert audit_entries(audit) == [{"tool": "fail_read", "arguments": {}}]

        await clients.disconnect("fixture")
        agent.tool_calls = [
            ToolCall(
                id="stale-call",
                function=Function(
                    name="mcp_fixture_echo_read",
                    arguments=json.dumps({"text": "stale"}),
                ),
            )
        ]

        stale = await agent.act()

        assert stale.startswith("Status: failure\n")
        assert "Unknown tool" in stale
        assert agent.memory.messages[-1].tool_call_id == "stale-call"
        assert audit_entries(audit) == [{"tool": "fail_read", "arguments": {}}]

        await connect(clients, audit)
        agent.tool_calls = [
            ToolCall(
                id="fresh-call",
                function=Function(
                    name="mcp_fixture_echo_read",
                    arguments=json.dumps({"text": "fresh"}),
                ),
            )
        ]
        fresh = await agent.act()

        assert fresh.startswith("Status: success\n")
        assert agent.memory.messages[-1].tool_call_id == "fresh-call"
        assert audit_entries(audit) == [
            {"tool": "fail_read", "arguments": {}},
            {"tool": "echo_read", "arguments": {"text": "fresh"}}
        ]
    finally:
        await clients.disconnect()
