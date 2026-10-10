import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
    from app.agent.toolcall import ToolCallAgent
    from app.llm import LLM
    from app.logger import logger
    from app.schema import Function, ToolCall
    from app.taskpilot.github_tools import GitHubClient, RepositoryContext
    from app.taskpilot.semantic_tool_retrieval import SemanticToolRetriever
    from app.taskpilot.tool_embedding_index import InMemoryToolIndex
    from app.tool import Terminate
    from app.tool.base import BaseTool, ToolResult
    from app.tool.tool_collection import ToolCollection


def call(name, arguments, call_id):
    return ToolCall(
        id=call_id,
        function=Function(name=name, arguments=json.dumps(arguments)),
    )


def scripted_llm(calls, *, content=None):
    llm = object.__new__(LLM)
    remaining = iter(calls)

    async def ask_tool(**kwargs):
        return SimpleNamespace(content=content, tool_calls=[next(remaining)])

    llm.ask_tool = AsyncMock(side_effect=ask_tool)
    return llm


def github_response(request):
    path = request.url.path
    if path == "/repos/example/repo":
        return httpx.Response(200, json={"full_name": "example/repo"})
    if path == "/search/issues":
        return httpx.Response(200, json={"total_count": 1, "items": [{"number": 7}]})
    if path == "/repos/example/repo/issues/7":
        return httpx.Response(
            200, json={"number": 7, "title": "Trace issue", "labels": []}
        )
    if path == "/repos/example/repo/issues/7/comments":
        return httpx.Response(200, json=[])
    if path == "/search/code":
        return httpx.Response(
            200, json={"total_count": 1, "items": [{"path": "src/a.py"}]}
        )
    if path == "/repos/example/repo/contents/src/a.py":
        return httpx.Response(
            200,
            json={
                "path": "src/a.py",
                "content": base64.b64encode(b"trace = True\n").decode(),
            },
        )
    raise AssertionError(f"unexpected fixture path: {path}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "business_calls",
    [
        [("github_repository_info", {}), ("github_read_file", {"path": "src/a.py"})],
        [
            ("github_issue_search", {"query": "trace"}),
            ("github_issue_detail", {"issue_number": 7}),
        ],
        [
            ("github_issue_detail", {"issue_number": 7}),
            ("github_code_search", {"query": "trace"}),
            ("github_read_file", {"path": "src/a.py"}),
        ],
    ],
    ids=["repository-understanding", "issue-investigation", "issue-to-code"],
)
async def test_trace_connects_three_investigation_chains(monkeypatch, business_calls):
    monkeypatch.setattr("app.agent.base.SANDBOX_CLIENT.cleanup", AsyncMock())
    client = GitHubClient(
        token="fixture",
        http_client=httpx.AsyncClient(
            base_url="https://api.github.com",
            transport=httpx.MockTransport(github_response),
        ),
    )
    calls = [
        call(name, arguments, f"call-{index}")
        for index, (name, arguments) in enumerate(business_calls, 1)
    ] + [call("terminate", {"status": "success"}, "finish-call")]
    agent = RepositoryInvestigationAgent.create(
        RepositoryContext.parse("example/repo", source="user_input"),
        client=client,
        llm=scripted_llm(calls),
        max_steps=len(calls) + 1,
    )
    try:
        await agent.run("Investigate the selected repository")
    finally:
        await client.aclose()

    events = agent.execution_trace.events
    assert events[0]["event"] == "task" and events[0]["status"] == "started"
    assert events[-1]["event"] == "finish" and events[-1]["status"] == "success"
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert {event["run_id"] for event in events} == {agent.execution_trace.run_id}
    for index in range(1, len(calls) + 1):
        linked = [
            event
            for event in events
            if event.get("call_ref")
            == agent.execution_trace.call_ref(
                f"call-{index}" if index < len(calls) else "finish-call"
            )
        ]
        assert any(
            event["event"] == "validation" and event["status"] == "success"
            for event in linked
        )
        expected = "success" if index < len(calls) else "unknown"
        assert any(
            event["event"] == "tool_execution" and event["status"] == expected
            for event in linked
        )
        assert any(
            event["event"] == "observation" and event["status"] == expected
            for event in linked
        )
    assert {event["event"] for event in events} >= {
        "task",
        "step",
        "llm",
        "validation",
        "tool_execution",
        "observation",
        "finish",
    }
    assert not any(event["event"] == "retry" for event in events)


