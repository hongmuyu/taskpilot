import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

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
    from app.tool.base import BaseTool, ToolResult
    from app.tool.mcp import MCPClients
    from app.tool.tool_collection import ToolCollection


SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "count": {"type": "integer"},
        "mode": {"type": "string", "enum": ["fast", "safe"]},
    },
    "required": ["query", "count", "mode"],
    "additionalProperties": False,
}
VALID = {"query": "issue", "count": 2, "mode": "fast"}


class LocalProbe(BaseTool):
    name: str = "local_schema_probe"
    description: str = "Local schema fixture"
    parameters: dict = SCHEMA
    calls: list[dict] = Field(default_factory=list)

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return ToolResult(output="local executed")


class FixtureSession(ClientSession):
    def __init__(self, schema):
        self.schema = schema
        self.calls = []

    async def initialize(self):
        return SimpleNamespace(instructions="")

    async def list_tools(self):
        return ListToolsResult(
            tools=[
                Tool(
                    name="search.results",
                    description="MCP schema fixture",
                    inputSchema=self.schema,
                )
            ]
        )

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(content=[TextContent(type="text", text="mcp executed")])


def call(name, arguments, call_id="fixture-call"):
    return ToolCall(
        id=call_id,
        function=Function(name=name, arguments=json.dumps(arguments)),
    )


async def fixtures(schema=None):
    schema = copy.deepcopy(schema or SCHEMA)
    local = LocalProbe(parameters=copy.deepcopy(schema))
    session = FixtureSession(schema)
    clients = MCPClients()
    clients.sessions["fixture"] = session
    await clients._initialize_and_list_tools("fixture")
    return local, ToolCollection(local), session, clients


@pytest.mark.asyncio
async def test_local_parameters_and_mcp_input_schema_map_to_same_validator():
    local, local_collection, session, clients = await fixtures()
    mcp_name = "mcp_fixture_search_results"
    mcp_tool = clients.get_tool(mcp_name)

    assert local_collection.to_params()[0]["function"]["parameters"] == SCHEMA
    assert clients.to_params()[0]["function"]["parameters"] == SCHEMA
    assert clients.to_params()[0]["function"]["name"] == mcp_name
    assert mcp_tool.parameters == session.schema
    assert mcp_tool.original_name == "search.results"

    local_agent = ToolCallAgent(available_tools=local_collection)
    mcp_agent = ToolCallAgent(available_tools=clients)
    assert "local executed" in await local_agent.execute_tool(call(local.name, VALID))
    assert "mcp executed" in await mcp_agent.execute_tool(call(mcp_name, VALID))
    assert local.calls == [VALID]
    assert session.calls == [("search.results", VALID)]

    unknown = await mcp_agent.execute_tool(call("search.results", VALID, "unknown"))
    assert "Unknown tool" in unknown
    assert session.calls == [("search.results", VALID)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments,keyword",
    [
        ({"count": 2, "mode": "fast"}, "required"),
        ({"query": "issue", "count": "2", "mode": "fast"}, "type"),
        ({"query": "issue", "count": True, "mode": "fast"}, "type"),
        ({"query": "issue", "count": 2, "mode": "unknown"}, "enum"),
        (
            {"query": "issue", "count": 2, "mode": "fast", "extra": 1},
            "additionalProperties",
        ),
    ],
)
async def test_invalid_local_and_mcp_arguments_have_zero_dispatch(
    monkeypatch, arguments, keyword
):
    monkeypatch.setattr("builtins.input", lambda prompt: "cancel")
    local, local_collection, session, clients = await fixtures()
    local_agent = ToolCallAgent(available_tools=local_collection)
    mcp_agent = ToolCallAgent(available_tools=clients)

    local_result = await local_agent.execute_tool(call(local.name, arguments))
    mcp_result = await mcp_agent.execute_tool(
        call("mcp_fixture_search_results", arguments)
    )

    assert keyword in local_result
    assert keyword in mcp_result
    assert local.calls == []
    assert session.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsupported_schema,keyword",
    [
        ({"properties": {"query": {"type": "string", "pattern": "^a"}}}, "pattern"),
        ({"oneOf": [{"type": "object"}]}, "oneOf"),
        ({"$schema": "https://json-schema.org/draft/2020-12/schema"}, "$schema"),
    ],
)
async def test_unsupported_schema_feature_is_reported_before_either_dispatch(
    unsupported_schema, keyword
):
    schema = copy.deepcopy(SCHEMA)
    schema.update(unsupported_schema)
    local, local_collection, session, clients = await fixtures(schema)
    local_agent = ToolCallAgent(available_tools=local_collection)
    mcp_agent = ToolCallAgent(available_tools=clients)

    local_result = await local_agent.execute_tool(call(local.name, VALID))
    mcp_result = await mcp_agent.execute_tool(call("mcp_fixture_search_results", VALID))

    assert "unsupported" in local_result.lower() and keyword in local_result
    assert "unsupported" in mcp_result.lower() and keyword in mcp_result
    assert local.calls == []
    assert session.calls == []
