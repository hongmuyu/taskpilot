import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import Field

from app.llm import LLM, _trace_llm_retry_sleep, llm_attempt_scope
from app.taskpilot.execution_trace import (
    ExecutionTrace,
    summarize_trace_costs,
    validate_trace_events,
)
from app.taskpilot.tool_embedding_index import (
    EmbeddingIndexError,
    InMemoryToolIndex,
    embedding_event_scope,
)
from app.tool.base import BaseTool, ToolFailure
from app.tool.tool_collection import ToolCollection, tool_event_scope


def trace_started():
    trace = ExecutionTrace()
    trace.record("task", "started", step=0)
    trace.record("step", "started", step=1)
    trace.record("llm", "started", step=1)
    return trace


def finish(trace, *, duration_ms=100):
    trace.record("step", "success", step=1)
    trace.record(
        "finish",
        "success",
        step=1,
        duration_ms=duration_ms,
        finish_source="agent_declaration",
        business_outcome="not_verified",
    )
    validate_trace_events(trace.events)
    return summarize_trace_costs(trace.events)


def test_provider_attempts_are_attributed_once_and_missing_usage_stays_unknown():
    trace = trace_started()
    trace.record(
        "llm_provider_attempt",
        "failure",
        step=1,
        llm_attempt=1,
        duration_ms=10,
        usage_source="unknown",
    )
    trace.record("llm_retry_wait", "success", step=1, duration_ms=4)
    trace.record(
        "llm_provider_attempt",
        "success",
        step=1,
        llm_attempt=2,
        duration_ms=20,
        usage_source="provider",
        input_tokens=7,
        output_tokens=3,
        total_tokens=10,
    )
    trace.record("llm", "success", step=1, duration_ms=45)
    cost = finish(trace)
    assert cost["provider_attempts"] == 2
    assert cost["tokens"]["input"]["value"] is None
    assert cost["tokens"]["input"]["known_sum"] == 7
    assert cost["tokens"]["input"]["unknown_attempts"] == 1
    assert cost["tokens"]["total"]["known_sum"] == 10
    assert cost["latency_ms"]["llm"] == 45
    assert cost["latency_ms"]["retry"] == 4
    assert cost["latency_ms"]["end_to_end"] == 100


def test_tool_attempts_retry_embedding_and_wait_are_not_double_counted():
    trace = trace_started()
    trace.record(
        "llm_provider_attempt",
        "success",
        step=1,
        llm_attempt=1,
        duration_ms=5,
        usage_source="provider",
        input_tokens=2,
        output_tokens=1,
        total_tokens=3,
    )
    trace.record("llm", "success", step=1, duration_ms=6)
    trace.record("embedding", "success", step=1, embedding_phase="query", duration_ms=4)
    trace.record("llm_selection", "success", step=1, call_id="a", tool="probe")
    trace.record("validation", "success", step=1, call_id="a", tool="probe")
    trace.record(
        "tool_execution", "started", step=1, call_id="a", tool="probe", attempt=1
    )
    trace.record(
        "tool_execution",
        "failure",
        step=1,
        call_id="a",
        tool="probe",
        attempt=1,
        duration_ms=8,
    )
    trace.record(
        "retry_wait", "success", step=1, call_id="a", tool="probe", duration_ms=3
    )
    trace.record(
        "retry",
        "started",
        step=1,
        call_id="a",
        tool="probe",
        attempt=2,
        classification="transient",
    )
    trace.record(
        "tool_execution", "started", step=1, call_id="a", tool="probe", attempt=2
    )
    trace.record(
        "tool_execution",
        "success",
        step=1,
        call_id="a",
        tool="probe",
        attempt=2,
        duration_ms=9,
    )
    trace.record(
        "clarification", "waited", step=1, call_id="a", tool="probe", duration_ms=11
    )
    trace.record("observation", "success", step=1, call_id="a", tool="probe")
    cost = finish(trace)
    assert cost["latency_ms"] == {
        "end_to_end": 100,
        "llm": 6,
        "embedding": 4,
        "tool": 17,
        "retry": 3,
        "human_wait": 11,
    }
    assert cost["tokens"]["total"]["value"] == 3


