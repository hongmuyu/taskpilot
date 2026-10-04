import asyncio
import json
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
    from app.tool.base import BaseTool, ToolFailure, ToolResult
    from app.tool.tool_collection import ToolCollection


class CountingTool(BaseTool):
    name: str = "resume_probe"
    description: str = "Count actual tool executions"
    parameters: dict = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }
    calls: list[dict] = Field(default_factory=list)
    delay: float = 0
    business_failure: bool = False

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.business_failure:
            return ToolFailure(error="business validation rejected")
        return ToolResult(output="executed")


class CountingCollection(ToolCollection):
    def __init__(self, *tools):
        super().__init__(*tools)
        self.validation_entries = 0

    async def execute(self, *, name, tool_input=None):
        self.validation_entries += 1
        return await super().execute(name=name, tool_input=tool_input)


def call(call_id, arguments=None):
    return ToolCall(
        id=call_id,
        function=Function(name="resume_probe", arguments=json.dumps(arguments or {})),
    )


def agent_with_tool(**tool_kwargs):
    tool = CountingTool(**tool_kwargs)
    collection = CountingCollection(tool)
    return ToolCallAgent(available_tools=collection), tool, collection


@pytest.mark.asyncio
async def test_valid_reply_revalidates_and_dispatches_once_with_original_call_id(
    monkeypatch,
):
    agent, tool, collection = agent_with_tool()
    monkeypatch.setattr("builtins.input", lambda prompt: "README.md")
    agent.tool_calls = [call("original-id")]

    result = await agent.act()

    assert "executed" in result
    assert collection.validation_entries == 2
    assert tool.calls == [{"path": "README.md"}]
    assert "original-id" not in agent.pending_tool_calls
    messages = [m for m in agent.memory.messages if m.tool_call_id == "original-id"]
    assert len(messages) == 1
    assert messages[0].name == tool.name
    assert "executed" in messages[0].content


@pytest.mark.asyncio
async def test_invalid_then_valid_reply_stays_pending_until_same_validator_accepts(
    monkeypatch,
):
    agent, tool, collection = agent_with_tool()
    answers = iter(['{"path":7}', '{"path":"README.md"}'])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    result = await agent.execute_tool(call("original-id"))

    assert "executed" in result
    assert collection.validation_entries == 3
    assert tool.calls == [{"path": "README.md"}]
    assert agent.pending_tool_calls == {}


@pytest.mark.asyncio
async def test_two_invalid_then_valid_replies_resume_in_same_cli_call(monkeypatch):
    agent, tool, collection = agent_with_tool()
    answers = iter(['{"path":7}', '{"path":false}', '{"path":"README.md"}'])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    result = await agent.execute_tool(call("original-id"))

    assert "executed" in result
    assert collection.validation_entries == 4
    assert tool.calls == [{"path": "README.md"}]
    assert agent.pending_tool_calls == {}


@pytest.mark.asyncio
async def test_two_invalid_replies_then_later_valid_reply_updates_original_observation(
    monkeypatch,
):
    agent, tool, collection = agent_with_tool()
    answers = iter(['{"path":7}', '{"path":false}', '{"path":7}'])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    agent.tool_calls = [call("original-id")]

    first = await agent.act()
    assert "pending" in first.lower()
    assert collection.validation_entries == 4
    assert tool.calls == []
    assert "original-id" in agent.pending_tool_calls

    final = await agent.submit_clarification_reply(
        "original-id", '{"path":"README.md"}'
    )

    assert "executed" in final
    assert collection.validation_entries == 5
    assert tool.calls == [{"path": "README.md"}]
    messages = [m for m in agent.memory.messages if m.tool_call_id == "original-id"]
    assert len(messages) == 1
    assert "executed" in messages[0].content


@pytest.mark.asyncio
async def test_duplicate_and_late_replies_do_not_dispatch_again(monkeypatch):
    agent, tool, collection = agent_with_tool()
    monkeypatch.setattr("builtins.input", lambda prompt: "README.md")
    await agent.execute_tool(call("original-id"))

    duplicate = await agent.submit_clarification_reply(
        "original-id", '{"path":"second.py"}'
    )
    repeated_call = await agent.execute_tool(call("original-id", {"path": "third.py"}))
    agent.max_steps = 0
    await agent.run("next task")
    late = await agent.submit_clarification_reply("original-id", '{"path":"late.py"}')

    assert "executed" not in duplicate + repeated_call + late
    assert tool.calls == [{"path": "README.md"}]
    assert collection.validation_entries == 2


@pytest.mark.asyncio
async def test_cancelled_pending_call_cannot_resume(monkeypatch):
    agent, tool, collection = agent_with_tool()
    monkeypatch.setattr("builtins.input", lambda prompt: "cancel")
    await agent.execute_tool(call("cancelled-id"))

    late = await agent.submit_clarification_reply(
        "cancelled-id", '{"path":"README.md"}'
    )
    repeated_call = await agent.execute_tool(
        call("cancelled-id", {"path": "README.md"})
    )

    assert "executed" not in late + repeated_call
    assert tool.calls == []
    assert collection.validation_entries == 1


@pytest.mark.asyncio
async def test_parallel_tool_calls_keep_ids_and_arguments_separate(monkeypatch):
    agent, tool, collection = agent_with_tool()
    answers = iter(["a.py", "b.py"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    agent.tool_calls = [call("call-a"), call("call-b")]

    await agent.act()

    assert collection.validation_entries == 4
    assert tool.calls == [{"path": "a.py"}, {"path": "b.py"}]
    assert [m.tool_call_id for m in agent.memory.messages] == ["call-a", "call-b"]
    assert agent.pending_tool_calls == {}


@pytest.mark.asyncio
async def test_concurrent_duplicate_submissions_dispatch_once(monkeypatch):
    agent, tool, collection = agent_with_tool(delay=0.02)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    await agent.execute_tool(call("original-id"))

    first, second = await asyncio.gather(
        agent.submit_clarification_reply("original-id", '{"path":"README.md"}'),
        agent.submit_clarification_reply("original-id", '{"path":"second.py"}'),
    )

    assert "executed" in first
    assert "executed" not in second
    assert collection.validation_entries == 2
    assert tool.calls == [{"path": "README.md"}]


@pytest.mark.asyncio
async def test_business_validation_failure_is_not_replayed(monkeypatch):
    agent, tool, collection = agent_with_tool(business_failure=True)
    monkeypatch.setattr("builtins.input", lambda prompt: "README.md")

    result = await agent.execute_tool(call("original-id"))
    duplicate = await agent.submit_clarification_reply(
        "original-id", '{"path":"README.md"}'
    )

    assert "business validation rejected" in result
    assert "business validation rejected" not in duplicate
    assert collection.validation_entries == 2
    assert tool.calls == [{"path": "README.md"}]


@pytest.mark.asyncio
async def test_revalidation_keeps_additional_properties_boundary(monkeypatch):
    agent, tool, collection = agent_with_tool()
    monkeypatch.setattr("builtins.input", lambda prompt: '{"path":"README.md"}')

    result = await agent.execute_tool(call("original-id", {"extra": "untrusted"}))

    assert "additionalProperties" in result
    assert "original-id" in agent.pending_tool_calls
    assert agent.pending_tool_calls["original-id"].arguments == {
        "extra": "untrusted",
        "path": "README.md",
    }
    assert collection.validation_entries == 4
    assert tool.calls == []
