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
    from app.tool.base import BaseTool, ToolResult
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
    name: str = "merge_probe"
    description: str = "Count real executions"
    parameters: dict = SCHEMA
    calls: list[dict] = Field(default_factory=list)

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return ToolResult(output="executed")


def call(call_id, arguments):
    return ToolCall(
        id=call_id,
        function=Function(name="merge_probe", arguments=json.dumps(arguments)),
    )


def make_agent(required=("path",)):
    tool = CountingTool(parameters={**SCHEMA, "required": list(required)})
    return ToolCallAgent(available_tools=ToolCollection(tool)), tool


@pytest.mark.asyncio
async def test_single_field_reply_merges_into_pending_call_without_dispatch(
    monkeypatch,
):
    agent, tool = make_agent()
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    command = call("call-a", {})

    await agent.execute_tool(command)
    assert agent._merge_clarification_reply("call-a", "README.md") == "merged"

    pending = agent.pending_tool_calls["call-a"]
    assert pending.tool_call_id == "call-a"
    assert pending.tool_name == "merge_probe"
    assert pending.original_arguments_json == command.function.arguments
    assert pending.original_arguments == {}
    assert pending.arguments == {"path": "README.md"}
    assert pending.sources == {"path": "user_clarification"}
    assert pending.missing_fields == []
    assert "pending_tool_calls" not in agent.model_dump()
    assert tool.calls == []


@pytest.mark.asyncio
async def test_multiple_named_fields_merge_without_guessing(monkeypatch):
    agent, tool = make_agent(("path", "issue_number"))
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    await agent.execute_tool(call("call-a", {}))
    assert (
        agent._merge_clarification_reply(
            "call-a", '{"path":"src/main.py","issue_number":12}'
        )
        == "merged"
    )

    pending = agent.pending_tool_calls["call-a"]
    assert pending.arguments == {"path": "src/main.py", "issue_number": 12}
    assert pending.sources == {
        "path": "user_clarification",
        "issue_number": "user_clarification",
    }
    assert pending.missing_fields == []
    assert tool.calls == []


@pytest.mark.asyncio
async def test_multi_round_reply_only_updates_named_field(monkeypatch):
    agent, tool = make_agent(("path", "issue_number"))
    monkeypatch.setattr("builtins.input", lambda prompt: '{"path":"README.md"}')

    await agent.execute_tool(call("call-a", {}))
    first = agent.pending_tool_calls["call-a"]
    assert first.arguments == {"path": "README.md"}
    assert first.missing_fields == ["issue_number"]

    status = agent._merge_clarification_reply("call-a", '{"issue_number":12}')

    assert status == "merged"
    assert first.arguments == {"path": "README.md", "issue_number": 12}
    assert first.missing_fields == []
    assert tool.calls == []


@pytest.mark.asyncio
async def test_existing_value_changes_only_when_explicitly_named(monkeypatch):
    agent, tool = make_agent()
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    original = {"repository": "octo/old"}

    await agent.execute_tool(call("call-a", original))
    assert agent._merge_clarification_reply("call-a", "README.md") == "merged"
    pending = agent.pending_tool_calls["call-a"]
    assert pending.arguments == {"repository": "octo/old", "path": "README.md"}
    assert pending.sources == {
        "repository": "tool_call",
        "path": "user_clarification",
    }

    status = agent._merge_clarification_reply("call-a", '{"repository":"octo/new"}')

    assert status == "merged"
    assert pending.original_arguments == original
    assert pending.arguments == {"repository": "octo/new", "path": "README.md"}
    assert pending.sources["repository"] == "user_clarification"
    assert tool.calls == []


@pytest.mark.asyncio
async def test_conflict_and_ambiguous_reply_reprompt_and_stay_pending(monkeypatch):
    agent, tool = make_agent(("path", "issue_number"))
    answers = iter(
        [
            '{"path":"a","path":"b"}',
            "path or issue number",
            '{"path":"a","path":"b"}',
        ]
    )
    prompts = []
    monkeypatch.setattr(
        "builtins.input", lambda prompt: prompts.append(prompt) or next(answers)
    )

    await agent.execute_tool(call("call-a", {}))

    pending = agent.pending_tool_calls["call-a"]
    assert len(prompts) == 3
    assert pending.arguments == {}
    assert pending.sources == {}
    assert pending.missing_fields == ["path", "issue_number"]
    assert tool.calls == []


