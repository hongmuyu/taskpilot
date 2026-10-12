"""T070 measured cost and latency accounting over frozen evidence."""

import json

from eval import t069_score_accuracy_success as t069
from eval.t070_cost_latency import (
    OUTPUT,
    aggregate_measurements,
    measure_provider_requests,
    retry_costs,
    score_all,
)


def test_missing_provider_usage_is_not_zero_or_estimated():
    requests = [
        {"usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}},
        {"usage": None},
    ]
    usage = measure_provider_requests(requests)
    assert usage["input"] == {"value": None, "known_sum": 10, "unknown_attempts": 1}
    assert usage["output"] == {"value": None, "known_sum": 4, "unknown_attempts": 1}
    assert usage["total"] == {"value": None, "known_sum": 14, "unknown_attempts": 1}
    assert measure_provider_requests([])["total"]["value"] == 0


def test_partial_provider_usage_field_stays_unavailable():
    usage = measure_provider_requests(
        [
            {
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": None,
                    "total_tokens": None,
                }
            }
        ]
    )
    assert usage["input"]["value"] == 7
    assert usage["output"] == {"value": None, "known_sum": 0, "unknown_attempts": 1}
    assert usage["total"]["value"] is None


def test_aggregate_retains_missing_denominator_and_does_not_impute():
    result = aggregate_measurements([100.0, None, 200.0])
    assert result == {
        "eligible": 3,
        "available": 2,
        "missing": 1,
        "known_sum": 300.0,
        "total": None,
        "mean": 150.0,
        "median": 150.0,
    }


def test_retry_counts_only_additional_attempts_and_keeps_overlap_separate():
    trace = [
        {
            "event": "tool_execution",
            "status": "failure",
            "attempt": 1,
            "duration_ms": 10,
        },
        {"event": "retry_wait", "status": "success", "duration_ms": 100},
        {
            "event": "tool_execution",
            "status": "success",
            "attempt": 2,
            "duration_ms": 25,
        },
        {
            "event": "llm_provider_attempt",
            "status": "failure",
            "llm_attempt": 1,
            "duration_ms": 30,
            "usage_source": "unknown",
        },
        {
            "event": "llm_provider_attempt",
            "status": "success",
            "llm_attempt": 2,
            "duration_ms": 40,
            "usage_source": "provider",
            "input_tokens": 8,
            "output_tokens": 2,
            "total_tokens": 10,
        },
    ]
    result = retry_costs(trace)
    assert result["tool_extra_attempts"] == 1
    assert result["llm_extra_attempts"] == 1
    assert result["wait_ms"] == 100
    assert result["extra_attempt_duration_ms"] == 65
    assert result["extra_provider_tokens"]["total"]["value"] == 10


def test_frozen_groups_pair_by_id_and_preserve_mode_and_outcome():
    result = score_all()
    groups = result["groups"]
    assert len(groups["original_openmanus"]["samples"]) == 21
    assert groups["original_openmanus"]["coverage"]["supported_attempted"] == 12
    assert groups["original_openmanus"]["coverage"]["unsupported"] == 9
    for name in ("taskpilot_full", "taskpilot_k3"):
        group = groups[name]
        assert group["coverage"]["natural_model"] == 12
        assert group["coverage"]["controlled_zero_llm"] == 9
        assert group["coverage"]["supported_attempted"] == 21
        assert all(
            row["provider_tokens"]["total"]["value"] == 0
            for row in group["samples"]
            if row["mode"] == "controlled_zero_llm"
        )
    pair = result["taskpilot_paired"]
    assert pair["fixed_contract_verified"] is True
    assert len(pair["samples"]) == 21
    assert pair["all_21"]["eligible"] == 21
    assert pair["natural_12"]["eligible"] == 12
    assert pair["controlled_9"]["eligible"] == 9
    assert {row["sample_id"] for row in pair["samples"]} == {
        row["sample_id"] for row in groups["taskpilot_full"]["samples"]
    }
    assert groups["taskpilot_full"]["samples"][10]["business_outcome"] == "not_verified"


