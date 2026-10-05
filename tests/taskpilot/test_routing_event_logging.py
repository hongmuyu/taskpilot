import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

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
    from app.schema import Function, Message, ToolCall
    from app.taskpilot.semantic_tool_retrieval import SemanticToolRetriever
    from app.taskpilot.tool_embedding_index import InMemoryToolIndex
    from app.tool import Terminate
    from app.tool.ask_human import AskHuman
    from app.tool.base import BaseTool, ToolResult
    from app.tool.mcp import MCPClientTool
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
        return ToolResult(output=f"result: {kwargs['query']}")


class FixedVectors:
    model_id = "fixed-vectors"
    revision = "fixture-1"
    vector_version = "keywords-v1"

    async def embed(self, texts):
        result = []
        for text in texts:
            value = text.lower()
            if "read source files" in value or "source file" in value:
                result.append([0.0, 1.0])
            elif "search repository issues" in value or "issue" in value:
                result.append([1.0, 0.0])
            else:
                result.append([-1.0, 0.0])
        return result


def make_agent(*tools, task="Investigate an issue", k=1, backend=None):
    llm = object.__new__(LLM)
    llm.ask_tool = AsyncMock(
        return_value=SimpleNamespace(content="done", tool_calls=[])
    )
    agent = ToolCallAgent(
        llm=llm,
        available_tools=ToolCollection(*tools),
        next_step_prompt="Next step",
        routing_top_k=k,
        routing_retriever=SemanticToolRetriever(
            InMemoryToolIndex(backend or FixedVectors())
        ),
    )
    agent.routing_original_task = task
    agent.messages = [Message.user_message(task)]
    return agent


def routing_events(info_log):
    return [
        json.loads(entry.args[0].removeprefix("tool_routing_event "))
        for entry in info_log.call_args_list
        if entry.args[0].startswith("tool_routing_event ")
    ]


@pytest.mark.asyncio
async def test_routing_event_records_candidates_versions_k_and_model_selection():
    issue = ProbeTool()
    file_tool = ProbeTool(name="file_tool", description="Read source files")
    agent = make_agent(issue, file_tool, Terminate())
    agent.llm.ask_tool.return_value = SimpleNamespace(
        content=None,
        tool_calls=[
            ToolCall(
                id="call-1",
                function=Function(name="issue_tool", arguments='{"query":"crash"}'),
            )
        ],
    )

    with patch("app.agent.toolcall.logger.info") as info_log:
        await agent.think()

    (event,) = routing_events(info_log)
    UUID(event["run_id"])
    assert event["event"] == "tool_routing"
    assert event["version"] == 1
    assert event["step"] == 0
    assert event["query"]["summary"] == "task_only"
    assert (
        event["query"]["sha256"]
        == hashlib.sha256(b"Original user task: Investigate an issue").hexdigest()
    )
    assert event["candidate_status"] == "ready"
    assert event["selection_status"] == "selected"
    assert event["k_business"] == 1
    assert event["k_total"] == 2
    assert [item["name"] for item in event["exposed_tools"]] == [
        "issue_tool",
        "terminate",
    ]
    assert event["selected_tools"] == [{"name": "issue_tool", "tool_call_id": "call-1"}]
    assert [item["name"] for item in event["candidates"]] == ["issue_tool", "file_tool"]
    assert event["candidates"][0]["score"] == pytest.approx(1.0)
    assert event["candidates"][0]["exposed"] is True
    assert event["candidates"][1]["exposed"] is False
    assert event["candidates"][0]["identity"] == "local:issue_tool"
    assert event["candidates"][0]["source"] == "local"
    assert len(event["candidates"][0]["schema_sha256"]) == 64
    assert event["index_version"]["model_id"] == "fixed-vectors"
    assert event["index_version"]["model_revision"] == "fixture-1"
    assert event["index_version"]["content_fingerprint"] == event["metadata_version"]


