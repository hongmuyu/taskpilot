import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest


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
    from app.llm import LLM
    from app.taskpilot.github_tools import GitHubClient
    from app.taskpilot.semantic_tool_retrieval import (
        RetrievalResult,
        SemanticToolRetriever,
    )
    from app.taskpilot.tool_embedding_index import (
        IndexVersion,
        InMemoryToolIndex,
        ToolMatch,
    )
    from eval.t030_routing_comparison import (
        EmbeddingCallRecorder,
        ProviderAttemptRecorder,
        build_agent,
        reported_usage,
        run_case,
        score_group,
    )


DATASET = (
    Path(__file__).resolve().parents[2] / "eval/datasets/routing_v1_evaluation.json"
)


def sample(sample_id):
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    return next(item for item in data["samples"] if item["id"] == sample_id)


def record(sample_id, action, acceptable, sent, selected):
    return {
        "sample_id": sample_id,
        "expected_action": action,
        "acceptable_tools": acceptable,
        "sent_tools": sent,
        "selected_tools": selected,
        "provider_attempts": [],
        "embedding_calls": [],
        "end_to_end_latency_ms": 10.0,
        "error": None,
    }


def test_tool_denominators_exclude_clarify_and_no_match_and_reject_extra_calls():
    rows = [
        record("one", "tool", ["repo"], ["repo", "terminate"], ["repo"]),
        record("two", "tool", ["read", "code"], ["issue", "terminate"], ["issue"]),
        record(
            "three",
            "tool",
            ["read", "code"],
            ["read", "code", "terminate"],
            ["read", "repo"],
        ),
        record("four", "clarify", ["issue"], ["issue", "terminate"], ["issue"]),
        record("five", "no_match", [], ["terminate"], ["terminate"]),
    ]

    result = score_group(rows)

    assert result["candidate_recall"] == {"numerator": 2, "denominator": 3}
    assert result["tool_selection_accuracy"] == {"numerator": 1, "denominator": 3}
    assert result["tool_selection_failures"] == ["two", "three"]
    assert result["clarify_samples"] == ["four"]
    assert result["no_match_samples"] == ["five"]
    assert result["no_match_terminate"] == {"numerator": 1, "denominator": 1}


def test_provider_usage_keeps_missing_fields_unavailable():
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=12, completion_tokens=None, total_tokens=None
        )
    )
    assert reported_usage(response) == {
        "input_tokens": 12,
        "output_tokens": None,
        "total_tokens": None,
    }
    assert reported_usage(SimpleNamespace(usage=None)) == {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }


def test_group_aggregates_reported_usage_retry_and_embedding_cost():
    first = record("one", "tool", ["repo"], ["repo", "terminate"], ["repo"])
    first["provider_attempts"] = [
        {"elapsed_ms": 4.0, "error_type": "TimeoutError", "usage": None},
        {
            "elapsed_ms": 7.0,
            "error_type": None,
            "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
        },
    ]
    first["embedding_calls"] = [{"input_texts": 5, "elapsed_ms": 2.0}]
    second = record("two", "clarify", ["issue"], ["terminate"], ["terminate"])
    second["provider_attempts"] = [
        {
            "elapsed_ms": 6.0,
            "error_type": None,
            "usage": {"input_tokens": 9, "output_tokens": None, "total_tokens": None},
        }
    ]
    result = score_group([first, second])
    assert result["provider_usage"]["input_tokens"] == {
        "sum": 19,
        "reported_attempts": 2,
        "successful_attempts": 2,
    }
    assert result["provider_usage"]["output_tokens"] == {
        "sum": 3,
        "reported_attempts": 1,
        "successful_attempts": 2,
    }
    assert result["retry_count"] == 1
    assert result["retry_elapsed_ms"] == 7.0
    assert result["provider_failures"] == 1
    assert result["failed_attempt_elapsed_ms"] == 4.0
    assert result["embedding_calls"] == 1
    assert result["embedding_input_texts"] == 5
    assert result["embedding_elapsed_ms"] == 2.0
    assert result["end_to_end_latency_ms"]["denominator"] == 2


@pytest.mark.asyncio
async def test_provider_attempt_recorder_counts_retries_without_error_text():
    calls = 0

    async def create(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("private-value-must-not-be-logged")
        return SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=4, total_tokens=14
            ),
            model="test-revision",
        )

    recorder = ProviderAttemptRecorder(create)
    with pytest.raises(RuntimeError):
        await recorder()
    await recorder()

    assert len(recorder.attempts) == 2
    assert recorder.attempts[0]["error_type"] == "RuntimeError"
    assert recorder.attempts[0]["usage"] is None
    assert recorder.attempts[1]["usage"]["total_tokens"] == 14
    assert "private-value-must-not-be-logged" not in json.dumps(recorder.attempts)