class PathTool(BaseTool):
    name: str = "path_tool"
    description: str = "Read a path"
    parameters: dict = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }
    calls: list[dict] = Field(default_factory=list)
    output: str = "read complete"

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return ToolResult(output=self.output)


@pytest.mark.asyncio
async def test_trace_clarification_resume_keeps_call_link_and_zero_early_dispatch(
    monkeypatch,
):
    monkeypatch.setattr("app.agent.base.SANDBOX_CLIENT.cleanup", AsyncMock())
    tool = PathTool()

    def answer(prompt):
        assert tool.calls == []
        return '{"path":"README.md"}'

    monkeypatch.setattr("builtins.input", answer)
    agent = ToolCallAgent(
        llm=scripted_llm(
            [
                call("path_tool", {}, "pending-1"),
                call("terminate", {"status": "success"}, "finish-1"),
            ]
        ),
        available_tools=ToolCollection(tool, Terminate()),
        next_step_prompt="",
        max_steps=3,
    )

    await agent.run("Read a file")

    linked = [
        event
        for event in agent.execution_trace.events
        if event.get("call_ref") == agent.execution_trace.call_ref("pending-1")
    ]
    assert [event["status"] for event in linked if event["event"] == "validation"] == [
        "pending",
        "success",
    ]
    assert any(
        event["event"] == "clarification" and event["status"] == "requested"
        for event in linked
    )
    assert any(
        event["event"] == "clarification" and event["status"] == "merged"
        for event in linked
    )
    assert (
        len(
            [
                event
                for event in linked
                if event["event"] == "tool_execution" and event["status"] == "started"
            ]
        )
        == 1
    )
    assert any(
        event["event"] == "observation" and event["status"] == "success"
        for event in linked
    )
    assert tool.calls == [{"path": "README.md"}]


@pytest.mark.asyncio
async def test_trace_failure_and_retry_attempts_keep_original_call(monkeypatch):
    monkeypatch.setattr("app.agent.base.SANDBOX_CLIENT.cleanup", AsyncMock())
    attempts = 0

    def respond(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"message": "temporarily unavailable"})

    client = GitHubClient(
        http_client=httpx.AsyncClient(
            base_url="https://api.github.com", transport=httpx.MockTransport(respond)
        )
    )
    agent = RepositoryInvestigationAgent.create(
        RepositoryContext.parse("example/repo", source="user_input"),
        client=client,
        llm=scripted_llm(
            [
                call("github_repository_info", {}, "retry-1"),
                call("terminate", {"status": "failure"}, "finish-1"),
            ]
        ),
        max_steps=3,
    )
    try:
        await agent.run("Inspect failing repository")
    finally:
        await client.aclose()
    linked = [
        event
        for event in agent.execution_trace.events
        if event.get("call_ref") == agent.execution_trace.call_ref("retry-1")
    ]
    retry = [event for event in linked if event["event"] == "retry"]
    assert attempts == 3
    assert [event["attempt"] for event in retry] == [2, 3]
    assert all(
        event["status"] == "started" and event["classification"] == "transient"
        for event in retry
    )
    assert any(
        event["event"] == "observation" and event["status"] == "failure"
        for event in linked
    )
    assert agent.execution_trace.events[-1]["status"] == "failure"


@pytest.mark.asyncio
async def test_trace_rejected_arguments_never_dispatch(monkeypatch):
    monkeypatch.setattr("app.agent.base.SANDBOX_CLIENT.cleanup", AsyncMock())
    tool = PathTool()
    bad = ToolCall(
        id="bad-1", function=Function(name="path_tool", arguments="[invalid")
    )
    agent = ToolCallAgent(
        llm=scripted_llm([bad, call("terminate", {"status": "failure"}, "finish-1")]),
        available_tools=ToolCollection(tool, Terminate()),
        next_step_prompt="",
        max_steps=3,
    )
    await agent.run("Reject invalid input")
    linked = [
        event
        for event in agent.execution_trace.events
        if event.get("call_ref") == agent.execution_trace.call_ref("bad-1")
    ]
    assert any(
        event["event"] == "validation" and event["status"] == "failure"
        for event in linked
    )
    assert any(
        event["event"] == "observation" and event["status"] == "failure"
        for event in linked
    )
    assert not any(event["event"] == "tool_execution" for event in linked)
    assert tool.calls == []
    assert agent.execution_trace.events[-1]["status"] == "failure"


