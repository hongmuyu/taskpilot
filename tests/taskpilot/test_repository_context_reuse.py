import base64
import json
from unittest.mock import patch

import httpx
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
    from app.agent.repository_investigation import RepositoryInvestigationAgent
    from app.schema import Function, ToolCall
    from app.taskpilot.github_tools import (
        GitHubClient,
        GitHubRepositoryInfo,
        RepositoryContext,
    )
    from app.tool.base import BaseTool, ToolResult
    from app.tool.tool_collection import ToolCollection


class ContextProbe(BaseTool):
    name: str = "context_probe"
    description: str = "Record validated arguments"
    parameters: dict = {
        "type": "object",
        "properties": {
            "repository": {"type": "string"},
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "ref": {"type": "string"},
            "path": {"type": "string"},
        },
        "required": ["repository", "ref", "path"],
        "additionalProperties": False,
    }
    calls: list[dict] = Field(default_factory=list)

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return ToolResult(output="executed")


def call(call_id="call-1", **kwargs):
    return ToolCall(
        id=call_id,
        function=Function(name="context_probe", arguments=json.dumps(kwargs)),
    )


def agent_for(repository="octo/one", ref="main", *, source="user_input"):
    context = RepositoryContext.parse(repository, ref=ref, source=source)
    agent = RepositoryInvestigationAgent.create(context, client=GitHubClient())
    probe = ContextProbe()
    agent.available_tools = ToolCollection(probe)
    return agent, probe


@pytest.mark.asyncio
async def test_confirmed_repository_and_ref_fill_missing_fields_without_reprompt(
    monkeypatch,
):
    agent, probe = agent_for()
    monkeypatch.setattr(
        "builtins.input", lambda prompt: pytest.fail("unexpected prompt")
    )
    try:
        result = await agent.execute_tool(call(path="README.md"))
        assert "executed" in result
        assert probe.calls == [
            {
                "repository": "octo/one",
                "owner": "octo",
                "repo": "one",
                "ref": "main",
                "path": "README.md",
            }
        ]
        assert agent.tool_call_sources["call-1"] == {
            "repository": "repository_context:user_input",
            "owner": "repository_context:user_input",
            "repo": "repository_context:user_input",
            "ref": "repository_context:user_input",
            "path": "tool_call",
        }
    finally:
        await agent.cleanup()


@pytest.mark.asyncio
async def test_context_only_fills_declared_fields_and_asks_only_for_path(monkeypatch):
    agent, probe = agent_for()
    prompts = []
    monkeypatch.setattr(
        "builtins.input", lambda prompt: prompts.append(prompt) or "README.md"
    )
    try:
        result = await agent.execute_tool(call())
        assert "executed" in result
        assert len(prompts) == 1
        assert (
            "path" in prompts[0]
            and "repository" not in prompts[0]
            and "ref" not in prompts[0]
        )
        assert probe.calls == [
            {
                "repository": "octo/one",
                "owner": "octo",
                "repo": "one",
                "ref": "main",
                "path": "README.md",
            }
        ]
        assert agent.tool_call_sources["call-1"]["path"] == "user_clarification"
    finally:
        await agent.cleanup()


@pytest.mark.asyncio
async def test_absent_ref_has_no_inherited_value_and_requires_clarification(
    monkeypatch,
):
    agent, probe = agent_for(ref=None)
    prompts = []
    monkeypatch.setattr(
        "builtins.input", lambda prompt: prompts.append(prompt) or "cancel"
    )
    try:
        await agent.execute_tool(call(path="README.md"))
        assert len(prompts) == 1 and "ref" in prompts[0]
        assert probe.calls == []
        assert agent.tool_call_sources["call-1"] == {
            "repository": "repository_context:user_input",
            "owner": "repository_context:user_input",
            "repo": "repository_context:user_input",
            "path": "tool_call",
        }
    finally:
        await agent.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "conflict", [{"repository": "other/repo"}, {"ref": "old"}, {"owner": "other"}]
)
async def test_conflicting_explicit_argument_stops_dispatch_and_identifies_conflict(
    monkeypatch, conflict
):
    agent, probe = agent_for()
    monkeypatch.setattr(
        "builtins.input", lambda prompt: pytest.fail("unexpected prompt")
    )
    try:
        result = await agent.execute_tool(call(path="x.py", **conflict))
        assert "conflict" in result.lower() and next(iter(conflict)) in result
        assert probe.calls == []
        assert agent.repository_context.owner == "octo"
    finally:
        await agent.cleanup()


