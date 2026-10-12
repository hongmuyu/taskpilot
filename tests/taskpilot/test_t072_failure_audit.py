"""T072 audit must remain tied to the frozen metric and run evidence."""

import json

from eval import t072_failure_audit as audit


def test_audit_covers_every_non_success_and_unsupported_run():
    result = audit.build_audit()
    assert len(result["cases"]) == 14
    assert len(result["unsupported"]) == 9
    assert len(result["expected_fault_probes"]) == 8
    assert {case["outcome"] for case in result["cases"]} == {"failure", "not_verified"}
    assert all(case["evidence"]["benchmark_run_id"] for case in result["cases"])
    assert all(case["evidence"]["source"] for case in result["cases"])
    assert all(case["categories"] for case in result["cases"])


def test_full_baseline_has_no_fabricated_routing_event_or_upstream_trace():
    result = audit.build_audit()
    core011 = next(
        case
        for case in result["cases"]
        if case["group"] == "taskpilot_full" and case["sample_id"] == "core-011"
    )
    assert core011["outcome"] == "not_verified"
    assert core011["evidence"]["routing_event_count"] == 0
    assert core011["evidence"]["runtime_run_id"]
    upstream = [
        case for case in result["cases"] if case["group"] == "original_openmanus"
    ]
    assert all(case["evidence"]["runtime_run_id"] is None for case in upstream)
    assert all(
        "NOT AVAILABLE" in case["evidence"]["runtime_trace"] for case in upstream
    )


def test_fault_probes_keep_attempts_and_committed_audit_matches():
    result = audit.build_audit()
    for group in ("taskpilot_full", "taskpilot_k3"):
        probes = {
            x["sample_id"]: x
            for x in result["expected_fault_probes"]
            if x["group"] == group
        }
        assert set(probes) == set(audit.PROBES)
        assert probes["core-016"]["tool_attempt_status_counts"] == {"failure": 1}
        assert probes["core-017"]["tool_attempt_status_counts"] == {"unknown": 1}
        assert probes["core-018"]["evidence"]["github_http_statuses"] == [404]
        assert probes["core-019"]["evidence"]["github_http_statuses"] == [503, 200]
        assert probes["core-019"]["tool_attempt_status_counts"] == {
            "failure": 1,
            "success": 1,
        }
    assert json.loads(audit.OUTPUT.read_text()) == result


def test_separate_real_replay_keeps_model_and_fault_evidence():
    path = audit.OUTPUT.with_name("t072_replay_k3_v1.jsonl")
    manifest, *rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert manifest["selected_sample_count"] == 6
    assert manifest["dataset_sha256"] == audit.build_audit()["dataset_sha256"]
    assert manifest["fixed_execution"]["full_tool_filter"] == "routing_top_k=3"
    assert all(
        x["trace_validation"] == "valid" and x["cleanup_status"] == "complete"
        for x in rows
    )
    by_id = {x["sample_id"]: x for x in rows}
    assert set(by_id) == {
        "core-003",
        "core-013",
        "core-016",
        "core-017",
        "core-018",
        "core-019",
    }
    for sid in ("core-003", "core-013"):
        assert by_id[sid]["actual_llm_tool_calls"] > 0
        assert by_id[sid]["routing_events"]
    assert [x["http_status"] for x in by_id["core-003"]["github_http"]] == [200, 200]
    assert [x["tool"] for x in by_id["core-013"]["mcp_audit"]] == ["echo_read"]
    expected = {"core-016": "failure", "core-017": "unknown", "core-018": "failure"}
    for sid, status in expected.items():
        row = by_id[sid]
        assert row["actual_llm_tool_calls"] == 0
        terminal = [
            e
            for e in row["trace"]
            if e["event"] == "tool_execution" and e["status"] != "started"
        ]
        assert len(terminal) == 1 and terminal[0]["status"] == status
    assert [x["http_status"] for x in by_id["core-018"]["github_http"]] == [404]
    retry = [
        e
        for e in by_id["core-019"]["trace"]
        if e["event"] == "tool_execution" and e["status"] != "started"
    ]
    assert [(e["attempt"], e["status"]) for e in retry] == [
        (1, "failure"),
        (2, "success"),
    ]
    assert retry[0]["tool_call_id"] == retry[1]["tool_call_id"]
    assert [x["http_status"] for x in by_id["core-019"]["github_http"]] == [503, 200]


def test_clean_checkout_container_replay_is_separate_and_complete():
    path = audit.OUTPUT.with_name("t072_replay_clean_full_v1.jsonl")
    manifest, *rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert manifest["selected_sample_count"] == 4
    assert manifest["dataset_sha256"] == audit.build_audit()["dataset_sha256"]
    assert (
        manifest["fixed_execution"]["full_tool_filter"]
        == "routing_top_k=None; routing_retriever=None"
    )
    by_id = {x["sample_id"]: x for x in rows}
    assert set(by_id) == {"core-003", "core-013", "core-018", "core-019"}
    assert all(
        row["trace_validation"] == "valid" and row["cleanup_status"] == "complete"
        for row in rows
    )
    assert by_id["core-003"]["actual_llm_tool_calls"] > 0
    assert by_id["core-013"]["actual_llm_tool_calls"] > 0
    assert all(not row["routing_events"] for row in rows)
    assert [e["http_status"] for e in by_id["core-003"]["github_http"]] == [200, 200]
    assert [e["tool"] for e in by_id["core-013"]["mcp_audit"]] == ["echo_read"]
    assert [e["http_status"] for e in by_id["core-018"]["github_http"]] == [404]
    retry = [
        e
        for e in by_id["core-019"]["trace"]
        if e["event"] == "tool_execution" and e["status"] != "started"
    ]
    assert [(e["attempt"], e["status"]) for e in retry] == [
        (1, "failure"),
        (2, "success"),
    ]
    assert retry[0]["tool_call_id"] == retry[1]["tool_call_id"]
    assert [e["http_status"] for e in by_id["core-019"]["github_http"]] == [503, 200]
