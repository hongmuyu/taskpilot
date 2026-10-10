import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from mcp import ClientSession
from mcp.types import CallToolResult, ListToolsResult, TextContent
from pydantic import Field


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
    from app.llm import LLM
    from app.schema import AgentState, Function, ToolCall
    from app.tool.base import BaseTool, ToolResult
    from app.tool.mcp import MCPClients, MCPClientTool
    from app.tool.tool_collection import ToolCollection


class SlowLocalTool(BaseTool):
    name: str = "slow_local"
    description: str = "A controlled local call"
    parameters: dict = {"type": "object", "additionalProperties": False}
    calls: list[str] = Field(default_factory=list)
    delay: float = 0.05

    async def execute(self, **kwargs):
        self.calls.append("started")
        await asyncio.sleep(self.delay)
        return ToolResult(output="completed")


class SlowMCPSession(ClientSession):
    def __init__(self, delay=0.05):
        self.delay = delay
        self.calls = 0

    async def call_tool(self, name, arguments):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return CallToolResult(
            content=[TextContent(type="text", text="completed")], isError=False
        )


def call(call_id="call_timeout"):
    return ToolCall(
        id=call_id,
        function=Function(name="slow_local", arguments=json.dumps({})),
    )


@pytest.mark.asyncio
async def test_slow_local_tool_returns_unknown_timeout_without_retry():
    tool = SlowLocalTool(timeout_seconds=0.01)

    result = await ToolCollection(tool).execute(name=tool.name, tool_input={})

    assert result.status == "unknown"
    assert result.error_kind == "timeout"
    assert "timed out" in str(result)
    assert "outcome unknown" in str(result)
    assert tool.calls == ["started"]


@pytest.mark.asyncio
async def test_slow_mcp_tool_returns_unknown_timeout_observation():
    session = SlowMCPSession()
    tool = MCPClientTool(
        name="remote_slow",
        description="Controlled MCP call",
        parameters={"type": "object", "additionalProperties": False},
        session=session,
        server_id="fixture",
        original_name="remote_slow",
        timeout_seconds=0.01,
    )
    agent = ToolCallAgent(available_tools=ToolCollection(tool))
    command = ToolCall(
        id="remote_call", function=Function(name=tool.name, arguments="{}")
    )

    observation = await agent.execute_tool(command)

    assert observation.startswith("Status: unknown\n")
    assert "timeout" in observation.lower()
    assert "outcome unknown" in observation.lower()
    assert session.calls == 1


@pytest.mark.asyncio
async def test_mcp_session_timeout_is_not_reported_as_failure():
    class TimedOutSession(SlowMCPSession):
        async def call_tool(self, name, arguments):
            raise TimeoutError("remote request deadline")

    tool = MCPClientTool(
        name="remote_slow",
        description="Controlled MCP call",
        parameters={"type": "object", "additionalProperties": False},
        session=TimedOutSession(),
        server_id="fixture",
        original_name="remote_slow",
    )

    result = await ToolCollection(tool).execute(name=tool.name, tool_input={})

    assert result.status == "unknown"
    assert result.error_kind == "timeout"


class SlowContext:
    async def __aenter__(self):
        await asyncio.sleep(0.05)

    async def __aexit__(self, *args):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["sse", "stdio"])
async def test_mcp_connection_timeout_releases_partial_state(monkeypatch, transport):
    if transport == "sse":
        monkeypatch.setattr("app.tool.mcp.sse_client", lambda url: SlowContext())
    else:
        monkeypatch.setattr("app.tool.mcp.stdio_client", lambda params: SlowContext())
    clients = MCPClients(connect_timeout_seconds=0.01, cleanup_timeout_seconds=0.01)

    with pytest.raises(TimeoutError, match="connection.*timed out"):
        if transport == "sse":
            await clients.connect_sse("http://127.0.0.1:9", "fixture")
        else:
            await clients.connect_stdio("fixture-command", [], "fixture")

    assert clients.sessions == {}
    assert clients.exit_stacks == {}
    assert clients.tool_map == {}


@pytest.mark.asyncio
async def test_mcp_timeout_after_session_entry_closes_partial_connection(monkeypatch):
    class FastContext:
        async def __aenter__(self):
            return (None, None)

        async def __aexit__(self, *args):
            return None

    class FakeSessionContext:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr("app.tool.mcp.sse_client", lambda url: FastContext())
    monkeypatch.setattr("app.tool.mcp.ClientSession", FakeSessionContext)
    clients = MCPClients(connect_timeout_seconds=0.01)

    async def slow_initialize(*args):
        await asyncio.sleep(0.05)

    monkeypatch.setattr(clients, "_initialize_and_list_tools", slow_initialize)

    with pytest.raises(TimeoutError, match="connection.*timed out"):
        await clients.connect_sse("http://127.0.0.1:9", "fixture")

    assert clients.sessions == {}
    assert clients.exit_stacks == {}


@pytest.mark.asyncio
async def test_cancelled_mcp_connection_propagates_and_clears_partial_state(
    monkeypatch,
):
    monkeypatch.setattr("app.tool.mcp.sse_client", lambda url: SlowContext())
    clients = MCPClients(connect_timeout_seconds=1.0)
    task = asyncio.create_task(clients.connect_sse("http://127.0.0.1:9", "fixture"))
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert clients.sessions == {}
    assert clients.exit_stacks == {}


