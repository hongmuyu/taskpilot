"""T067 runner invariants and the saved live-run evidence contract."""

import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from eval.t067_full_tool_baseline import (
    First503Transport,
    FullToolAudit,
    load_frozen_holdout,
    new_case_llm,
)


RESULTS = Path(__file__).resolve().parents[2] / "eval/results"


def _saved_run():
    rows = [
        json.loads(line)
        for line in (RESULTS / "t067_full_tools_v1.jsonl").read_text().splitlines()
    ]
    return rows[0], {row["sample_id"]: row for row in rows[1:]}


def test_t067_uses_all_21_frozen_samples_and_scoring_hash():
    holdout = load_frozen_holdout()
    assert holdout["version"] == "1.0.0"
    assert len(holdout["samples"]) == 21
    assert {sample["id"] for sample in holdout["samples"]} == {
        f"core-{number:03}" for number in range(1, 22)
    }


@pytest.mark.asyncio
async def test_full_tool_audit_checks_every_llm_call_not_just_first():
    schemas = [
        {"type": "function", "function": {"name": "read", "parameters": {}}},
        {"type": "function", "function": {"name": "terminate", "parameters": {}}},
    ]

    async def ask_tool(**kwargs):
        return None

    agent = SimpleNamespace(
        available_tools=SimpleNamespace(to_params=lambda: schemas),
        current_step=1,
        routing_top_k=None,
        routing_retriever=None,
        llm=SimpleNamespace(ask_tool=ask_tool),
    )
    audit = FullToolAudit(agent)
    audit.install()
    try:
        await agent.llm.ask_tool(tools=schemas)
        assert audit.calls[0]["full_pool_verified"] is True
        with pytest.raises(ValueError, match="full tool schemas"):
            await agent.llm.ask_tool(tools=schemas[:1])
        assert len(audit.calls) == 2
        assert audit.calls[1]["full_pool_verified"] is False
    finally:
        audit.restore()
    assert agent.llm.ask_tool is ask_tool


@pytest.mark.asyncio
async def test_controlled_503_affects_only_first_matching_transport_request():
    downstream_requests = []

    async def downstream(request):
        downstream_requests.append(request.url.path)
        return httpx.Response(200, json={"path": "README.md"})

    path = "/repos/encode/httpx/contents/README.md"
    transport = First503Transport(httpx.MockTransport(downstream), path)
    async with httpx.AsyncClient(transport=transport) as client:
        first = await client.get(f"https://api.github.com{path}")
        second = await client.get(f"https://api.github.com{path}")
    assert [first.status_code, second.status_code] == [503, 200]
    assert downstream_requests == [path]
    assert [event["origin"] for event in transport.events] == [
        "controlled_first_503",
        "downstream_transport",
    ]


@pytest.mark.asyncio
async def test_each_case_has_an_independent_llm_client():
    first = new_case_llm("core-007")
    second = new_case_llm("core-013")
    try:
        assert first is not second
        assert first.client is not second.client
        assert first.model == second.model
        await first.client.close()
        assert not second.client.is_closed()
    finally:
        await second.client.close()


def test_saved_live_baseline_covers_all_holdout_cases_and_full_schemas():
    manifest, cases = _saved_run()
    holdout = load_frozen_holdout()
    assert manifest["dataset_version"] == holdout["version"]
    assert manifest["scoring_sha256"] == holdout["scoring_sha256"]
    assert set(cases) == {sample["id"] for sample in holdout["samples"]}
    assert manifest["selected_sample_count"] == len(cases) == 21
    assert all(item["matched"] for item in manifest["source_check"]["issues"])
    assert all(item["matched"] for item in manifest["source_check"]["pinned_files"])
    assert manifest["fixed_execution"]["full_tool_filter"] == (
        "routing_top_k=None; routing_retriever=None"
    )
    calls = [call for row in cases.values() for call in row["schema_calls"]]
    assert len(calls) == 44
    assert all(
        call["full_pool_verified"]
        and call["sent_names"] == call["expected_names"]
        and call["sent_schema_sha256"] == call["expected_schema_sha256"]
        for call in calls
    )
    for row in cases.values():
        assert row["routing_top_k"] is None
        assert row["routing_events"] == []
        assert row["embedding_events"] == 0
        assert row["trace_validation"] == "valid"
        assert row["cleanup_status"] == "complete"
        assert row["run"]["run_status"] in {
            "returned",
            "controlled_boundary_returned",
        }
        assert row["tool_pool"]["control_tools"] == ["terminate"]
        assert len(row["tool_pool"]["all_tools"]) == 6
        assert (
            row["full_schema_exposure"] is True
            if row["schema_calls"]
            else (row["full_schema_exposure"] == "NO_LLM_TOOL_CALL")
        )


def test_saved_controlled_cases_keep_call_ids_and_real_failure_evidence():
    _, cases = _saved_run()
    for sample_id in ("core-007", "core-008", "core-014"):
        row = cases[sample_id]
        assert row["run"]["dispatch_before_reply"] == 0
        assert row["run"]["tool_message_call_ids"] == [
            row["run"]["injected_tool_call_id"]
        ]
        assert (
            len(
                [
                    event
                    for event in row["trace"]
                    if event["event"] == "tool_execution"
                    and event["status"] == "started"
                ]
            )
            == 1
        )
    for sample_id in ("core-009", "core-010"):
        row = cases[sample_id]
        assert row["github_http"] == []
        assert row["run"]["tool_message_call_ids"] == [
            row["run"]["injected_tool_call_id"]
        ]
    assert len(cases["core-013"]["mcp_audit"]) == 1
    assert len(cases["core-014"]["mcp_audit"]) == 1
    assert len(cases["core-015"]["mcp_audit"]) == 1
    assert len(cases["core-016"]["mcp_audit"]) == 1
    assert "Status: failure" in cases["core-016"]["run"]["observation"]
    assert len(cases["core-017"]["mcp_audit"]) == 1
    assert "Status: unknown" in cases["core-017"]["run"]["observation"]
    assert "Error kind: timeout" in cases["core-017"]["run"]["observation"]
    assert [event["http_status"] for event in cases["core-018"]["github_http"]] == [404]
    assert "Status: failure" in cases["core-018"]["run"]["observation"]
    assert [event["http_status"] for event in cases["core-019"]["github_http"]] == [
        503,
        200,
    ]
    assert [event["origin"] for event in cases["core-019"]["github_http"]] == [
        "controlled_first_503",
        "downstream_transport",
    ]
    assert "Status: success" in cases["core-019"]["run"]["observation"]


def test_review_retains_non_success_and_failed_preflight_attempts():
    _, cases = _saved_run()
    review = json.loads((RESULTS / "t067_full_tools_review.json").read_text())
    assert {item["sample_id"] for item in review["samples"]} == set(cases)
    assert Counter(item["outcome"] for item in review["samples"]) == {
        "success": 19,
        "not_verified": 2,
    }
    assert {
        item["sample_id"]
        for item in review["samples"]
        if item["outcome"] == "not_verified"
    } == {
        "core-005",
        "core-006",
    }
    failed_preflight = [
        json.loads(line)
        for line in (RESULTS / "t067_full_tools_preflight_retry_2.jsonl")
        .read_text()
        .splitlines()
    ]
    assert any(
        row.get("sample_id") == "core-013" and row["run"]["run_status"] == "error"
        for row in failed_preflight
    )