@pytest.mark.asyncio
async def test_trace_schema_rejection_has_no_execution_event(monkeypatch):
    monkeypatch.setattr("app.agent.base.SANDBOX_CLIENT.cleanup", AsyncMock())
    tool = PathTool()
    agent = ToolCallAgent(
        llm=scripted_llm(
            [
                call("path_tool", {"path": 17}, "schema-1"),
                call("terminate", {"status": "failure"}, "finish-1"),
            ]
        ),
        available_tools=ToolCollection(tool, Terminate()),
        next_step_prompt="",
        max_steps=3,
    )
    await agent.run("Reject invalid field type")
    linked = [
        event
        for event in agent.execution_trace.events
        if event.get("call_ref") == agent.execution_trace.call_ref("schema-1")
    ]
    assert any(
        event["event"] == "validation"
        and event["status"] == "failure"
        and event["reason"] == "schema_rejected"
        for event in linked
    )
    assert not any(event["event"] == "tool_execution" for event in linked)
    assert tool.calls == []


class SlowTool(BaseTool):
    name: str = "slow_tool"
    description: str = "Slow read"
    parameters: dict = {"type": "object", "additionalProperties": False}
    started: asyncio.Event = Field(default_factory=asyncio.Event)

    class Config:
        arbitrary_types_allowed = True

    async def execute(self, **kwargs):
        self.started.set()
        await asyncio.sleep(10)
        return ToolResult(output="late")


@pytest.mark.asyncio
async def test_trace_timeout_remains_unknown(monkeypatch):
    monkeypatch.setattr("app.agent.base.SANDBOX_CLIENT.cleanup", AsyncMock())
    tool = SlowTool(timeout_seconds=0.01)
    agent = ToolCallAgent(
        llm=scripted_llm(
            [
                call("slow_tool", {}, "timeout-1"),
                call("terminate", {"status": "failure"}, "finish-1"),
            ]
        ),
        available_tools=ToolCollection(tool, Terminate()),
        next_step_prompt="",
        max_steps=3,
    )
    await agent.run("Wait for a read")
    linked = [
        event
        for event in agent.execution_trace.events
        if event.get("call_ref") == agent.execution_trace.call_ref("timeout-1")
    ]
    assert any(
        event["event"] == "tool_execution"
        and event["status"] == "unknown"
        and event.get("error_kind") == "timeout"
        for event in linked
    )
    assert any(
        event["event"] == "observation" and event["status"] == "unknown"
        for event in linked
    )
    assert agent.execution_trace.events[-1]["status"] == "failure"


@pytest.mark.asyncio
async def test_trace_external_cancellation_is_not_success(monkeypatch):
    monkeypatch.setattr("app.agent.base.SANDBOX_CLIENT.cleanup", AsyncMock())
    tool = SlowTool()
    agent = ToolCallAgent(
        llm=scripted_llm([call("slow_tool", {}, "cancel-1")]),
        available_tools=ToolCollection(tool),
        next_step_prompt="",
        max_steps=2,
    )
    task = asyncio.create_task(agent.run("Read slowly"))
    await tool.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert agent.execution_trace.events[-1]["event"] == "finish"
    assert agent.execution_trace.events[-1]["status"] == "cancelled"
    linked = [
        event
        for event in agent.execution_trace.events
        if event.get("call_ref") == agent.execution_trace.call_ref("cancel-1")
    ]
    assert any(
        event["event"] == "tool_execution" and event["status"] == "cancelled"
        for event in linked
    )
    assert any(
        event["event"] == "observation" and event["status"] == "cancelled"
        for event in linked
    )


@pytest.mark.asyncio
async def test_trace_cancelled_clarification_never_dispatches(monkeypatch):
    monkeypatch.setattr("app.agent.base.SANDBOX_CLIENT.cleanup", AsyncMock())
    monkeypatch.setattr("builtins.input", lambda prompt: "cancel")
    tool = PathTool()
    agent = ToolCallAgent(
        llm=scripted_llm(
            [
                call("path_tool", {}, "pending-1"),
                call("terminate", {"status": "failure"}, "finish-1"),
            ]
        ),
        available_tools=ToolCollection(tool, Terminate()),
        next_step_prompt="",
        max_steps=3,
    )
    await agent.run("Read a missing path")
    linked = [
        event
        for event in agent.execution_trace.events
        if event.get("call_ref") == agent.execution_trace.call_ref("pending-1")
    ]
    assert any(
        event["event"] == "clarification" and event["status"] == "cancelled"
        for event in linked
    )
    assert not any(event["event"] == "tool_execution" for event in linked)
    assert tool.calls == []


class FixedVectors:
    model_id = "fixture"
    revision = "fixture-1"
    vector_version = "v1"

    async def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


