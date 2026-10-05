import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import Field, ValidationError


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
    from app.llm import LLM
    from app.schema import Function, Message, ToolCall
    from app.taskpilot.semantic_tool_retrieval import SemanticToolRetriever
    from app.taskpilot.tool_embedding_index import InMemoryToolIndex
    from app.tool.base import BaseTool, ToolResult
    from app.tool.tool_collection import ToolCollection


class ProbeTool(BaseTool):
    name: str = "issue_tool"
    description: str = "Search repository issues"
    parameters: dict = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    calls: list[dict] = Field(default_factory=list)

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return ToolResult(output="probe result")


class KeywordVectors:
    model_id = "fixed-vectors"
    revision = "fixture-1"
    vector_version = "keywords-v1"

    async def embed(self, texts):
        vectors = []
        for text in texts:
            value = text.lower()
            if "read source files" in value or "source file" in value:
                vectors.append([1.0, 1.0, 0.0])
            elif "search repository issues" in value or "issue" in value:
                vectors.append([1.0, 0.0, 0.0])
            elif "terminate" in value:
                vectors.append([0.0, 0.0, 1.0])
            else:
                vectors.append([0.0, 0.0, -1.0])
        return vectors


def agent_with_tools(*tools, k=None):
    llm = object.__new__(LLM)
    llm.ask_tool = AsyncMock(
        return_value=SimpleNamespace(content="done", tool_calls=[])
    )
    agent = ToolCallAgent(
        llm=llm,
        available_tools=ToolCollection(*tools),
        next_step_prompt="Next step",
        routing_top_k=k,
        routing_retriever=SemanticToolRetriever(InMemoryToolIndex(KeywordVectors())),
    )
    return agent


def exposed_names(agent):
    return [
        item["function"]["name"]
        for item in agent.llm.ask_tool.call_args.kwargs["tools"]
    ]


@pytest.mark.asyncio
async def test_top_k_selects_only_ranked_business_schemas_and_keeps_registry():
    issue = ProbeTool()
    file_tool = ProbeTool(name="file_tool", description="Read source files")
    control = ProbeTool(name="terminate", description="Terminate")
    agent = agent_with_tools(issue, file_tool, control, k=1)
    agent.routing_original_task = "Investigate an issue"
    agent.messages = [Message.user_message(agent.routing_original_task)]
    before = agent.available_tools.to_params()

    await agent.think()

    assert exposed_names(agent) == ["issue_tool"]
    assert (
        agent.llm.ask_tool.call_args.kwargs["tools"][0]["function"]["parameters"]
        is issue.parameters
    )
    assert agent.available_tools.to_params() == before
    assert set(agent.available_tools.tool_map) == {
        "issue_tool",
        "file_tool",
        "terminate",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "k, expected",
    [
        (1, ["issue_tool"]),
        (2, ["issue_tool", "file_tool"]),
        (9, ["issue_tool", "file_tool"]),
    ],
)
async def test_k_boundary_and_fewer_candidates_than_k(k, expected):
    agent = agent_with_tools(
        ProbeTool(), ProbeTool(name="file_tool", description="Read source files"), k=k
    )
    agent.routing_original_task = "Investigate an issue"
    agent.messages = [Message.user_message(agent.routing_original_task)]

    await agent.think()

    assert exposed_names(agent) == expected


def test_invalid_k_is_rejected():
    with pytest.raises(ValidationError):
        agent_with_tools(ProbeTool(), k=0)


@pytest.mark.asyncio
async def test_default_agent_still_sends_full_tool_pool():
    agent = agent_with_tools(
        ProbeTool(), ProbeTool(name="file_tool", description="Read source files")
    )
    agent.messages = [Message.user_message("Investigate an issue")]

    await agent.think()

    assert exposed_names(agent) == ["issue_tool", "file_tool"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tools, task",
    [([], "Investigate an issue"), ([ProbeTool()], "What is tomorrow's weather?")],
)
async def test_empty_pool_or_no_match_clarifies_without_llm_or_dispatch(tools, task):
    agent = agent_with_tools(*tools, k=2)
    agent.routing_original_task = task
    agent.messages = [Message.user_message(task)]

    response = await agent.step()

    assert "clarif" in response.lower()
    agent.llm.ask_tool.assert_not_awaited()
    assert all(tool.calls == [] for tool in tools)


@pytest.mark.asyncio
async def test_current_observation_changes_exposed_candidate():
    task = "Investigate the repository"
    tools = (ProbeTool(), ProbeTool(name="file_tool", description="Read source files"))
    issue_agent = agent_with_tools(*tools, k=1)
    issue_agent.routing_original_task = task
    issue_agent.messages = [
        Message.user_message(task),
        Message.tool_message(
            "Issue #42 reports a crash", name="probe", tool_call_id="issue"
        ),
    ]
    file_agent = agent_with_tools(*tools, k=1)
    file_agent.routing_original_task = task
    file_agent.messages = [
        Message.user_message(task),
        Message.tool_message(
            "Read source file app/router.py", name="probe", tool_call_id="file"
        ),
    ]

    await issue_agent.think()
    await file_agent.think()

    assert exposed_names(issue_agent) == ["issue_tool"]
    assert exposed_names(file_agent) == ["file_tool"]


@pytest.mark.asyncio
async def test_run_returns_no_match_clarification_without_dispatch():
    tool = ProbeTool()
    agent = agent_with_tools(tool, k=1)
    with patch("app.agent.base.SANDBOX_CLIENT.cleanup", new_callable=AsyncMock):
        result = await agent.run("What is tomorrow's weather?")

    assert "clarif" in result.lower()
    agent.llm.ask_tool.assert_not_awaited()
    assert tool.calls == []
    assert agent.routing_original_task is None


@pytest.mark.asyncio
async def test_index_failure_does_not_expose_full_tool_pool():
    class FailingVectors(KeywordVectors):
        async def embed(self, texts):
            raise RuntimeError("fixture failure")

    agent = agent_with_tools(ProbeTool(), k=1)
    agent.routing_retriever = SemanticToolRetriever(InMemoryToolIndex(FailingVectors()))
    agent.routing_original_task = "Investigate an issue"
    agent.messages = [Message.user_message(agent.routing_original_task)]

    response = await agent.step()

    assert "retry" in response.lower()
    agent.llm.ask_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_candidate_filter_does_not_change_original_dispatch_boundary():
    issue = ProbeTool()
    file_tool = ProbeTool(name="file_tool", description="Read source files")
    agent = agent_with_tools(issue, file_tool, k=1)
    agent.routing_original_task = "Investigate an issue"
    agent.messages = [Message.user_message(agent.routing_original_task)]
    await agent.think()
    assert exposed_names(agent) == ["issue_tool"]

    invalid = ToolCall(
        id="invalid",
        function=Function(name="file_tool", arguments=json.dumps({"query": 4})),
    )
    valid = ToolCall(
        id="valid",
        function=Function(
            name="file_tool", arguments=json.dumps({"query": "router.py"})
        ),
    )
    assert "validation" in (await agent.execute_tool(invalid)).lower()
    assert file_tool.calls == []
    assert "probe result" in await agent.execute_tool(valid)
    assert file_tool.calls == [{"query": "router.py"}]
