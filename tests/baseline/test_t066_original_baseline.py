"""Frozen original-OpenManus capability plan and redaction checks."""

import hashlib
import json
from pathlib import Path

from eval.t066_original_openmanus import classify, render_input, scrub
from eval.t066_review_results import build_review


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "eval/datasets/core_benchmark_v1_holdout.json"
SCORING = ROOT / "eval/datasets/core_benchmark_v1_scoring.json"


def test_original_baseline_preflight_covers_the_frozen_holdout():
    holdout = json.loads(DATASET.read_text())
    before = hashlib.sha256(DATASET.read_bytes()).hexdigest()
    decisions = {sample["id"]: classify(sample) for sample in holdout["samples"]}
    assert len(decisions) == 21
    supported = {
        key for key, value in decisions.items() if value["support"] == "supported"
    }
    assert supported == {
        "core-001",
        "core-002",
        "core-003",
        "core-004",
        "core-005",
        "core-006",
        "core-013",
        "core-015",
        "core-016",
        "core-018",
        "core-020",
        "core-021",
    }
    assert all(value["reason"] for value in decisions.values())
    assert all(
        value["support"] == "unsupported"
        for key, value in decisions.items()
        if key not in supported
    )
    assert hashlib.sha256(DATASET.read_bytes()).hexdigest() == before
    assert holdout["scoring_sha256"] == hashlib.sha256(SCORING.read_bytes()).hexdigest()


def test_render_input_uses_frozen_task_and_confirmed_context_only():
    samples = {item["id"]: item for item in json.loads(DATASET.read_text())["samples"]}
    github = render_input(samples["core-001"])
    assert samples["core-001"]["task"] in github
    assert "encode/httpx" in github
    assert samples["core-001"]["context"]["ref"] in github
    assert "read-only" in github
    assert "github_read_file" not in github
    mcp = render_input(samples["core-013"])
    assert samples["core-013"]["task"] in mcp
    assert "Repository:" not in mcp


def test_scrub_hides_credential_values_and_common_token_shapes():
    raw = "configured-key GH_TOKEN " + "ghp_" + "A" * 30 + " sk-" + "b" * 30
    cleaned = scrub(raw, ["configured-key"])
    assert "configured-key" not in cleaned
    assert "ghp_" not in cleaned
    assert "sk-" not in cleaned
    assert "[REDACTED]" in cleaned


def test_review_preserves_all_frozen_samples_and_failed_retests():
    review = build_review()
    cases = {row["sample_id"]: row for row in review["samples"]}
    assert len(cases) == 21
    assert review["upstream_commit"] == "3309bf4e416fb1c74b008f3e86494439a31bad53"
    assert review["dataset_sha256"] == hashlib.sha256(DATASET.read_bytes()).hexdigest()
    assert {key for key, row in cases.items() if row["outcome"] == "success"} == {
        "core-001",
        "core-003",
        "core-013",
        "core-015",
    }
    assert {key for key, row in cases.items() if row["outcome"] == "failure"} == {
        "core-002",
        "core-004",
        "core-005",
        "core-006",
        "core-016",
        "core-018",
        "core-020",
        "core-021",
    }
    assert len([row for row in cases.values() if row["outcome"] == "unsupported"]) == 9
    assert [attempt["run_status"] for attempt in cases["core-013"]["attempts"]] == [
        "setup_error",
        "setup_error",
        "returned",
    ]
    for row in cases.values():
        assert row["attempts"]
        assert row["selected_attempt"] == {
            key: row["attempts"][-1][key] for key in ("file", "line")
        }
        if row["outcome"] == "unsupported":
            assert row["tool_calls"] == []
            assert row["provider_http_statuses"] == []
            assert row["mcp_server_dispatches"] == 0


def test_mcp_failure_is_not_scored_from_terminate_declaration():
    cases = {row["sample_id"]: row for row in build_review()["samples"]}
    failure = cases["core-016"]
    assert failure["outcome"] == "failure"
    assert failure["mcp_server_dispatches"] == 1
    assert [call["tool"] for call in failure["tool_calls"]] == [
        "mcp_fixture_fail_read",
        "terminate",
    ]
    assert failure["provider_http_statuses"] == [200, 200]
    for sample_id in ("core-013", "core-015"):
        case = cases[sample_id]
        assert case["mcp_server_dispatches"] == 1
        assert case["provider_reported_usage"]["total_tokens"] > 0
