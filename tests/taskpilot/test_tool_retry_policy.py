import asyncio
import time
from unittest.mock import patch

import httpx
import pytest
from mcp import ClientSession


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
    from app.taskpilot.github_tools import (
        GitHubClient,
        GitHubRepositoryInfo,
        RepositoryContext,
    )
    from app.tool.base import BaseTool, ToolResult
    from app.tool.mcp import MCPClientTool
    from app.tool.tool_collection import ToolCollection


def github_tool(handler, *, timeout_seconds=1.0):
    client = GitHubClient(
        http_client=httpx.AsyncClient(
            base_url="https://api.github.com", transport=httpx.MockTransport(handler)
        )
    )
    return GitHubRepositoryInfo(
        context=RepositoryContext.parse("example/repo"),
        client=client,
        timeout_seconds=timeout_seconds,
    )


@pytest.mark.asyncio
async def test_transient_503_then_success_has_two_attempts():
    calls = []

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"message": "temporarily unavailable"})
        return httpx.Response(200, json={"full_name": "example/repo"})

    tool = github_tool(respond)
    result = await ToolCollection(tool).execute(name=tool.name, tool_input={})

    assert result.status == "success"
    assert len(calls) == 2
    assert [
        (item["number"], item["classification"], item["http_status"])
        for item in result.attempts
    ] == [
        (1, "transient", 503),
        (2, "success", None),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_transient_http_errors_exhaust_three_attempts(status):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={"message": "unavailable"})

    tool = github_tool(respond)
    result = await ToolCollection(tool).execute(name=tool.name, tool_input={})

    assert result.status == "failure"
    assert result.retry_classification == "transient"
    assert len(calls) == 3
    assert [item["number"] for item in result.attempts] == [1, 2, 3]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 422, 501, 505])
async def test_permanent_http_error_is_not_retried(status):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={"message": "denied"})

    tool = github_tool(respond)
    result = await ToolCollection(tool).execute(name=tool.name, tool_input={})

    assert result.status == "failure"
    assert result.retry_classification == "permanent"
    assert len(calls) == 1
    assert result.attempts[0]["classification"] == "permanent"


@pytest.mark.asyncio
async def test_connect_error_retries_but_response_decode_error_does_not():
    calls = []

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("connection reset")
        return httpx.Response(200, json={"full_name": "example/repo"})

    tool = github_tool(respond)
    result = await ToolCollection(tool).execute(name=tool.name, tool_input={})
    assert result.status == "success"
    assert len(calls) == 2

    bad_calls = []

    def bad_response(request):
        bad_calls.append(request)
        return httpx.Response(200, text="not JSON")

    bad_tool = github_tool(bad_response)
    bad_result = await ToolCollection(bad_tool).execute(
        name=bad_tool.name, tool_input={}
    )
    assert bad_result.status == "failure"
    assert len(bad_calls) == 1


@pytest.mark.asyncio
async def test_http_timeout_is_unknown_and_not_retried():
    calls = []

    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout("read deadline")

    tool = github_tool(respond)
    result = await ToolCollection(tool).execute(name=tool.name, tool_input={})

    assert result.status == "unknown"
    assert result.error_kind == "timeout"
    assert len(calls) == 1
    assert result.attempts[0]["classification"] == "unknown"


@pytest.mark.asyncio
async def test_schema_failure_has_zero_dispatch():
    calls = []
    tool = github_tool(
        lambda request: calls.append(request) or httpx.Response(200, json={})
    )

    result = await ToolCollection(tool).execute(
        name=tool.name, tool_input={"unknown": 1}
    )

    assert result.status == "failure"
    assert calls == []


class NonIdempotentTool(BaseTool):
    name: str = "non_idempotent"
    description: str = "A write-like operation"
    parameters: dict = {"type": "object", "additionalProperties": False}
    calls: int = 0

    async def execute(self, **kwargs):
        self.calls += 1
        return ToolResult(error="transient response", retry_classification="transient")


@pytest.mark.asyncio
async def test_non_idempotent_and_mcp_tools_do_not_retry_transient_results():
    local = NonIdempotentTool()
    local_result = await ToolCollection(local).execute(name=local.name, tool_input={})
    assert local_result.status == "failure"
    assert local.calls == 1

    class Session(ClientSession):
        def __init__(self):
            self.calls = 0

        async def call_tool(self, name, arguments):
            self.calls += 1
            raise httpx.ConnectError("temporary")

    session = Session()
    mcp = MCPClientTool(
        name="read_sounding_name",
        description="Fixture",
        parameters={"type": "object"},
        session=session,
        server_id="fixture",
        original_name="read_sounding_name",
    )
    mcp_result = await ToolCollection(mcp).execute(name=mcp.name, tool_input={})
    assert mcp_result.status == "failure"
    assert session.calls == 1


@pytest.mark.asyncio
async def test_retry_after_longer_than_tool_deadline_does_not_start_second_request():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            429, headers={"Retry-After": "30"}, json={"message": "rate limit"}
        )

    tool = github_tool(respond, timeout_seconds=0.05)
    result = await ToolCollection(tool).execute(name=tool.name, tool_input={})

    assert result.status == "failure"
    assert len(calls) == 1
    assert len(result.attempts) == 1


@pytest.mark.asyncio
async def test_retry_after_is_respected_when_it_fits_deadline():
    call_times = []

    def respond(request):
        call_times.append(time.monotonic())
        if len(call_times) == 1:
            return httpx.Response(
                429, headers={"Retry-After": "0.2"}, json={"message": "rate limit"}
            )
        return httpx.Response(200, json={"full_name": "example/repo"})

    tool = github_tool(respond)
    result = await ToolCollection(tool).execute(name=tool.name, tool_input={})

    assert result.status == "success"
    assert len(call_times) == 2
    assert call_times[1] - call_times[0] >= 0.19


@pytest.mark.asyncio
async def test_agent_observation_preserves_attempts_and_original_call_id():
    calls = []

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"message": "unavailable"})
        return httpx.Response(200, json={"full_name": "example/repo"})

    tool = github_tool(respond)
    agent = ToolCallAgent(available_tools=ToolCollection(tool))
    command = ToolCall(
        id="retry-call", function=Function(name=tool.name, arguments="{}")
    )
    agent.tool_calls = [command]

    observation = await agent.act()

    assert observation.startswith(
        "Status: success\nAttempts: 1:transient(503), 2:success\n"
    )
    assert agent.memory.messages[-1].tool_call_id == "retry-call"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_agent_budget_stops_backoff_before_second_request():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(503, json={"message": "unavailable"})

    tool = github_tool(respond)
    agent = ToolCallAgent(available_tools=ToolCollection(tool))
    agent._run_deadline = asyncio.get_running_loop().time() + 0.09
    command = ToolCall(
        id="budget-call", function=Function(name=tool.name, arguments="{}")
    )

    observation = await agent.execute_tool(command)

    assert observation.startswith("Status: unknown\nError kind: timeout\n")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_tool_timeout_during_backoff_stops_replay_and_records_attempt():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(503, json={"message": "unavailable"})

    tool = github_tool(respond, timeout_seconds=0.02)
    result = await ToolCollection(tool).execute(name=tool.name, tool_input={})

    assert result.status in {"failure", "unknown"}
    assert len(calls) == 1
    assert len(result.attempts) == 1


@pytest.mark.asyncio
async def test_cancellation_during_backoff_does_not_replay():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(503, json={"message": "unavailable"})

    tool = github_tool(respond)
    task = asyncio.create_task(
        ToolCollection(tool).execute(name=tool.name, tool_input={})
    )
    while not calls:
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) == 1