@pytest.mark.asyncio
async def test_events_from_two_steps_share_run_id_and_track_observation():
    agent = make_agent(
        ProbeTool(), ProbeTool(name="file_tool", description="Read source files")
    )

    with patch("app.agent.toolcall.logger.info") as info_log:
        await agent.think()
        agent.messages += [
            Message.tool_message(
                "Read source file app/router.py", name="probe", tool_call_id="obs-1"
            )
        ]
        await agent.think()

    first, second = routing_events(info_log)
    assert first["run_id"] == second["run_id"]
    assert first["query"]["sha256"] != second["query"]["sha256"]
    assert second["query"]["summary"] == "task_and_observation"
    assert second["query"]["observation_tool_call_id"] == "obs-1"
    assert first["selection_status"] == second["selection_status"] == "not_selected"
    assert first["selected_tools"] == second["selected_tools"] == []


@pytest.mark.asyncio
async def test_separate_runs_have_distinct_association_ids():
    agent = make_agent(ProbeTool(), task="What is tomorrow's weather?")

    with patch("app.agent.toolcall.logger.info") as info_log, patch(
        "app.agent.base.SANDBOX_CLIENT.cleanup", new_callable=AsyncMock
    ):
        await agent.run("What is tomorrow's weather?")
        await agent.run("What is tomorrow's weather?")

    first, second = routing_events(info_log)
    assert first["run_id"] != second["run_id"]
    assert agent.routing_run_id is None


@pytest.mark.asyncio
async def test_schema_update_changes_logged_schema_and_metadata_versions():
    issue = ProbeTool()
    agent = make_agent(issue, Terminate())

    with patch("app.agent.toolcall.logger.info") as info_log:
        await agent.think()
        issue.parameters = {
            **issue.parameters,
            "properties": {"query": {"type": "string", "description": "Updated query"}},
        }
        await agent.think()

    first, second = routing_events(info_log)
    assert first["metadata_version"] != second["metadata_version"]
    assert first["index_version"] != second["index_version"]
    assert (
        first["candidates"][0]["schema_sha256"]
        != second["candidates"][0]["schema_sha256"]
    )


@pytest.mark.asyncio
async def test_routed_logs_omit_task_schema_model_content_arguments_and_result():
    marker = "sensitive-marker-123"
    issue = ProbeTool(
        description=f"Search repository issues {marker}",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "default": marker}},
            "required": ["query"],
        },
    )
    agent = make_agent(issue, Terminate(), task=f"Investigate an issue {marker}")
    agent.messages += [
        Message.tool_message(
            f"Private observation {marker}", name="probe", tool_call_id="obs"
        )
    ]
    agent.llm.ask_tool.return_value = SimpleNamespace(
        content=f"Private reasoning: {marker}",
        tool_calls=[
            ToolCall(
                id="call-1",
                function=Function(
                    name="issue_tool", arguments=json.dumps({"query": marker})
                ),
            )
        ],
    )

    with patch("app.agent.toolcall.logger.info") as info_log:
        await agent.step()

    assert len(routing_events(info_log)) == 1
    assert marker not in "\n".join(str(entry.args) for entry in info_log.call_args_list)
    assert issue.calls == [{"query": marker}]


@pytest.mark.asyncio
async def test_mcp_identity_with_url_credentials_is_logged_as_stable_hash():
    marker = "sensitive-marker-123"
    mcp_tool = MCPClientTool(
        name=f"mcp_{marker}_remote_probe",
        original_name="remote_probe",
        server_id=f"https://user:{marker}@example.invalid",
        description="Search repository issues",
        parameters={"type": "object", "properties": {}},
    )
    agent = make_agent(mcp_tool)
    agent.llm.ask_tool.return_value = SimpleNamespace(
        content=None,
        tool_calls=[
            ToolCall(id="call-1", function=Function(name=mcp_tool.name, arguments="{}"))
        ],
    )

    with patch("app.agent.toolcall.logger.info") as info_log:
        await agent.think()

    (event,) = routing_events(info_log)
    hit = event["candidates"][0]
    assert marker not in str(event)
    assert hit["source"] == "mcp"
    assert hit["identity"].startswith("mcp:sha256:")
    assert hit["name"] == event["exposed_tools"][0]["name"]
    assert event["selected_tools"][0]["name"] == hit["name"]


