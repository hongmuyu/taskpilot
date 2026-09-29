import copy
import json
from types import SimpleNamespace
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
    from app.agent.toolcall import ToolCallAgent
    from app.schema import Function, ToolCall
    from app.tool.base import BaseTool, ToolResult
    from app.tool.mcp import MCPClientTool
    from app.tool.tool_collection import ToolCollection


SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "count": {"type": "integer"},
        "ratio": {"type": "number"},
        "enabled": {"type": "boolean"},
        "meta": {
            "type": "object",
            "properties": {"owner": {"type": "string"}},
            "required": ["owner"],
            "additionalProperties": False,
        },
        "tags": {"type": "array", "items": {"type": "string"}},
        "mode": {"enum": ["fast", "slow"]},
    },
    "required": ["name", "count", "ratio", "enabled", "meta", "tags", "mode"],
    "additionalProperties": False,
}
VALID = {
    "name": "case",
    "count": 4,
    "ratio": 1.5,
    "enabled": True,
    "meta": {"owner": "octo"},
    "tags": ["bug"],
    "mode": "fast",
}


class SchemaProbe(BaseTool):
    name: str = "schema_probe"
    description: str = "Count schema-valid dispatches"
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
        return SimpleNamespace(content=[TextContent(type="text", text="executed")])


def call(name, arguments):
    return ToolCall(
        id="call_1", function=Function(name=name, arguments=json.dumps(arguments))
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates,missing,keyword",
    [
        ({}, "name", "required"),
        ({"name": None}, None, "type"),
        ({"name": 1}, None, "type"),
        ({"count": True}, None, "type"),
        ({"count": 4.5}, None, "type"),
        ({"count": "4"}, None, "type"),
        ({"ratio": True}, None, "type"),
        ({"ratio": "1.5"}, None, "type"),
        ({"enabled": "true"}, None, "type"),
        ({"meta": []}, None, "type"),
        ({"tags": {}}, None, "type"),
        ({"tags": [1]}, None, "type"),
        ({"mode": "unknown"}, None, "enum"),
        ({"extra": "value"}, None, "additionalProperties"),
        ({"meta": {}}, None, "required"),
        ({"meta": {"owner": "octo", "extra": 1}}, None, "additionalProperties"),
    ],
)
async def test_invalid_fields_never_dispatch(updates, missing, keyword):
    tool = SchemaProbe()
    agent = ToolCallAgent(available_tools=ToolCollection(tool))
    arguments = copy.deepcopy(VALID)
    arguments.update(updates)
    if missing:
        del arguments[missing]

    result = await agent.execute_tool(call(tool.name, arguments))

    assert "validation failed" in result
    assert keyword in result
    assert tool.calls == []


@pytest.mark.asyncio
async def test_valid_nested_fields_dispatch_once_without_coercion():
    tool = SchemaProbe()
    agent = ToolCallAgent(available_tools=ToolCollection(tool))

    result = await agent.execute_tool(call(tool.name, VALID))

    assert "executed" in result
    assert tool.calls == [VALID]


@pytest.mark.asyncio
async def test_boolean_does_not_match_numeric_enum():
    tool = SchemaProbe(
        parameters={"type": "object", "properties": {"n": {"enum": [0]}}}
    )
    agent = ToolCallAgent(available_tools=ToolCollection(tool))

    result = await agent.execute_tool(call(tool.name, {"n": False}))

    assert "enum" in result
    assert tool.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field_schema,bad_value,keyword",
    [
        ({"type": "array", "items": {"type": "string"}, "minItems": 1}, [], "minItems"),
        ({"type": "integer", "minimum": 1}, 0, "minimum"),
        ({"type": "integer", "maximum": 5}, 6, "maximum"),
        ({"anyOf": [{"type": "string"}, {"type": "integer"}]}, False, "anyOf"),
        (
            {"type": "object", "additionalProperties": {"type": "integer"}},
            {"item": "bad"},
            "type",
        ),
    ],
)
async def test_supported_existing_schema_keywords_are_enforced(
    field_schema, bad_value, keyword
):
    tool = SchemaProbe(
        parameters={
            "type": "object",
            "properties": {"value": field_schema},
            "required": ["value"],
        }
    )
    agent = ToolCallAgent(available_tools=ToolCollection(tool))

    result = await agent.execute_tool(call(tool.name, {"value": bad_value}))

    assert keyword in result
    assert tool.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "unknownRule": True},
        {"type": "object", "properties": {"name": {"pattern": "^a"}}},
        {"type": "object", "properties": {"tags": {"items": {"pattern": "^a"}}}},
        {"type": "object", "dependencies": {"run": ["target"]}},
        {"type": "object", "$schema": "https://json-schema.org/draft/2020-12/schema"},
    ],
)
async def test_unsupported_schema_keyword_is_reported_before_dispatch(schema):
    tool = SchemaProbe(parameters=schema)
    agent = ToolCallAgent(available_tools=ToolCollection(tool))

    result = await agent.execute_tool(call(tool.name, {}))

    assert "unsupported" in result.lower()
    assert tool.calls == []


@pytest.mark.asyncio
async def test_invalid_schema_is_rejected_before_dispatch():
    tool = SchemaProbe(parameters={"type": "object", "required": "name"})
    agent = ToolCallAgent(available_tools=ToolCollection(tool))

    result = await agent.execute_tool(call(tool.name, {}))

    assert "invalid parameter schema" in result.lower()
    assert tool.calls == []


@pytest.mark.asyncio
async def test_mcp_style_schema_rejects_invalid_field_before_remote_dispatch():
    session = RecordingSession()
    tool = MCPClientTool(
        name="mcp_probe",
        description="MCP schema probe",
        parameters=SCHEMA,
        session=session,
        server_id="fixture",
        original_name="probe",
    )
    agent = ToolCallAgent(available_tools=ToolCollection(tool))
    arguments = copy.deepcopy(VALID)
    arguments["count"] = "4"

    result = await agent.execute_tool(call(tool.name, arguments))

    assert "type" in result
    assert session.calls == []
