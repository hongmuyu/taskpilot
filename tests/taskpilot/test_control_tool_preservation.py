import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
    from app.llm import LLM
    from app.schema import AgentState, Function, Message, ToolCall
    from app.taskpilot.semantic_tool_retrieval import SemanticToolRetriever
    from app.taskpilot.tool_embedding_index import InMemoryToolIndex
    from app.tool import Terminate
    from app.tool.ask_human import AskHuman
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


class FixedVectors:
    model_id = "fixed-vectors"
    revision = "fixture-1"
    vector_version = "keywords-v1"

    def __init__(self):
        self.calls = []

    async def embed(self, texts):
        self.calls.append(tuple(texts))
        result = []
        for text in texts:
            value = text.lower()
            if "read source files" in value or "source file" in value:
                result.append([1.0, 1.0, 0.0])
            elif "search repository issues" in value or "issue" in value:
                result.append([1.0, 0.0, 0.0])
            else:
                result.append([0.0, 0.0, -1.0])
        return result


def make_agent(*tools, k=1):
    llm = object.__new__(LLM)
    llm.ask_tool = AsyncMock(
        return_value=SimpleNamespace(content="done", tool_calls=[])
    )
    backend = FixedVectors()
    agent = ToolCallAgent(
        llm=llm,
        available_tools=ToolCollection(*tools),
        next_step_prompt="Next step",
        routing_top_k=k,
        routing_retriever=SemanticToolRetriever(InMemoryToolIndex(backend)),
    )
    agent.routing_original_task = "Investigate an issue"
    agent.messages = [Message.user_message(agent.routing_original_task)]
    return agent, backend


def exposed_names(agent):
    return [
        item["function"]["name"]
        for item in agent.llm.ask_tool.call_args.kwargs["tools"]
    ]


def call(name, arguments, call_id):
    return ToolCall(
        id=call_id, function=Function(name=name, arguments=json.dumps(arguments))
    )


@pytest.mark.asyncio
async def test_k_one_keeps_terminate_outside_business_ranking():
    issue = ProbeTool()
    file_tool = ProbeTool(name="file_tool", description="Read source files")
    terminate = Terminate()
    agent, backend = make_agent(issue, file_tool, terminate, k=1)
    registry_before = agent.available_tools.to_params()

    await agent.think()

    assert exposed_names(agent) == ["issue_tool", "terminate"]
    assert len(exposed_names(agent)) == 2  # K_business=1, K_total=2
    assert len(backend.calls[0]) == 2
    assert all('"name":"terminate"' not in document for document in backend.calls[0])
    assert agent.available_tools.to_params() == registry_before


@pytest.mark.asyncio
async def test_control_name_or_subclass_does_not_auto_expose_another_tool():
    class ImpostorTerminate(Terminate):
        name: str = "dangerous_stop"

        async def execute(self, status: str):
            return "unexpected"

    agent, backend = make_agent(ProbeTool(), ImpostorTerminate(), Terminate(), k=1)

    await agent.think()

    assert exposed_names(agent) == ["issue_tool", "terminate"]
    assert len(backend.calls[0]) == 1


@pytest.mark.asyncio
async def test_zero_business_tools_can_still_terminate():
    agent, backend = make_agent(Terminate(), k=1)
    agent.llm.ask_tool.return_value = SimpleNamespace(
        content=None, tool_calls=[call("terminate", {"status": "success"}, "stop")]
    )

    result = await agent.step()

    assert exposed_names(agent) == ["terminate"]
    assert "completed with status: success" in result
    assert agent.state == AgentState.FINISHED
    assert backend.calls == []


@pytest.mark.asyncio
async def test_ask_human_is_only_exposed_when_clarification_is_pending():
    issue = ProbeTool()
    agent, _ = make_agent(issue, AskHuman(), Terminate(), k=1)
    await agent.think()
    assert exposed_names(agent) == ["issue_tool", "terminate"]

    with patch.object(
        AskHuman, "execute", new_callable=AsyncMock, return_value=""
    ) as ask:
        await agent.execute_tool(call("issue_tool", {}, "missing"))

    assert ask.await_count == 3  # T016 forced clarification, not model choice
    assert issue.calls == []
    assert "missing" in agent.pending_tool_calls
    await agent.think()
    assert exposed_names(agent) == ["issue_tool", "ask_human", "terminate"]


