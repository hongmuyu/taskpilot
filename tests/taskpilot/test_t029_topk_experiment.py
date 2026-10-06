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
    from app.taskpilot.tool_embedding_index import IndexVersion, InMemoryToolIndex, ToolMatch
    from eval.t029_topk_experiment import run_case


DATASET = Path(__file__).resolve().parents[2] / "eval/datasets/routing_v1_experiment.json"


def sample(sample_id):
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    return next(item for item in data["samples"] if item["id"] == sample_id)


def fake_llm(response=None):
    llm = object.__new__(LLM)
    llm.ask_tool = AsyncMock(
        return_value=response or SimpleNamespace(content="", tool_calls=[])
    )
    return llm


def fake_retriever():
    retriever = SemanticToolRetriever(InMemoryToolIndex(object()))
    retriever.retrieve = AsyncMock(
        return_value=RetrievalResult(
            query="Original user task: Find recent issues mentioning MCP.",
            matches=(ToolMatch("github_issue_search", "local:github_issue_search", 0.8),),
            index_version=IndexVersion("fixed", "v1", "fixed-vectors", "fingerprint"),
            observation_tool_call_id=None,
        )
    )
    return retriever


@pytest.mark.asyncio
async def test_full_baseline_sends_all_schemas_without_retrieval():
    client = GitHubClient(http_client=httpx.AsyncClient(base_url="https://example.test"))
    retriever = fake_retriever()
    try:
        record = await run_case(sample("exp-004"), "full", fake_llm(), client, retriever)
    finally:
        await client.aclose()

    assert record["filter_bypassed"] is True
    assert record["routing_events"] == []
    assert record["sent_tools"] == [
        "github_repository_info",
        "github_issue_search",
        "github_issue_detail",
        "github_code_search",
        "github_read_file",
        "terminate",
    ]
    retriever.retrieve.assert_not_awaited()


@pytest.mark.asyncio
async def test_routed_and_full_groups_share_messages_and_registry():
    client = GitHubClient(http_client=httpx.AsyncClient(base_url="https://example.test"))
    retriever = fake_retriever()
    llm = fake_llm()
    try:
        full = await run_case(sample("exp-004"), "full", llm, client, retriever)
        routed = await run_case(sample("exp-004"), "k1", llm, client, retriever)
    finally:
        await client.aclose()

    assert full["input_messages_sha256"] == routed["input_messages_sha256"]
    assert full["tool_registry"] == routed["tool_registry"]
    assert routed["sent_tools"] == ["github_issue_search", "terminate"]
    assert routed["routing_events"][0]["candidates"][0]["score"] == 0.8
    retriever.retrieve.assert_awaited_once()


@pytest.mark.asyncio
async def test_model_error_records_type_without_error_message():
    client = GitHubClient(http_client=httpx.AsyncClient(base_url="https://example.test"))
    llm = fake_llm()
    llm.ask_tool.side_effect = RuntimeError("private-value-must-not-be-logged")
    try:
        record = await run_case(sample("exp-004"), "full", llm, client, fake_retriever())
    finally:
        await client.aclose()

    assert record["error"] == {"type": "RuntimeError", "status_code": None}
    assert "private-value-must-not-be-logged" not in json.dumps(record)