@pytest.mark.asyncio
async def test_normal_mcp_connection_and_disconnect_remain_available(monkeypatch):
    class FastContext:
        async def __aenter__(self):
            return (None, None)

        async def __aexit__(self, *args):
            return None

    class FakeSessionContext:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def initialize(self):
            return SimpleNamespace(instructions="")

        async def list_tools(self):
            return ListToolsResult(tools=[])

    monkeypatch.setattr("app.tool.mcp.sse_client", lambda url: FastContext())
    monkeypatch.setattr("app.tool.mcp.ClientSession", FakeSessionContext)
    clients = MCPClients(connect_timeout_seconds=0.1, cleanup_timeout_seconds=0.1)

    await clients.connect_sse("http://127.0.0.1:9", "fixture")
    assert "fixture" in clients.sessions
    await clients.disconnect("fixture")
    assert clients.sessions == {}
    assert clients.exit_stacks == {}


@pytest.mark.asyncio
async def test_mcp_disconnect_timeout_is_bounded_and_clears_local_state():
    class SlowStack:
        async def aclose(self):
            await asyncio.Event().wait()

    clients = MCPClients(cleanup_timeout_seconds=0.01)
    clients.exit_stacks["fixture"] = SlowStack()
    clients.sessions["fixture"] = SlowMCPSession()

    await asyncio.wait_for(clients.disconnect("fixture"), timeout=0.2)

    assert clients.sessions == {}
    assert clients.exit_stacks == {}


@pytest.mark.asyncio
async def test_agent_budget_exhaustion_prevents_next_dispatch():
    tool = SlowLocalTool(timeout_seconds=1.0)
    agent = ToolCallAgent(available_tools=ToolCollection(tool))
    agent._run_deadline = asyncio.get_running_loop().time() + 0.01
    agent.tool_calls = [call("first"), call("second")]

    observation = await agent.act()

    assert "Status: unknown" in observation
    assert "execution budget exhausted" in observation.lower()
    assert tool.calls == ["started"]
    assert [message.tool_call_id for message in agent.memory.messages] == [
        "first",
        "second",
    ]


@pytest.mark.asyncio
async def test_run_deadline_blocks_tool_selected_after_slow_model_reply(monkeypatch):
    tool = SlowLocalTool()
    llm = object.__new__(LLM)

    async def delayed_reply(**kwargs):
        await asyncio.sleep(0.03)
        return SimpleNamespace(content=None, tool_calls=[call("late")])

    llm.ask_tool = AsyncMock(side_effect=delayed_reply)
    monkeypatch.setattr("app.agent.base.SANDBOX_CLIENT.cleanup", AsyncMock())
    agent = ToolCallAgent(
        llm=llm,
        available_tools=ToolCollection(tool),
        next_step_prompt="",
        run_timeout_seconds=0.01,
        cleanup_timeout_seconds=0.2,
        max_steps=2,
    )

    result = await agent.run("run late call")

    assert "execution budget exhausted" in result.lower()
    assert tool.calls == []


@pytest.mark.asyncio
async def test_external_cancellation_propagates_without_a_second_dispatch():
    tool = SlowLocalTool(timeout_seconds=1.0)
    agent = ToolCallAgent(available_tools=ToolCollection(tool))
    agent.tool_calls = [call("first"), call("second")]
    task = asyncio.create_task(agent.act())
    while not tool.calls:
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tool.calls == ["started"]
    observation = agent.memory.messages[-1]
    assert observation.tool_call_id == "first"
    assert observation.content.startswith("Status: unknown\nError kind: cancelled\n")


@pytest.mark.asyncio
async def test_timed_out_control_tool_does_not_finish_agent():
    tool = SlowLocalTool(timeout_seconds=0.01)
    agent = ToolCallAgent(
        available_tools=ToolCollection(tool), special_tool_names=[tool.name]
    )

    observation = await agent.execute_tool(call())

    assert observation.startswith("Status: unknown\nError kind: timeout\n")
    assert agent.state != AgentState.FINISHED


class SlowCleanupTool(SlowLocalTool):
    async def cleanup(self):
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_cleanup_timeout_is_bounded():
    agent = ToolCallAgent(
        available_tools=ToolCollection(SlowCleanupTool()),
        cleanup_timeout_seconds=0.01,
    )

    await asyncio.wait_for(agent.cleanup(), timeout=0.2)


@pytest.mark.asyncio
async def test_run_budget_bounds_base_cleanup(monkeypatch):
    async def slow_cleanup():
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "app.agent.base.SANDBOX_CLIENT.cleanup", AsyncMock(side_effect=slow_cleanup)
    )
    agent = ToolCallAgent(
        max_steps=0, run_timeout_seconds=0.01, cleanup_timeout_seconds=0.01
    )

    result = await asyncio.wait_for(agent.run("finish"), timeout=0.2)

    assert result.startswith("Status: unknown\n")


@pytest.mark.asyncio
async def test_normal_tool_execution_remains_successful():
    tool = SlowLocalTool(delay=0, timeout_seconds=0.1)
    agent = ToolCallAgent(available_tools=ToolCollection(tool))

    observation = await agent.execute_tool(call())

    assert observation.startswith("Status: success\n")
    assert "completed" in observation
    assert tool.calls == ["started"]