@pytest.mark.asyncio
async def test_pending_clarification_preserves_controls_with_no_business_match():
    agent, _ = make_agent(ProbeTool(), AskHuman(), Terminate(), k=1)
    agent.routing_original_task = "What is tomorrow's weather?"
    agent.messages = [Message.user_message(agent.routing_original_task)]
    with patch.object(AskHuman, "execute", new_callable=AsyncMock, return_value=""):
        await agent.execute_tool(call("issue_tool", {}, "missing"))

    await agent.think()

    assert exposed_names(agent) == ["ask_human", "terminate"]


@pytest.mark.asyncio
async def test_pending_without_registered_controls_never_sends_empty_schemas():
    agent, _ = make_agent(ProbeTool(), k=1)
    agent.routing_original_task = "What is tomorrow's weather?"
    agent.messages = [Message.user_message(agent.routing_original_task)]
    with patch.object(AskHuman, "execute", new_callable=AsyncMock, return_value=""):
        await agent.execute_tool(call("issue_tool", {}, "missing"))

    result = await agent.step()

    assert "clarif" in result.lower()
    agent.llm.ask_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_match_without_pending_still_clarifies_with_terminate_registered():
    agent, _ = make_agent(ProbeTool(), Terminate(), k=1)
    agent.routing_original_task = "What is tomorrow's weather?"
    agent.messages = [Message.user_message(agent.routing_original_task)]

    result = await agent.step()

    assert "clarif" in result.lower()
    agent.llm.ask_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_parallel_calls_keep_forced_clarification_and_original_dispatch():
    issue = ProbeTool()
    file_tool = ProbeTool(name="file_tool", description="Read source files")
    agent, _ = make_agent(issue, file_tool, Terminate(), k=1)
    agent.llm.ask_tool.return_value = SimpleNamespace(
        content=None,
        tool_calls=[
            call("issue_tool", {"query": "crash"}, "valid"),
            call("file_tool", {}, "missing"),
        ],
    )

    with patch.object(
        AskHuman, "execute", new_callable=AsyncMock, return_value="cancel"
    ) as ask:
        await agent.step()

    assert exposed_names(agent) == ["issue_tool", "terminate"]
    assert ask.await_count == 1
    assert issue.calls == [{"query": "crash"}]
    assert file_tool.calls == []
    assert {
        message.tool_call_id for message in agent.messages if message.role == "tool"
    } == {"valid", "missing"}


@pytest.mark.asyncio
async def test_routing_disabled_keeps_original_full_registry_behavior():
    agent, backend = make_agent(ProbeTool(), AskHuman(), Terminate(), k=None)

    await agent.think()

    assert exposed_names(agent) == ["issue_tool", "ask_human", "terminate"]
    assert backend.calls == []


@pytest.mark.asyncio
async def test_top_k_clarification_revalidation_and_retry_keep_original_call_id():
    class RetryingRead(ProbeTool):
        retry_safe_read: bool = True

        async def execute(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return ToolResult(
                    error="temporary read failure",
                    retry_classification="transient",
                    http_status=503,
                )
            return ToolResult(output="issue evidence")

    issue = RetryingRead()
    other = ProbeTool(name="file_tool", description="Read source files")
    agent, _ = make_agent(issue, other, Terminate(), k=1)
    registry_before = agent.available_tools.to_params()
    agent.llm.ask_tool.return_value = SimpleNamespace(
        content=None, tool_calls=[call("issue_tool", {}, "routed-missing")]
    )

    async def answer(self, *, inquire):
        assert "query" in inquire
        assert issue.calls == []
        return json.dumps({"query": "crash"})

    with patch.object(AskHuman, "execute", new=answer):
        observation = await agent.step()

    assert exposed_names(agent) == ["issue_tool", "terminate"]
    assert observation.startswith(
        "Status: success\nAttempts: 1:transient(503), 2:success\n"
    )
    assert issue.calls == [{"query": "crash"}, {"query": "crash"}]
    assert other.calls == []
    assert agent.tool_call_sources["routed-missing"] == {"query": "user_clarification"}
    assert agent.memory.messages[-1].tool_call_id == "routed-missing"
    assert agent.available_tools.to_params() == registry_before
    assert set(agent.available_tools.tool_map) == {
        "issue_tool",
        "file_tool",
        "terminate",
    }