@pytest.mark.asyncio
async def test_multiple_pending_calls_and_reply_mismatch_stay_isolated(monkeypatch):
    agent, tool = make_agent(("path", "issue_number"))
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    await agent.execute_tool(call("call-a", {"repository": "octo/a"}))
    await agent.execute_tool(call("call-b", {"repository": "octo/b"}))
    before_a = agent.pending_tool_calls["call-a"].arguments.copy()
    before_b = agent.pending_tool_calls["call-b"].arguments.copy()

    assert agent._merge_clarification_reply("unknown", '{"path":"wrong"}') == "mismatch"
    assert (
        agent._merge_clarification_reply(
            "call-a", '{"tool_call_id":"call-b","path":"wrong"}'
        )
        == "needs_clarification"
    )
    assert agent.pending_tool_calls["call-a"].arguments == before_a
    assert agent.pending_tool_calls["call-b"].arguments == before_b

    assert agent._merge_clarification_reply("call-b", '{"path":"b.py"}') == "merged"
    assert agent.pending_tool_calls["call-a"].arguments == before_a
    assert agent.pending_tool_calls["call-b"].arguments == {
        "repository": "octo/b",
        "path": "b.py",
    }
    assert tool.calls == []


@pytest.mark.asyncio
async def test_cancel_removes_pending_and_late_reply_cannot_merge(monkeypatch):
    agent, tool = make_agent()
    monkeypatch.setattr("builtins.input", lambda prompt: "cancel")

    await agent.execute_tool(call("call-a", {}))

    assert agent.pending_tool_calls == {}
    assert (
        agent._merge_clarification_reply("call-a", '{"path":"README.md"}') == "mismatch"
    )
    assert tool.calls == []


@pytest.mark.asyncio
async def test_nested_required_field_merges_without_replacing_parent(monkeypatch):
    agent, tool = make_agent()
    tool.parameters = {
        "type": "object",
        "properties": {
            "meta": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "branch": {"type": "string"},
                },
                "required": ["owner"],
            }
        },
        "required": ["meta"],
    }
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    await agent.execute_tool(call("call-a", {"meta": {"branch": "main"}}))
    assert (
        agent._merge_clarification_reply("call-a", '{"meta.owner":"octo"}') == "merged"
    )

    pending = agent.pending_tool_calls["call-a"]
    assert pending.original_arguments == {"meta": {"branch": "main"}}
    assert pending.arguments == {"meta": {"branch": "main", "owner": "octo"}}
    assert pending.sources["meta.owner"] == "user_clarification"
    assert pending.missing_fields == []
    assert tool.calls == []


@pytest.mark.asyncio
async def test_pending_call_id_cannot_be_reused_with_new_arguments(monkeypatch):
    agent, tool = make_agent()
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    await agent.execute_tool(call("call-a", {}))
    result = await agent.execute_tool(call("call-a", {"path": "README.md"}))

    assert "different pending call" in result
    assert agent.pending_tool_calls["call-a"].arguments == {}
    assert tool.calls == []


@pytest.mark.asyncio
async def test_pending_state_is_cleared_after_agent_run(monkeypatch):
    agent, tool = make_agent()
    agent.max_steps = 0
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    await agent.execute_tool(call("call-a", {}))
    assert "call-a" in agent.pending_tool_calls

    await agent.run("next task")

    assert agent.pending_tool_calls == {}
    assert tool.calls == []


@pytest.mark.asyncio
async def test_unnamed_list_reply_stays_pending(monkeypatch):
    agent, tool = make_agent()
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    await agent.execute_tool(call("call-a", {}))

    status = agent._merge_clarification_reply("call-a", '["a.py", "b.py"]')

    assert status == "needs_clarification"
    assert agent.pending_tool_calls["call-a"].arguments == {}
    assert tool.calls == []
