from unittest.mock import patch

import pytest
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
    from app.tool.tool_collection import ToolCollection


class CountingTool(BaseTool):
    name: str = "counting_tool"
    description: str = "Count real tool executions"
    parameters: dict = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    calls: list[dict] = Field(default_factory=list)

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return ToolResult(output="executed")


class CountingCollection(ToolCollection):
    def __init__(self, *tools):
        super().__init__(*tools)
        self.dispatches = 0

    async def execute(self, *, name, tool_input=None):
        self.dispatches += 1
        return await super().execute(name=name, tool_input=tool_input)


def call(raw_arguments, call_id):
    return ToolCall(
        id=call_id,
        function=Function(name="counting_tool", arguments=raw_arguments),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_arguments,error_kind",
    [
        ("{bad", "Invalid JSON"),
        ('{"value":', "Invalid JSON"),
        ("", "Invalid JSON"),
        ('{"value": NaN}', "Invalid JSON"),
        ('{"value": Infinity}', "Invalid JSON"),
        ("[]", "JSON object"),
        ("null", "JSON object"),
        ("42", "JSON object"),
        ('"value"', "JSON object"),
        ("true", "JSON object"),
    ],
)
async def test_invalid_arguments_never_enter_dispatch_and_keep_call_id(
    raw_arguments, error_kind
):
    tool = CountingTool()
    collection = CountingCollection(tool)
    agent = ToolCallAgent(available_tools=collection)
    agent.tool_calls = [call(raw_arguments, "invalid-call")]

    result = await agent.act()

    assert result.startswith("Error:")
    assert error_kind in result
    assert collection.dispatches == 0
    assert tool.calls == []
    message = agent.memory.messages[-1]
    assert message.tool_call_id == "invalid-call"
    assert message.name == tool.name
    assert message.content == result


@pytest.mark.asyncio
async def test_empty_object_reaches_schema_and_executes_when_allowed():
    tool = CountingTool(
        parameters={"type": "object", "properties": {}, "additionalProperties": False}
    )
    collection = CountingCollection(tool)
    agent = ToolCallAgent(available_tools=collection)
    agent.tool_calls = [call("{}", "empty-valid")]

    result = await agent.act()

    assert "executed" in result
    assert collection.dispatches == 1
    assert tool.calls == [{}]
    assert agent.memory.messages[-1].tool_call_id == "empty-valid"


@pytest.mark.asyncio
async def test_empty_object_reaches_schema_and_stays_unexecuted_when_required(
    monkeypatch,
):
    monkeypatch.setattr("builtins.input", lambda prompt: "cancel")
    tool = CountingTool()
    collection = CountingCollection(tool)
    agent = ToolCallAgent(available_tools=collection)
    agent.tool_calls = [call("{}", "empty-invalid")]

    result = await agent.act()

    assert "required" in result
    assert collection.dispatches == 1
    assert tool.calls == []
    assert agent.memory.messages[-1].tool_call_id == "empty-invalid"


@pytest.mark.asyncio
async def test_corrected_call_executes_once_after_invalid_json():
    tool = CountingTool()
    collection = CountingCollection(tool)
    agent = ToolCallAgent(available_tools=collection)
    agent.tool_calls = [call('{"value":', "first-invalid")]

    first = await agent.act()
    agent.tool_calls = [call('{"value": "fixed"}', "second-valid")]
    second = await agent.act()

    assert "Invalid JSON" in first
    assert "executed" in second
    assert collection.dispatches == 1
    assert tool.calls == [{"value": "fixed"}]
    assert [message.tool_call_id for message in agent.memory.messages] == [
        "first-invalid",
        "second-valid",
    ]
