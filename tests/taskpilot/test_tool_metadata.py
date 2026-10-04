from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from mcp import ClientSession
from mcp.types import ListToolsResult, Tool
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
    from app.taskpilot.github_tools import (
        GitHubClient,
        GitHubCodeSearch,
        GitHubIssueDetail,
        GitHubIssueSearch,
        GitHubReadFile,
        GitHubRepositoryInfo,
        RepositoryContext,
    )
    from app.tool.base import BaseTool, ToolResult
    from app.tool.mcp import MCPClients
    from app.tool.tool_collection import ToolCollection


SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": False,
}


class LocalProbe(BaseTool):
    name: str = "local_probe"
    description: str = "Find matching records"
    parameters: dict = SCHEMA
    capabilities: tuple[str, ...] = ("record_search",)
    examples: tuple[str, ...] = ("Find recent records",)
    calls: list[dict] = Field(default_factory=list)

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return ToolResult(output="found")


class FixtureSession(ClientSession):
    def __init__(self, tools):
        self.fixture_tools = tools

    async def initialize(self):
        return SimpleNamespace(instructions="")

    async def list_tools(self):
        return ListToolsResult(tools=self.fixture_tools)


def mcp_tool(name, schema=SCHEMA):
    return Tool(name=name, description="Search remote records", inputSchema=schema)


async def discovered(server_id="fixture", names=("search.results",)):
    clients = MCPClients()
    clients.sessions[server_id] = FixtureSession([mcp_tool(name) for name in names])
    await clients._initialize_and_list_tools(server_id)
    return clients


@pytest.mark.asyncio
async def test_local_metadata_reuses_live_schema_and_does_not_gate_execution():
    tool = LocalProbe()
    collection = ToolCollection(tool)
    metadata = tool.metadata

    assert metadata.name == tool.name
    assert metadata.description == tool.description
    assert metadata.schema is tool.parameters
    assert metadata.capabilities == ("record_search",)
    assert metadata.examples == ("Find recent records",)
    assert metadata.source == "local"
    assert metadata.identity == "local:local_probe"
    assert metadata.original_name == "local_probe"
    assert metadata.server_id is None
    assert metadata.risk == {
        "status": "pending_phase_5",
        "write_allowed": False,
        "enforced": False,
    }
    assert collection.to_metadata()[0].identity == metadata.identity
    assert collection.to_params()[0]["function"]["parameters"] is tool.parameters

    result = await collection.execute(name=tool.name, tool_input={"query": "issue"})
    assert result.output == "found"
    assert tool.calls == [{"query": "issue"}]

    updated_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    tool.parameters = updated_schema
    assert metadata.schema is updated_schema
    assert collection.to_metadata()[0].schema is updated_schema
    assert collection.to_params()[0]["function"]["parameters"] is updated_schema


@pytest.mark.asyncio
async def test_mcp_metadata_preserves_raw_name_server_and_input_schema():
    clients = await discovered()
    tool = clients.get_tool("mcp_fixture_search_results")
    metadata = clients.to_metadata()[0]

    assert metadata.name == "mcp_fixture_search_results"
    assert metadata.description == "Search remote records"
    assert metadata.schema is tool.parameters
    assert metadata.source == "mcp"
    assert metadata.identity == "mcp:fixture:search.results"
    assert metadata.original_name == "search.results"
    assert metadata.server_id == "fixture"
    assert metadata.capabilities == ()
    assert metadata.examples == ()
    assert metadata.risk["write_allowed"] is False
    assert clients.to_params()[0]["function"]["parameters"] is tool.parameters

    updated_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    tool.parameters = updated_schema
    assert metadata.schema is updated_schema
    assert clients.to_params()[0]["function"]["parameters"] is updated_schema


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "names",
    [
        ("search.results", "search/results"),
        ("a" * 80 + "x", "a" * 80 + "y"),
    ],
)
async def test_mcp_sanitization_collisions_keep_both_original_identities(names):
    clients = await discovered(names=names)
    metadata = clients.to_metadata()

    assert len(metadata) == 2
    assert len({item.name for item in metadata}) == 2
    assert len({item.identity for item in metadata}) == 2
    assert {item.original_name for item in metadata} == set(names)
    assert all(len(item.name) <= 64 for item in metadata)
    assert all(
        item.source == "mcp" and item.server_id == "fixture" for item in metadata
    )
    assert {item.name for item in metadata} == set(clients.tool_map)


@pytest.mark.asyncio
async def test_mcp_server_ids_that_sanitize_to_same_name_keep_distinct_identities():
    clients = MCPClients()
    for server_id in ("team.one", "team/one"):
        clients.sessions[server_id] = FixtureSession([mcp_tool("search")])
        await clients._initialize_and_list_tools(server_id)

    metadata = clients.to_metadata()
    assert len(metadata) == 2
    assert {item.identity for item in metadata} == {
        "mcp:team.one:search",
        "mcp:team%2Fone:search",
    }
    assert len({item.name for item in metadata}) == 2


@pytest.mark.asyncio
async def test_local_mcp_public_name_collision_is_rejected_before_dispatch():
    clients = await discovered()
    local = LocalProbe(name="mcp_fixture_search_results")
    mcp = clients.get_tool(local.name)

    with pytest.raises(ValueError, match="Duplicate tool name"):
        ToolCollection(local, mcp)

    collection = ToolCollection(local)
    with pytest.raises(ValueError, match="Duplicate tool name"):
        collection.add_tool(mcp)

    assert collection.get_tool(local.name) is local
    assert local.calls == []


@pytest.mark.asyncio
async def test_github_local_tools_expose_capabilities_and_examples():
    client = GitHubClient(
        http_client=httpx.AsyncClient(base_url="https://example.test")
    )
    context = RepositoryContext.parse("octo/repo", source="user_input")
    tools = (
        GitHubRepositoryInfo(context=context, client=client),
        GitHubIssueSearch(context=context, client=client),
        GitHubIssueDetail(context=context, client=client),
        GitHubCodeSearch(context=context, client=client),
        GitHubReadFile(context=context, client=client),
    )
    try:
        metadata = ToolCollection(*tools).to_metadata()
        assert len(metadata) == 5
        assert all(item.source == "local" for item in metadata)
        assert all(item.capabilities and item.examples for item in metadata)
        assert all(
            item.schema is tool.parameters for item, tool in zip(metadata, tools)
        )
    finally:
        await client.aclose()