@pytest.mark.asyncio
async def test_embedding_recorder_counts_calls_without_storing_text():
    async def embed(texts):
        return [[1.0] for _ in texts]

    recorder = EmbeddingCallRecorder(embed)
    assert await recorder(["private-input", "another-input"]) == [[1.0], [1.0]]
    assert recorder.calls[0]["input_texts"] == 2
    assert "private-input" not in json.dumps(recorder.calls)


def test_step_two_observation_keeps_original_task_and_call_link():
    agent = build_agent(sample("eva-005"), object.__new__(LLM), GitHubClient())
    assert agent.routing_original_task == sample("eva-005")["task"]
    assert agent.messages[1].tool_calls[0].function.name == "github_code_search"
    assert agent.messages[2].tool_call_id == agent.messages[1].tool_calls[0].id
    assert agent.messages[2].content == sample("eva-005")["observation"]


@pytest.mark.asyncio
async def test_full_baseline_bypasses_retrieval_and_records_provider_usage():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[]))],
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=3, total_tokens=23),
        model="test-revision",
    )
    create = AsyncMock(return_value=response)
    llm = object.__new__(LLM)
    llm.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    async def ask_tool(**kwargs):
        return (await llm.client.chat.completions.create(**kwargs)).choices[0].message

    llm.ask_tool = ask_tool
    backend = SimpleNamespace(
        embed=AsyncMock(), model_id="fixed", revision="v1", vector_version="v1"
    )
    retriever = SemanticToolRetriever(InMemoryToolIndex(backend))
    retriever.retrieve = AsyncMock(
        return_value=RetrievalResult(
            query="Original user task: fixture",
            matches=(
                ToolMatch(
                    "github_repository_info", "local:github_repository_info", 0.8
                ),
            ),
            index_version=IndexVersion("fixed", "v1", "v1", "fingerprint"),
            observation_tool_call_id=None,
        )
    )
    client = GitHubClient(
        http_client=httpx.AsyncClient(base_url="https://example.test")
    )
    try:
        result = await run_case(
            sample("eva-001"), "full", llm, client, retriever, backend
        )
    finally:
        await client.aclose()

    assert result["filter_bypassed"] is True
    assert result["routing_events"] == []
    assert result["sent_tools"] == result["tool_registry"]
    assert result["provider_attempts"][0]["usage"]["total_tokens"] == 23
    assert result["retry_count"] == 0
    assert result["embedding_calls"] == []
    assert result["tool_dispatch_count"] == 0
    retriever.retrieve.assert_not_awaited()


@pytest.mark.asyncio
async def test_k3_and_full_share_input_and_registry_but_only_k3_routes():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[]))],
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=3, total_tokens=23),
        model="test-revision",
    )
    llm = object.__new__(LLM)
    llm.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=response))
        )
    )

    async def ask_tool(**kwargs):
        return (await llm.client.chat.completions.create(**kwargs)).choices[0].message

    llm.ask_tool = ask_tool
    backend = SimpleNamespace(
        embed=AsyncMock(), model_id="fixed", revision="v1", vector_version="v1"
    )
    retriever = SemanticToolRetriever(InMemoryToolIndex(backend))
    retriever.retrieve = AsyncMock(
        return_value=RetrievalResult(
            query="Original user task: fixture",
            matches=(
                ToolMatch(
                    "github_repository_info", "local:github_repository_info", 0.8
                ),
            ),
            index_version=IndexVersion("fixed", "v1", "v1", "fingerprint"),
            observation_tool_call_id=None,
        )
    )
    client = GitHubClient(
        http_client=httpx.AsyncClient(base_url="https://example.test")
    )
    try:
        full = await run_case(
            sample("eva-001"), "full", llm, client, retriever, backend
        )
        k3 = await run_case(sample("eva-001"), "k3", llm, client, retriever, backend)
    finally:
        await client.aclose()

    assert full["input_messages_sha256"] == k3["input_messages_sha256"]
    assert full["tool_registry"] == k3["tool_registry"]
    assert full["routing_events"] == []
    assert k3["sent_tools"] == ["github_repository_info", "terminate"]
    assert k3["routing_events"][0]["candidates"][0]["score"] == 0.8
    retriever.retrieve.assert_awaited_once()
