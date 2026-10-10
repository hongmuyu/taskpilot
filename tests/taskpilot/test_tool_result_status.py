import json
from typing import Any
from unittest.mock import patch

import pytest
from mcp import ClientSession
from mcp.types import CallToolResult, ImageContent, TextContent
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
    from app.exceptions import ToolError
    from app.schema import AgentState, Function, ToolCall
    from app.tool.base import BaseTool, ToolResult
    from app.tool.mcp import MCPClientTool
    from app.tool.tool_collection import ToolCollection


class ResultProbe(BaseTool):
    name: str = "result_probe"
    description: str = "Return a fixed local result"
    parameters: dict = {"type": "object", "additionalProperties": False}
    response: Any = Field(default=None, exclude=True)
    exception: Exception | None = Field(default=None, exclude=True)

    async def execute(self, **kwargs):
        if self.exception:
            raise self.exception
        return self.response


class FakeMCPSession(ClientSession):
    def __init__(self, response):
        self.response = response

    async def call_tool(self, name, arguments):
        return self.response


def tool_call(name="result_probe", call_id="call_status"):
    return ToolCall(id=call_id, function=Function(name=name, arguments=json.dumps({})))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,expected",
    [
        (ToolResult(output="ok"), "success"),
        (ToolResult(error="business rejected"), "failure"),
        (ToolResult(output="ok", error="business rejected"), "failure"),
        (ToolResult(output="Error: this is only text"), "success"),
        ("plain text", "unknown"),
        ("Error: this is only text", "unknown"),
        ({"message": "structured"}, "unknown"),
        (ToolResult(), "unknown"),
    ],
)
async def test_local_result_status_is_normalized_before_observation(response, expected):
    collection = ToolCollection(ResultProbe(response=response))
    result = await collection.execute(name="result_probe", tool_input={})

    assert isinstance(result, ToolResult)
    assert result.status == expected

    agent = ToolCallAgent(available_tools=collection)
    observation = await agent.execute_tool(tool_call())
    assert observation.startswith(f"Status: {expected}\n")
    if expected == "failure":
        assert "business rejected" in observation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,expected",
    [
        (
            CallToolResult(
                content=[TextContent(type="text", text="all good")],
                isError=True,
            ),
            "failure",
        ),
        (
            CallToolResult(
                content=[TextContent(type="text", text="Error: only a label")],
                isError=False,
            ),
            "success",
        ),
        (
            CallToolResult(content=[TextContent(type="text", text="unmarked")]),
            "unknown",
        ),
    ],
)
async def test_mcp_is_error_controls_status_even_without_exception(response, expected):
    tool = MCPClientTool(
        name="remote_probe",
        description="remote fixture",
        parameters={"type": "object", "additionalProperties": False},
        session=FakeMCPSession(response),
        server_id="fixture",
        original_name="remote_probe",
    )
    collection = ToolCollection(tool)
    result = await collection.execute(name="remote_probe", tool_input={})

    assert result.status == expected
    agent = ToolCallAgent(available_tools=collection)
    observation = await agent.execute_tool(tool_call("remote_probe"))
    assert observation.startswith(f"Status: {expected}\n")
    assert response.content[0].text in observation


@pytest.mark.asyncio
async def test_mcp_error_keeps_multiple_messages_and_image():
    response = CallToolResult(
        content=[
            TextContent(type="text", text="first cause"),
            ImageContent(type="image", data="cG5n", mimeType="image/png"),
            TextContent(type="text", text="second cause"),
        ],
        isError=True,
    )
    tool = MCPClientTool(
        name="remote_probe",
        description="remote fixture",
        parameters={"type": "object", "additionalProperties": False},
        session=FakeMCPSession(response),
        server_id="fixture",
        original_name="remote_probe",
    )

    result = await ToolCollection(tool).execute(name="remote_probe", tool_input={})

    assert result.status == "failure"
    assert "first cause" in result.error
    assert "second cause" in result.error
    assert result.base64_image == "cG5n"


@pytest.mark.asyncio
async def test_schema_validation_failure_has_status_before_stringification():
    result = await ToolCollection(ResultProbe()).execute(
        name="result_probe", tool_input=["not an object"]
    )

    assert result.status == "failure"
    assert "JSON object" in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("output,rendered", [(0, "0"), (False, "False")])
async def test_tool_result_falsey_scalar_output_is_preserved(output, rendered):
    agent = ToolCallAgent(
        available_tools=ToolCollection(ResultProbe(response=ToolResult(output=output)))
    )

    observation = await agent.execute_tool(tool_call())

    assert observation.startswith("Status: success\n")
    assert observation.endswith(f"\n{rendered}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception", [ToolError("tool rejected"), ValueError("tool crashed")]
)
async def test_local_exception_becomes_failure_observation(exception):
    agent = ToolCallAgent(
        available_tools=ToolCollection(ResultProbe(exception=exception))
    )

    observation = await agent.execute_tool(tool_call())

    assert observation.startswith("Status: failure\n")
    assert str(exception) in observation


@pytest.mark.asyncio
async def test_truncated_tool_message_keeps_failure_status_and_call_id():
    agent = ToolCallAgent(
        available_tools=ToolCollection(
            ResultProbe(response=ToolResult(error="long failure " * 30))
        ),
        max_observe=85,
    )
    agent.tool_calls = [tool_call(call_id="original_call")]

    observation = await agent.act()

    assert observation.startswith("Status: failure\n")
    assert "long failure" in observation
    assert len(observation) == 85
    assert agent.memory.messages[-1].tool_call_id == "original_call"
    assert agent.memory.messages[-1].content == observation


@pytest.mark.asyncio
async def test_failed_special_tool_does_not_finish_agent():
    agent = ToolCallAgent(
        available_tools=ToolCollection(
            ResultProbe(response=ToolResult(error="control action failed"))
        ),
        special_tool_names=["result_probe"],
    )

    observation = await agent.execute_tool(tool_call())

    assert observation.startswith("Status: failure\n")
    assert agent.state != AgentState.FINISHED
