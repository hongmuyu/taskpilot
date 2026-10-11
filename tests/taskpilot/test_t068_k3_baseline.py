"""T068 K=3 harness invariants and saved real-run evidence integrity."""

import hashlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval.t067_full_tool_baseline import load_frozen_holdout
from eval.t068_k3_baseline import TopKAudit, compare_fixed_contract


ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "eval/results"


def _lines(name: str) -> list[dict]:
    return [json.loads(line) for line in (RESULTS / name).read_text().splitlines()]


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


@pytest.mark.asyncio
async def test_k3_audit_preserves_control_and_limits_business_schemas():
    full = [_schema(name) for name in ("one", "two", "three", "four", "terminate")]

    async def ask_tool(**kwargs):
        return None

    agent = SimpleNamespace(
        available_tools=SimpleNamespace(to_params=lambda: full),
        current_step=2,
        routing_top_k=3,
        routing_retriever=object(),
        llm=SimpleNamespace(ask_tool=ask_tool),
        _routing_tool_label=lambda name: name,
    )
    audit = TopKAudit(agent)
    audit.install()
    try:
        await agent.llm.ask_tool(tools=[full[0], full[1], full[2], full[4]])
        assert audit.calls[0]["top_k_verified"] is True
        assert audit.calls[0]["business_names"] == ["one", "two", "three"]
        assert audit.calls[0]["control_names"] == ["terminate"]
        with pytest.raises(ValueError, match="K=3 schema boundary"):
            await agent.llm.ask_tool(tools=full)
        with pytest.raises(ValueError, match="K=3 schema boundary"):
            await agent.llm.ask_tool(tools=[full[0], _schema("unknown"), full[4]])
    finally:
        audit.restore()
    assert agent.llm.ask_tool is ask_tool


def test_comparison_contract_rejects_any_non_routing_change():
    prior = {
        "dataset_sha256": "dataset",
        "scoring_sha256": "scoring",
        "source_catalog_sha256": "sources",
        "product_app_tree": "app",
        "profiles": [{"id": "github_readonly"}],
        "model_config": {"model": "deepseek-flash", "temperature": 0.0},
        "fixed_execution": {
            "max_steps": 8,
            "agent_timeout_seconds": 180,
            "full_tool_filter": "routing_top_k=None; routing_retriever=None",
        },
    }
    current = {
        **prior,
        "fixed_execution": {
            **prior["fixed_execution"],
            "full_tool_filter": "routing_top_k=3",
        },
    }
    compare_fixed_contract(prior, current)
    altered = {**current, "model_config": {"model": "other", "temperature": 0.0}}
    with pytest.raises(ValueError, match="model_config"):
        compare_fixed_contract(prior, altered)


def test_saved_three_group_records_cover_the_same_frozen_21_samples():
    holdout = load_frozen_holdout()
    ids = {sample["id"] for sample in holdout["samples"]}
    upstream = _lines("t066_original_openmanus_v1.jsonl")
    full = _lines("t067_full_tools_v1.jsonl")
    k3 = _lines("t068_k3_v1.jsonl")
    upstream_review = json.loads(
        (RESULTS / "t066_original_openmanus_review.json").read_text()
    )
    assert len(upstream) == 21
    assert len(full) == len(k3) == 22
    assert {row["sample_id"] for row in upstream} == ids
    assert {row["sample_id"] for row in upstream_review["samples"]} == ids
    assert {row["sample_id"] for row in full[1:]} == ids
    assert {row["sample_id"] for row in k3[1:]} == ids
    assert all(item["matched"] for item in k3[0]["source_check"]["issues"])
    assert all(item["matched"] for item in k3[0]["source_check"]["pinned_files"])
    compare_fixed_contract(full[0], k3[0])
    assert full[0]["environment"] == k3[0]["environment"]
    assert (
        k3[0]["t067_manifest_sha256"]
        == hashlib.sha256(
            (RESULTS / "t067_full_tools_v1.jsonl").read_bytes().splitlines()[0] + b"\n"
        ).hexdigest()
    )
    assert (
        hashlib.sha256((ROOT / "eval/t068_k3_baseline.py").read_bytes()).hexdigest()
        == k3[0]["runner_sha256"]
    )
    by_full = {row["sample_id"]: row for row in full[1:]}
    for row in k3[1:]:
        prior = by_full[row["sample_id"]]
        assert (
            row["task_sha256"],
            row["profile"],
            row["context"],
            row["setup_mode"],
            row["tool_pool"],
        ) == (
            prior["task_sha256"],
            prior["profile"],
            prior["context"],
            prior["setup_mode"],
            prior["tool_pool"],
        )