@pytest.mark.asyncio
async def test_explicit_switch_updates_bound_tools_and_cancels_old_pending(monkeypatch):
    agent, probe = agent_for()
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    await agent.execute_tool(call())
    assert "call-1" in agent.pending_tool_calls
    assert agent.pending_tool_calls["call-1"].original_arguments == {}
    assert agent.pending_tool_calls["call-1"].sources["repository"] == (
        "repository_context:user_input"
    )

    agent.switch_repository_context(
        RepositoryContext.parse("octo/two", ref="release", source="user_input")
    )
    try:
        assert agent.repository_context.repo == "two"
        assert all(
            tool.context.repo == "two"
            for tool in agent.available_tools
            if hasattr(tool, "context")
        )
        assert "call-1" not in agent.pending_tool_calls
        assert "executed" not in await agent.submit_clarification_reply(
            "call-1", '{"path":"old.py"}'
        )
        result = await agent.execute_tool(call("call-2", path="new.py"))
        assert "executed" in result
        assert probe.calls == [
            {
                "repository": "octo/two",
                "owner": "octo",
                "repo": "two",
                "ref": "release",
                "path": "new.py",
            }
        ]
    finally:
        await agent.cleanup()


@pytest.mark.asyncio
async def test_separate_tasks_do_not_share_context(monkeypatch):
    first, first_probe = agent_for("octo/one")
    second, second_probe = agent_for("octo/two", ref="next")
    monkeypatch.setattr(
        "builtins.input", lambda prompt: pytest.fail("unexpected prompt")
    )
    try:
        await first.execute_tool(call(path="a.py"))
        await second.execute_tool(call(path="b.py"))
        assert first_probe.calls[0]["repository"] == "octo/one"
        assert second_probe.calls[0]["repository"] == "octo/two"
        assert second_probe.calls[0]["ref"] == "next"
    finally:
        await first.cleanup()
        await second.cleanup()


@pytest.mark.asyncio
async def test_switch_updates_real_github_tool_repository_and_ref():
    requests = []

    def respond(request):
        requests.append((request.url.path, request.url.params.get("ref")))
        return httpx.Response(
            200,
            json={
                "path": "README.md",
                "encoding": "base64",
                "content": base64.b64encode(b"hello").decode(),
            },
        )

    client = GitHubClient(
        http_client=httpx.AsyncClient(
            base_url="https://api.github.com", transport=httpx.MockTransport(respond)
        )
    )
    agent = RepositoryInvestigationAgent.create(
        RepositoryContext.parse("octo/one", ref="main", source="user_input"),
        client=client,
    )
    try:

        def read_file(call_id):
            return ToolCall(
                id=call_id,
                function=Function(
                    name="github_read_file", arguments='{"path":"README.md"}'
                ),
            )

        await agent.execute_tool(read_file("old"))
        agent.switch_repository_context(
            RepositoryContext.parse("octo/two", ref="release", source="user_input")
        )
        await agent.execute_tool(read_file("new"))
        assert requests == [
            ("/repos/octo/one/contents/README.md", "main"),
            ("/repos/octo/two/contents/README.md", "release"),
        ]
    finally:
        await agent.cleanup()


def test_unverified_or_default_context_cannot_be_bound_to_agent():
    with pytest.raises(ValueError, match="source"):
        RepositoryInvestigationAgent.create(RepositoryContext.parse("octo/one"))


@pytest.mark.asyncio
async def test_directly_constructed_agent_does_not_inherit_unverified_context(
    monkeypatch,
):
    probe = ContextProbe()
    agent = RepositoryInvestigationAgent(
        repository_context=RepositoryContext.parse("octo/one", ref="main"),
        github_client=GitHubClient(),
        available_tools=ToolCollection(probe),
    )
    prompts = []
    monkeypatch.setattr(
        "builtins.input", lambda prompt: prompts.append(prompt) or "cancel"
    )
    try:
        result = await agent.execute_tool(call(path="README.md"))
        assert "source" in result.lower()
        assert prompts == []
        assert probe.calls == []
    finally:
        await agent.cleanup()


@pytest.mark.asyncio
async def test_unverified_context_cannot_run_bound_github_tool():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={})

    client = GitHubClient(
        http_client=httpx.AsyncClient(
            base_url="https://api.github.com", transport=httpx.MockTransport(respond)
        )
    )
    context = RepositoryContext.parse("octo/one")
    agent = RepositoryInvestigationAgent(
        repository_context=context,
        github_client=client,
        available_tools=ToolCollection(
            GitHubRepositoryInfo(context=context, client=client)
        ),
    )
    try:
        command = ToolCall(
            id="unverified",
            function=Function(name="github_repository_info", arguments="{}"),
        )
        result = await agent.execute_tool(command)
        assert "source" in result.lower()
        assert requests == []
    finally:
        await agent.cleanup()
