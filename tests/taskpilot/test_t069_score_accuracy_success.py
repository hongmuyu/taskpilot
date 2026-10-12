"""T069 deterministic scoring and frozen-evidence regression."""

import json

from eval.t069_score_accuracy_success import RESULTS, score_all, score_selected_tools


def test_parallel_multilabel_wrong_extra_and_missing_choices():
    acceptable = ["github_issue_search", "github_code_search"]
    assert score_selected_tools(["github_issue_search"], acceptable)
    assert score_selected_tools(
        ["github_issue_search", "github_code_search"], acceptable
    )
    assert score_selected_tools(
        ["github_code_search", "github_code_search"], acceptable
    )
    assert not score_selected_tools(
        ["github_issue_search", "github_read_file"], acceptable
    )
    assert not score_selected_tools(["terminate"], acceptable)
    assert not score_selected_tools([], acceptable)


def test_frozen_three_group_denominators_and_independent_coverage():
    scored = score_all()["groups"]
    expected = {
        "original_openmanus": ((2, 15), (2, 10), (4, 12), 9),
        "taskpilot_full": ((6, 16), (5, 11), (18, 21), 0),
        "taskpilot_k3": ((7, 16), (7, 11), (18, 21), 0),
    }
    for group, (steps, tasks, success, unsupported) in expected.items():
        got = scored[group]
        assert len(got["samples"]) == got["sample_count"] == 21
        assert (
            got["tool_selection_steps"]["correct"],
            got["tool_selection_steps"]["eligible"],
        ) == steps
        assert (
            got["tool_selection_tasks_all_steps_correct"]["correct"],
            got["tool_selection_tasks_all_steps_correct"]["eligible"],
        ) == tasks
        assert (
            got["task_success_supported"]["success"],
            got["task_success_supported"]["eligible"],
        ) == success
        assert got["unsupported"] == unsupported
    for group in ("taskpilot_full", "taskpilot_k3"):
        assert scored[group]["natural_model_tasks"] == 12
        assert scored[group]["controlled_zero_llm_tasks"] == 9
        assert all(
            not sample["selection_steps"]
            for sample in scored[group]["samples"]
            if sample["mode"] == "controlled_zero_llm"
        )


def test_selected_original_mcp_retests_and_review_conflict_are_explicit():
    groups = score_all()["groups"]
    original = {s["sample_id"]: s for s in groups["original_openmanus"]["samples"]}
    for sid in ("core-013", "core-015", "core-016"):
        assert (
            original[sid]["selected_run_file"]
            == "t066_original_openmanus_mcp_retest_2.jsonl"
        )
    assert original["core-016"]["business_outcome"] == "failure"
    full = {s["sample_id"]: s for s in groups["taskpilot_full"]["samples"]}
    assert full["core-011"]["source_review_outcome"] == "success"
    assert full["core-011"]["business_outcome"] == "not_verified"
    assert "routing-score" in full["core-011"]["adjudication_conflict"]
    for group in ("taskpilot_full", "taskpilot_k3"):
        cases = {s["sample_id"]: s for s in groups[group]["samples"]}
        assert cases["core-005"]["business_outcome"] == "not_verified"
        assert cases["core-006"]["business_outcome"] == "not_verified"
        assert cases["core-011"]["business_outcome"] != "failure"
    k3 = {s["sample_id"]: s for s in groups["taskpilot_k3"]["samples"]}
    assert k3["core-020"]["business_outcome"] == "not_verified"


def test_actual_parallel_choices_and_no_match_do_not_inflate_accuracy():
    groups = score_all()["groups"]
    full = {s["sample_id"]: s for s in groups["taskpilot_full"]["samples"]}
    k3 = {s["sample_id"]: s for s in groups["taskpilot_k3"]["samples"]}
    assert full["core-001"]["selection_steps"][0]["selected_tools"] == [
        "github_repository_info",
        "github_read_file",
    ]
    assert not full["core-001"]["selection_steps"][0]["correct"]
    assert k3["core-011"]["selection_steps"][0]["correct"]
    assert k3["core-011"]["selection_steps"][0]["selected_tools"] == [
        "github_code_search",
        "github_code_search",
    ]
    assert full["core-012"]["selection_steps"] == []
    assert k3["core-012"]["selection_steps"] == []
    assert full["core-005"]["unlabelled_followup_turns"]
    assert all(
        "selected_tools" in turn
        for turn in full["core-005"]["unlabelled_followup_turns"]
    )


def test_committed_report_is_exact_recalculation():
    committed = json.loads((RESULTS / "t069_accuracy_success_v1.json").read_text())
    assert committed == score_all()
