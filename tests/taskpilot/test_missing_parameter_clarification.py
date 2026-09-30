import json
from unittest.mock import patch

import pytest
from mcp import ClientSession
from mcp.types import TextContent
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
    from app.agent.repository_investigation import RepositoryInvestigationAgent
    from app.agent.toolcall import ToolCallAgent
    from app.schema import Function, ToolCall
    from app.taskpilot.github_tools import GitHubClient, RepositoryContext
    from app.tool.base import BaseTool, ToolResult
    from app.tool.mcp import MCPClientTool
    from app.tool.tool_collection import ToolCollection


SCHEMA = {
    "type": "object",
    "properties": {
        "repository": {"type": "string"},
        "path": {"type": "string"},
        "issue_number": {"type": "integer"},
    },
    "required": ["path"],
}


class CountingTool(BaseTool):
    name: str = "counting_tool"
    description: str = "Count real executions"
    parameters: dict = SCHEMA
    calls: list[dict] = Field(default_factory=list)

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return ToolResult(output="executed")


class RecordingSession(ClientSession):
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return [TextContent(type="text", text="executed")]


def call(arguments, call_id="missing-call"):
    return ToolCall(
        id=call_id,
        function=Function(name="counting_tool", arguments=json.dumps(arguments)),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "required,arguments,expected_fields",
    [
        (["path"], {}, ["path"]),
        (["repository", "path", "issue_number"], {}, ["repository", "path", "issue_number"]),
    ],
)
async def test_missing_fields_force_clarification_before_real_execution(
    monkeypatch, required, arguments, expected_fields
):
    tool = CountingTool(parameters={**SCHEMA, "required": required})
    agent = ToolCallAgent(available_tools=ToolCollection(tool))
    prompts = []

    def answer(prompt):
        assert tool.calls == []
        prompts.append(prompt)
        return "cancel"

    monkeypatch.setattr("builtins.input", answer)
    assert "ask_human" not in agent.available_tools.tool_map
    agent.tool_calls = [call(arguments)]

    result = await agent.act()

    assert len(prompts) == 1
    assert all(field in prompts[0] for field in expected_fields)
    assert "clarification" in result.lower()
    assert tool.calls == []
    assert agent.memory.messages[-1].tool_call_id == "missing-call"
    assert agent.memory.messages[-1].content == result


@pytest.mark.asyncio
async def test_trusted_repository_context_is_not_asked_again(monkeypatch):
    tool = CountingTool(
        parameters={
            **SCHEMA,
            "required": ["repository", "path"],
        }
    )
    agent = RepositoryInvestigationAgent.create(
        RepositoryContext.parse("octo-org/sample-repo"), client=GitHubClient()
    )
    agent.available_tools = ToolCollection(tool)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "README.md")
    agent.tool_calls = [call({})]

    try:
        result = await agent.act()
    finally:
        await agent.cleanup()

    assert len(prompts) == 1
    assert "path" in prompts[0]
    assert "repository" not in prompts[0]
    assert "clarification" in result.lower()
    assert tool.calls == []


@pytest.mark.asyncio
async def test_without_repository_context_asks_for_repository(monkeypatch):
    tool = CountingTool(
        parameters={
            **SCHEMA,
            "required": ["repository", "path"],
        }
    )
    agent = ToolCallAgent(available_tools=ToolCollection(tool))
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "cancel")

    await agent.execute_tool(call({}))

    assert len(prompts) == 1
    assert "repository" in prompts[0]
    assert "path" in prompts[0]
    assert tool.calls == []


@pytest.mark.asyncio
async def test_only_trusted_repository_field_missing_does_not_prompt_or_dispatch(
    monkeypatch,
):
    tool = CountingTool(parameters={**SCHEMA, "required": ["repository"]})
    agent = RepositoryInvestigationAgent.create(
        RepositoryContext.parse("octo-org/sample-repo"), client=GitHubClient()
    )
    agent.available_tools = ToolCollection(tool)

    def unexpected_prompt(prompt):
        pytest.fail("repository context was asked again")

    monkeypatch.setattr("builtins.input", unexpected_prompt)
    try:
        result = await agent.execute_tool(call({}))
    finally:
        await agent.cleanup()

    assert "required" in result
    assert "trusted context" in result
    assert tool.calls == []


@pytest.mark.asyncio
async def test_mcp_schema_missing_parameter_clarifies_without_remote_call(monkeypatch):
    session = RecordingSession()
    tool = MCPClientTool(
        name="mcp_probe",
        description="MCP schema probe",
        parameters={**SCHEMA, "required": ["issue_number"]},
        session=session,
        server_id="fixture",
        original_name="probe",
    )
    agent = ToolCallAgent(available_tools=ToolCollection(tool))
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "cancel")
    command = ToolCall(
        id="mcp-missing",
        function=Function(name=tool.name, arguments="{}"),
    )

    result = await agent.execute_tool(command)

    assert len(prompts) == 1 and "issue_number" in prompts[0]
    assert "clarification" in result.lower()
    assert session.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["cancel", "", "  ", "not a path"])
async def test_cancel_empty_or_invalid_reply_never_dispatches_original_tool(
    monkeypatch, reply
):
    tool = CountingTool()
    agent = ToolCallAgent(available_tools=ToolCollection(tool))
    monkeypatch.setattr("builtins.input", lambda prompt: reply)

    result = await agent.execute_tool(call({}))

    assert "clarification" in result.lower()
    assert tool.calls == []


@pytest.mark.asyncio
async def test_cancelled_input_stream_never_dispatches_original_tool(monkeypatch):
    tool = CountingTool()
    agent = ToolCallAgent(available_tools=ToolCollection(tool))

    def end_of_input(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", end_of_input)

    result = await agent.execute_tool(call({}))

    assert "clarification" in result.lower()
    assert tool.calls == []