def test_trace_failures_embedding_phases_and_prior_setup_failures_are_disclosed():
    groups = score_all()["groups"]
    original = groups["original_openmanus"]
    assert len(original["provenance"]["selected_mcp_retest_sha256"]) == 64
    assert original["prior_setup_failures"]["attempts"] == 6
    assert len(original["samples"][12]["prior_setup_failures"]) == 2
    assert original["samples"][12]["source_file"] == (
        "t066_original_openmanus_mcp_retest_2.jsonl"
    )
    full = groups["taskpilot_full"]
    k3 = groups["taskpilot_k3"]
    assert full["tool_attempt_status_counts"]["failure"] > 0
    assert k3["tool_attempt_status_counts"]["unknown"] > 0
    assert k3["all_supported"]["embedding_calls"]["total"] == 52
    assert k3["all_supported"]["embedding_index_calls"]["total"] == 4
    assert k3["all_supported"]["embedding_query_calls"]["total"] == 48
    case = k3["samples"][0]
    assert (
        case["embedding"]["index_duration_ms"] + case["embedding"]["query_duration_ms"]
        == case["embedding"]["duration_ms"]
    )
    assert full["samples"][18]["retry"]["tool_extra_attempts"] == 1
    assert k3["samples"][18]["retry"]["tool_extra_attempts"] == 1
    assert (
        full["samples"][0]["latency_ms"]["end_to_end"]
        > full["samples"][0]["agent_trace_ms"]
    )


def test_committed_result_is_exactly_recomputed():
    assert json.loads(OUTPUT.read_text()) == score_all()


def test_raw_provider_and_failure_attempt_spot_checks():
    result = score_all()["groups"]
    original_raw = t069.load_jsonl(t069.RESULTS / "t066_original_openmanus_v1.jsonl")[0]
    original_case = result["original_openmanus"]["samples"][0]
    assert original_case["provider_tokens"]["total"]["value"] == sum(
        request["usage"]["total_tokens"]
        for request in original_raw["provider_requests"]
    )
    assert (
        original_case["latency_ms"]["end_to_end"] == original_raw["run"]["elapsed_ms"]
    )
    for name, group in (
        ("t067_full_tools_v1.jsonl", "taskpilot_full"),
        ("t068_k3_v1.jsonl", "taskpilot_k3"),
    ):
        raw = next(
            row
            for row in t069.load_jsonl(t069.RESULTS / name)[1:]
            if row["sample_id"] == "core-019"
        )
        measured = result[group]["samples"][18]
        assert [event["http_status"] for event in raw["github_http"]] == [503, 200]
        assert measured["provider_attempts"] == 0
        assert measured["retry"]["tool_extra_attempts"] == 1
        assert measured["latency_ms"]["end_to_end"] == raw["elapsed_ms"]
        attempts = [
            event
            for event in raw["trace"]
            if event["event"] == "tool_execution" and event["status"] != "started"
        ]
        assert len({event["tool_call_id"] for event in attempts}) == 1
        assert {event["attempt"] for event in attempts} == {1, 2}


def test_paired_totals_equal_same_id_group_difference():
    result = score_all()
    full = result["groups"]["taskpilot_full"]["all_supported"]
    k3 = result["groups"]["taskpilot_k3"]["all_supported"]
    paired = result["taskpilot_paired"]["all_21"]["k3_minus_full"]
    for field in ("input", "output", "total"):
        assert paired[f"{field}_tokens"]["total"] == (
            k3["tokens"][field]["total"] - full["tokens"][field]["total"]
        )
    assert (
        abs(
            paired["end_to_end_ms"]["total"]
            - (
                k3["latency_ms"]["end_to_end"]["total"]
                - full["latency_ms"]["end_to_end"]["total"]
            )
        )
        < 1e-7
    )