def test_saved_k3_calls_have_scored_events_real_execution_and_cost_evidence():
    rows = _lines("t068_k3_v1.jsonl")
    manifest = rows[0]
    assert manifest["embedding"]["model_id"] == (
        "sentence-transformers/paraphrase-MiniLM-L3-v2"
    )
    assert manifest["embedding"]["model_revision"]
    assert manifest["selected_sample_count"] == 21
    assert sum(len(row["schema_calls"]) for row in rows[1:]) == 48
    for row in rows[1:]:
        assert row["routing_top_k"] == 3
        assert row["trace_validation"] == "valid"
        assert row["cleanup_status"] == "complete"
        assert row["elapsed_ms"] > 0
        assert row["trace_costs"]["latency_ms"]
        assert row["run"]["run_status"] in {
            "returned",
            "controlled_boundary_returned",
        }
        if row["setup_mode"] == "natural_task":
            assert row["schema_calls"]
            assert row["top_k_schema_exposure"] is True
            assert row["routing_audit"]["all_calls_correlated"]
            assert row["provider_usage"]["total"]["value"] is not None
            assert row["embedding_attempts"]
            for event in row["routing_events"]:
                assert event["candidate_status"] == "ready"
                assert event["k_business"] == 3
                assert event["k_total"] == 4
                assert len(event["candidates"]) == 5
                assert all(
                    isinstance(hit["score"], float) for hit in event["candidates"]
                )
                assert {item["name"] for item in event["selected_tools"]} <= {
                    item["name"] for item in event["exposed_tools"]
                }
                version = event["index_version"]
                assert version["model_id"] == manifest["embedding"]["model_id"]
                assert (
                    version["model_revision"] == manifest["embedding"]["model_revision"]
                )
                assert (
                    version["vector_version"] == manifest["embedding"]["vector_version"]
                )
        else:
            assert row["schema_calls"] == row["routing_events"] == []
            assert row["top_k_schema_exposure"] == "NO_LLM_TOOL_CALL"
            assert row["provider_usage"] == "NO_LLM_CALL"


def test_saved_k3_preserves_clarification_faults_and_non_success_rows():
    cases = {row["sample_id"]: row for row in _lines("t068_k3_v1.jsonl")[1:]}
    review = json.loads((RESULTS / "t068_k3_review.json").read_text())
    assert {item["sample_id"] for item in review["samples"]} == set(cases)
    assert Counter(item["outcome"] for item in review["samples"]) == {
        "success": 18,
        "not_verified": 3,
    }
    for sample_id in ("core-007", "core-008", "core-014"):
        run = cases[sample_id]["run"]
        assert run["dispatch_before_reply"] == 0
        assert run["tool_message_call_ids"] == [run["injected_tool_call_id"]]
    for sample_id in ("core-009", "core-010"):
        assert cases[sample_id]["github_http"] == []
        assert "Error" in cases[sample_id]["run"]["observation"]
    assert [event["http_status"] for event in cases["core-018"]["github_http"]] == [404]
    assert [event["http_status"] for event in cases["core-019"]["github_http"]] == [
        503,
        200,
    ]
    assert [event["origin"] for event in cases["core-019"]["github_http"]] == [
        "controlled_first_503",
        "downstream_transport",
    ]
    assert len(cases["core-016"]["mcp_audit"]) == 1
    assert "Status: failure" in cases["core-016"]["run"]["observation"]
    assert len(cases["core-017"]["mcp_audit"]) == 1
    assert "Status: unknown" in cases["core-017"]["run"]["observation"]
    assert "Error kind: timeout" in cases["core-017"]["run"]["observation"]
    assert [event["http_status"] for event in cases["core-005"]["github_http"]].count(
        403
    ) == 2
    assert [event["http_status"] for event in cases["core-006"]["github_http"]].count(
        403
    ) == 3