@pytest.mark.asyncio
async def test_no_match_event_records_zero_exposure_and_no_model_call():
    agent = make_agent(ProbeTool(), Terminate(), task="What is tomorrow's weather?")

    with patch("app.agent.toolcall.logger.info") as info_log:
        result = await agent.step()

    (event,) = routing_events(info_log)
    assert "clarif" in result.lower()
    agent.llm.ask_tool.assert_not_awaited()
    assert event["candidate_status"] == "no_match"
    assert event["selection_status"] == "not_called"
    assert event["k_business"] == event["k_total"] == 0
    assert event["exposed_tools"] == []
    assert event["selected_tools"] == []
    assert event["candidates"] and all(
        not hit["exposed"] for hit in event["candidates"]
    )


@pytest.mark.asyncio
async def test_empty_pool_event_has_no_index_version_or_selection():
    agent = make_agent(task="Investigate an issue")

    with patch("app.agent.toolcall.logger.info") as info_log:
        await agent.step()

    (event,) = routing_events(info_log)
    assert event["candidate_status"] == "no_business_tool"
    assert event["index_version"] is None
    assert event["metadata_version"] is None
    assert event["selected_tools"] == []


@pytest.mark.asyncio
async def test_pending_control_only_event_reports_no_business_match():
    agent = make_agent(
        ProbeTool(), AskHuman(), Terminate(), task="What is tomorrow's weather?"
    )
    missing = ToolCall(
        id="missing",
        function=Function(name="issue_tool", arguments="{}"),
    )
    with patch.object(AskHuman, "execute", new_callable=AsyncMock, return_value=""):
        await agent.execute_tool(missing)

    with patch("app.agent.toolcall.logger.info") as info_log:
        await agent.think()

    (event,) = routing_events(info_log)
    assert event["candidate_status"] == "no_match"
    assert event["k_business"] == 0
    assert event["k_total"] == 2
    assert [item["name"] for item in event["exposed_tools"]] == [
        "ask_human",
        "terminate",
    ]


@pytest.mark.asyncio
async def test_routing_disabled_emits_no_routing_event():
    agent = make_agent(ProbeTool(), Terminate(), k=None)

    with patch("app.agent.toolcall.logger.info") as info_log:
        await agent.think()

    assert routing_events(info_log) == []


@pytest.mark.asyncio
async def test_index_failure_emits_event_without_error_details():
    class FailingVectors(FixedVectors):
        async def embed(self, texts):
            raise RuntimeError("sensitive-marker-123")

    agent = make_agent(ProbeTool(), backend=FailingVectors())

    with patch("app.agent.toolcall.logger.info") as info_log:
        await agent.step()

    (event,) = routing_events(info_log)
    assert event["candidate_status"] == "index_error"
    assert event["selected_tools"] == []
    assert "sensitive-marker-123" not in str(event)


@pytest.mark.asyncio
async def test_none_model_response_is_distinct_from_no_tool_selection():
    agent = make_agent(ProbeTool())
    agent.llm.ask_tool.return_value = None

    with patch("app.agent.toolcall.logger.info") as info_log:
        await agent.think()

    (event,) = routing_events(info_log)
    assert event["selection_status"] == "no_response"
    assert event["selected_tools"] == []


@pytest.mark.asyncio
async def test_llm_error_event_omits_exception_details():
    agent = make_agent(ProbeTool())
    agent.llm.ask_tool.side_effect = RuntimeError("sensitive-marker-123")

    with patch("app.agent.toolcall.logger.info") as info_log:
        with pytest.raises(RuntimeError):
            await agent.think()

    (event,) = routing_events(info_log)
    assert event["selection_status"] == "llm_error"
    assert "sensitive-marker-123" not in str(event)