def test_costs_remain_run_scoped_and_invalid_usage_is_rejected():
    first = trace_started()
    first.record(
        "llm_provider_attempt",
        "success",
        step=1,
        llm_attempt=1,
        duration_ms=2,
        usage_source="provider",
        input_tokens=1,
        output_tokens=1,
        total_tokens=2,
    )
    first.record("llm", "success", step=1, duration_ms=3)
    assert finish(first)["tokens"]["total"]["value"] == 2
    second = trace_started()
    second.record("llm", "failure", step=1, duration_ms=4, usage_source="unknown")
    assert finish(second)["tokens"]["total"]["value"] is None
    assert first.run_id != second.run_id
    invalid = trace_started()
    with pytest.raises(ValueError, match="Invalid trace event"):
        invalid.record(
            "llm_provider_attempt",
            "success",
            step=1,
            llm_attempt=1,
            duration_ms=1,
            usage_source="unknown",
            input_tokens=5,
        )


def test_partial_provider_usage_does_not_invent_total_tokens():
    trace = trace_started()
    trace.record(
        "llm_provider_attempt",
        "success",
        step=1,
        llm_attempt=1,
        duration_ms=2,
        usage_source="provider",
        input_tokens=7,
        output_tokens=2,
    )
    trace.record("llm", "success", step=1, duration_ms=3)
    cost = finish(trace)
    assert cost["tokens"]["input"]["value"] == 7
    assert cost["tokens"]["output"]["value"] == 2
    assert cost["tokens"]["total"] == {
        "value": None,
        "known_sum": 0,
        "unknown_attempts": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage, expected",
    [
        (
            SimpleNamespace(prompt_tokens=7, completion_tokens=2, total_tokens=9),
            "provider",
        ),
        (None, "unknown"),
    ],
)
async def test_real_ask_tool_response_boundary_reports_only_provider_usage(
    usage, expected
):
    llm = object.__new__(LLM)
    llm.model = "fixture"
    llm.max_tokens = 100
    llm.temperature = 0
    llm.total_input_tokens = 0
    llm.total_completion_tokens = 0
    llm.count_message_tokens = lambda messages: 1
    llm.check_token_limit = lambda count: True
    llm.format_messages = lambda messages, supports_images: messages
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], usage=usage
    )
    create = AsyncMock(return_value=response)
    llm.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    captured = []
    with llm_attempt_scope(lambda status, details: captured.append((status, details))):
        message = await LLM.ask_tool.__wrapped__(
            llm, messages=[{"role": "user", "content": "hi"}]
        )
    assert message.content == "ok"
    assert create.await_count == 1
    assert len(captured) == 1
    assert captured[0][0] == "success"
    assert captured[0][1]["usage_source"] == expected
    if expected == "provider":
        assert (captured[0][1]["input_tokens"], captured[0][1]["total_tokens"]) == (
            7,
            9,
        )
    else:
        assert "input_tokens" not in captured[0][1]


