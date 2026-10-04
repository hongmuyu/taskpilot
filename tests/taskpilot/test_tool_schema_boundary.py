import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from mcp import ClientSession
from mcp.types import ListToolsResult, TextContent, Tool
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
    from app.schema import Function, ToolCall
    from app.taskpilot.github_tools import (
        GitHubClient,
        GitHubIssueDetail,
        RepositoryContext,
    )
    from app.tool.base import BaseTool, ToolResult
    from app.tool.mcp import MCPClients
    from app.tool.tool_collection import ToolCollection


class LocalProbe(BaseTool):
    name: str = "local_probe"
    description: str = "Count local dispatches"
    parameters: dict = {"type": "object", "properties": {"query": {"type": "string"}}}
    calls: list[dict] = Field(default_factory=list)

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return ToolResult(output="local result")


class FakeSession(ClientSession):
    def __init__(self):
        self.calls = []

    async def initialize(self):
        return SimpleNamespace(instructions="")

    async def list_tools(self):
        return ListToolsResult(
            tools=[
                Tool(
                    name="remote_probe",
                    description="Count MCP dispatches",
                    inputSchema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                )
            ]
        )

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(content=[TextContent(type="text", text="remote result")])


def tool_call(name, arguments):
    return ToolCall(
        id="call_1", function=Function(name=name, arguments=json.dumps(arguments))
    )


@pytest.mark.asyncio
async def test_local_agent_rejects_non_object_before_tool_dispatch():
    tool = LocalProbe()
    agent = ToolCallAgent(available_tools=ToolCollection(tool))

    result = await agent.execute_tool(tool_call(tool.name, ["wrong shape"]))

    assert "json object" in result.lower()
    assert tool.calls == []


@pytest.mark.asyncio
async def test_local_agent_dispatches_valid_object_exactly_once():
    tool = LocalProbe()
    agent = ToolCallAgent(available_tools=ToolCollection(tool))

    result = await agent.execute_tool(tool_call(tool.name, {"query": "issue"}))

    assert "local result" in result
    assert tool.calls == [{"query": "issue"}]


@pytest.mark.asyncio
async def test_mcp_schema_path_rejects_non_object_before_remote_dispatch():
    session = FakeSession()
    clients = MCPClients()
    clients.sessions["fixture"] = session
    await clients._initialize_and_list_tools("fixture")
    agent = ToolCallAgent(available_tools=clients)

    result = await agent.execute_tool(tool_call("mcp_fixture_remote_probe", [1]))

    assert "json object" in result.lower()
    assert session.calls == []


@pytest.mark.asyncio
async def test_mcp_schema_path_dispatches_valid_object_exactly_once():
    session = FakeSession()
    clients = MCPClients()
    clients.sessions["fixture"] = session
    await clients._initialize_and_list_tools("fixture")
    agent = ToolCallAgent(available_tools=clients)

    result = await agent.execute_tool(
        tool_call("mcp_fixture_remote_probe", {"query": "issue"})
    )

    assert "remote result" in result
    assert session.calls == [("remote_probe", {"query": "issue"})]


@pytest.mark.asyncio
async def test_collection_rejects_non_object_schema_without_tool_dispatch():
    tool = LocalProbe(parameters={"type": "array"})
    collection = ToolCollection(tool)

    result = await collection.execute(name=tool.name, tool_input={"query": "issue"})

    assert "validation" in str(result).lower()
    assert tool.calls == []


@pytest.mark.asyncio
async def test_existing_tool_semantic_validation_still_runs():
    client = GitHubClient(
        http_client=httpx.AsyncClient(
            base_url="https://api.github.com",
            transport=httpx.MockTransport(
                lambda request: pytest.fail("invalid issue number made an HTTP request")
            ),
        )
    )
    tool = GitHubIssueDetail(
        context=RepositoryContext.parse("octo-org/sample-repo"), client=client
    )
    agent = ToolCallAgent(available_tools=ToolCollection(tool))

    try:
        result = await agent.execute_tool(tool_call(tool.name, {"issue_number": -1}))
    finally:
        await client.aclose()

    assert "positive integer" in result