@pytest.mark.asyncio
async def test_trace_routing_uses_same_run_and_links_selected_call(monkeypatch):
    monkeypatch.setattr("app.agent.base.SANDBOX_CLIENT.cleanup", AsyncMock())
    agent = ToolCallAgent(
        llm=scripted_llm(
            [
                call("path_tool", {"path": "README.md"}, "routed-1"),
                call("terminate", {"status": "success"}, "finish-1"),
            ]
        ),
        available_tools=ToolCollection(PathTool(), Terminate()),
        routing_top_k=1,
        routing_retriever=SemanticToolRetriever(InMemoryToolIndex(FixedVectors())),
        next_step_prompt="",
        max_steps=3,
    )
    await agent.run("Read repository documentation")
    events = agent.execution_trace.events
    routing = [event for event in events if event["event"] == "routing"]
    assert routing and all(
        event["run_id"] == agent.execution_trace.run_id for event in routing
    )
    assert routing[0]["k_business"] == 1 and routing[0]["k_total"] == 2
    assert any(
        event["event"] == "llm_selection"
        and event.get("call_ref") == agent.execution_trace.call_ref("routed-1")
        for event in events
    )
    assert any(
        event["event"] == "observation"
        and event.get("call_ref") == agent.execution_trace.call_ref("routed-1")
        for event in events
    )


@pytest.mark.asyncio
async def test_trace_and_existing_logs_redact_task_parameters_results_and_model_content(
    monkeypatch,
):
    monkeypatch.setattr("app.agent.base.SANDBOX_CLIENT.cleanup", AsyncMock())
    secret = "sk-" + "S" * 25
    records = []
    sink = logger.add(lambda message: records.append(str(message)), format="{message}")
    tool = PathTool(output=secret)
    agent = ToolCallAgent(
        llm=scripted_llm(
            [
                call("path_tool", {"path": secret}, "secret-call"),
                call("terminate", {"status": "success"}, "finish-1"),
            ],
            content=secret,
        ),
        available_tools=ToolCollection(tool, Terminate()),
        next_step_prompt="",
        max_steps=3,
    )
    try:
        await agent.run(f"Investigate {secret}")
    finally:
        logger.remove(sink)
    assert secret not in json.dumps(agent.execution_trace.events)
    assert secret not in "".join(records)
    assert any(
        event["event"] == "task" and event["task_sha256"]
        for event in agent.execution_trace.events
    )
    assert all(
        event.get("call_ref") != "secret-call" for event in agent.execution_trace.events
    )


@pytest.mark.asyncio
async def test_github_argument_and_error_text_do_not_enter_logs(monkeypatch):
    monkeypatch.setattr("app.agent.base.SANDBOX_CLIENT.cleanup", AsyncMock())
    secret = "sk-" + "Q" * 25
    records = []
    sink = logger.add(
        lambda message: records.append(str(message)), format="{message}", level="DEBUG"
    )
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(404, json={"message": secret})

    client = GitHubClient(
        http_client=httpx.AsyncClient(
            base_url="https://api.github.com", transport=httpx.MockTransport(respond)
        )
    )
    agent = RepositoryInvestigationAgent.create(
        RepositoryContext.parse("example/repo", source="user_input"),
        client=client,
        llm=scripted_llm(
            [
                call("github_issue_search", {"query": secret}, "secret-query"),
                call("terminate", {"status": "failure"}, "finish-1"),
            ]
        ),
        max_steps=3,
    )
    try:
        with patch("app.tool.base.logger.debug") as debug_log:
            PathTool().fail_response(secret)
        assert all(secret not in str(item) for item in debug_log.call_args_list)
        await agent.run("Investigate a failing query")
    finally:
        logger.remove(sink)
        await client.aclose()
    assert len(requests) == 1
    assert secret not in json.dumps(agent.execution_trace.events)
    assert secret not in "".join(records)


@pytest.mark.asyncio
async def test_cleanup_exception_text_is_not_logged():
    secret = "sk-" + "Z" * 25

    class BrokenCleanupTool(PathTool):
        async def cleanup(self):
            raise RuntimeError(secret)

    agent = ToolCallAgent(
        llm=scripted_llm([]),
        available_tools=ToolCollection(BrokenCleanupTool()),
        next_step_prompt="",
    )
    records = []
    sink = logger.add(lambda message: records.append(str(message)), format="{message}")
    try:
        await agent.cleanup()
    finally:
        logger.remove(sink)
    assert records
    assert secret not in "".join(records)