@pytest.mark.asyncio
async def test_cancelled_provider_attempt_keeps_unknown_usage_and_elapsed_time():
    llm = object.__new__(LLM)
    llm.model = "fixture"
    llm.max_tokens = 100
    llm.temperature = 0
    llm.count_message_tokens = lambda messages: 1
    llm.check_token_limit = lambda count: True
    llm.format_messages = lambda messages, supports_images: messages
    create = AsyncMock(side_effect=asyncio.CancelledError())
    llm.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    captured = []
    with llm_attempt_scope(lambda status, details: captured.append((status, details))):
        with pytest.raises(asyncio.CancelledError):
            await LLM.ask_tool.__wrapped__(
                llm, messages=[{"role": "user", "content": "hi"}]
            )
    assert len(captured) == 1
    assert captured[0][0] == "cancelled"
    assert captured[0][1]["usage_source"] == "unknown"
    assert captured[0][1]["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_cancelled_llm_retry_wait_is_measured_without_a_new_attempt():
    captured = []
    with llm_attempt_scope(lambda status, details: captured.append((status, details))):
        task = asyncio.create_task(_trace_llm_retry_sleep(10))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(captured) == 1
    assert captured[0][0] == "retry_wait_cancelled"
    assert captured[0][1]["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_concurrent_tasks_share_llm_without_sharing_reported_usage():
    llm = object.__new__(LLM)
    llm.model = "fixture"
    llm.max_tokens = 100
    llm.temperature = 0
    llm.total_input_tokens = 0
    llm.total_completion_tokens = 0
    llm.count_message_tokens = lambda messages: 1
    llm.check_token_limit = lambda count: True
    llm.format_messages = lambda messages, supports_images: messages

    async def create(**params):
        await asyncio.sleep(0)
        amount = int(params["messages"][0]["content"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(
                prompt_tokens=amount, completion_tokens=1, total_tokens=amount + 1
            ),
        )

    llm.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    async def run_one(amount):
        trace = trace_started()
        with llm_attempt_scope(
            lambda status, details: trace.record(
                "llm_provider_attempt", status, step=1, llm_attempt=1, **details
            )
        ):
            await LLM.ask_tool.__wrapped__(
                llm, messages=[{"role": "user", "content": str(amount)}]
            )
        trace.record("llm", "success", step=1, duration_ms=1)
        return trace.run_id, finish(trace)["tokens"]["total"]["value"]

    first, second = await asyncio.gather(run_one(1), run_one(9))
    assert first[0] != second[0]
    assert (first[1], second[1]) == (2, 10)
    assert llm.total_input_tokens == 10


class TransientRead(BaseTool):
    name: str = "transient_read"
    description: str = "Fixture read"
    parameters: dict = {"type": "object", "additionalProperties": False}
    retry_safe_read: bool = True
    calls: int = Field(default=0)

    async def execute(self, **kwargs):
        self.calls += 1
        return ToolFailure(error="temporary", retry_classification="transient")


class TimeoutEmbedding:
    model_id = "fixture"
    revision = "v1"
    vector_version = "v1"

    async def embed(self, texts):
        raise TimeoutError("fixture")


@pytest.mark.asyncio
async def test_embedding_timeout_cost_is_unknown_and_measured():
    events = []
    with embedding_event_scope(
        lambda status, details: events.append((status, details))
    ):
        with pytest.raises(EmbeddingIndexError):
            await InMemoryToolIndex(TimeoutEmbedding()).refresh(
                ToolCollection(TransientRead())
            )
    assert len(events) == 1
    assert events[0][0] == "unknown"
    assert events[0][1]["embedding_phase"] == "index"
    assert events[0][1]["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_cancelled_tool_backoff_records_wait_without_starting_retry(monkeypatch):
    waiting = asyncio.Event()

    async def wait_until_cancelled(delay):
        waiting.set()
        await asyncio.Future()

    monkeypatch.setattr("app.tool.tool_collection.asyncio.sleep", wait_until_cancelled)
    tool = TransientRead(timeout_seconds=10)
    events = []
    with tool_event_scope(
        lambda event, status, details: events.append((event, status, details))
    ):
        task = asyncio.create_task(
            ToolCollection(tool).execute(name=tool.name, tool_input={})
        )
        await waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert tool.calls == 1
    assert [status for event, status, _ in events if event == "retry_wait"] == [
        "cancelled"
    ]
    assert not any(event == "retry" for event, _, _ in events)
    assert events[-1][2]["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_outer_llm_retry_records_each_provider_attempt_and_actual_wait(
    monkeypatch,
):
    llm = object.__new__(LLM)
    llm.model = "fixture"
    llm.max_tokens = 100
    llm.temperature = 0
    llm.total_input_tokens = 0
    llm.total_completion_tokens = 0
    llm.count_message_tokens = lambda messages: 1
    llm.check_token_limit = lambda count: True
    llm.format_messages = lambda messages, supports_images: messages
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        usage=SimpleNamespace(prompt_tokens=4, completion_tokens=1, total_tokens=5),
    )
    create = AsyncMock(side_effect=[Exception("transient fixture"), response])
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    client.with_options = Mock(return_value=client)
    llm.client = client
    monkeypatch.setattr("app.llm.asyncio.sleep", AsyncMock(return_value=None))
    captured = []
    with llm_attempt_scope(lambda status, details: captured.append((status, details))):
        message = await llm.ask_tool(messages=[{"role": "user", "content": "hi"}])
    assert message.content == "ok"
    assert create.await_count == 2
    assert [status for status, _ in captured] == [
        "failure",
        "retry_wait_success",
        "success",
    ]
    assert captured[0][1]["usage_source"] == "unknown"
    assert captured[-1][1]["total_tokens"] == 5
    assert captured[1][1]["duration_ms"] >= 0
    client.with_options.assert_called_with(max_retries=0)
