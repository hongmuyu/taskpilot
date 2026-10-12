"""T071 scoring against frozen slot labels and recorded calls."""

import json

from eval.t071_slot_metrics import OUTPUT, pair_counts, score_all


def test_field_pairs_count_wrong_values_as_fp_and_fn_without_bool_coercion():
    gold = {"issue_number": 42, "path": "README.md"}
    actual = [
        {"field": "issue_number", "value": True},
        {"field": "path", "value": "readme.md"},
    ]
    assert pair_counts(gold, actual) == {"tp": 0, "fp": 2, "fn": 2}


def test_query_normalization_accepts_gold_terms_and_deduplicates_parallel_calls():
    gold = {"query": "ForwardRef is_class"}
    actual = [
        {"field": "query", "value": "ForwardRef is_class on Python 3.10"},
        {"field": "query", "value": "is_class=True ForwardRef"},
    ]
    assert pair_counts(gold, actual) == {"tp": 1, "fp": 0, "fn": 0}


def test_wrong_then_corrected_value_keeps_wrong_pair_visible():
    assert pair_counts(
        {"path": "README.md"},
        [
            {"field": "path", "value": "wrong.md"},
            {"field": "path", "value": "README.md"},
        ],
    ) == {"tp": 1, "fp": 1, "fn": 0}


def test_frozen_results_keep_controlled_calls_out_of_model_extraction():
    result = score_all()
    original = result["groups"]["original_openmanus"]
    assert original["coverage"]["unsupported"] == 9
    assert original["coverage"]["unstructured_git_calls"] == 9
    assert original["metrics"]["natural_resolved"]["gold_fields"] == 3
    assert original["metrics"]["controlled_resolved"]["precision"] is None
    for group in ("taskpilot_full", "taskpilot_k3"):
        item = result["groups"][group]
        assert item["coverage"]["natural_model"] == 12
        assert item["coverage"]["controlled_zero_llm"] == 9
        assert item["missing_detection"]["expected_fields"] == 3
        assert item["clarification"]["scripted_reply_cases"] == 3
        assert item["clarification"]["valid_after_reply"] == 3
        assert item["parameter_error_recognition"]["expected_cases"] == 2
        for sid in ("core-007", "core-008", "core-014"):
            row = next(row for row in item["samples"] if row["sample_id"] == sid)
            assert row["mode"] == "controlled_zero_llm"
            assert row["missing_detection"]["zero_dispatch_before_reply"] is True
            assert row["clarification"]["original_call_id_preserved"] is True
        for sid, expected in (
            ("core-009", "invalid_json"),
            ("core-010", "invalid_type"),
        ):
            row = next(row for row in item["samples"] if row["sample_id"] == sid)
            assert row["parameter_error_recognition"]["expected"] == expected
            assert row["parameter_error_recognition"]["zero_dispatch"] is True


def test_context_and_scripted_reply_do_not_enter_model_only_denominator():
    result = score_all()
    for name in ("taskpilot_full", "taskpilot_k3"):
        group = result["groups"][name]
        assert group["metrics"]["natural_resolved"]["gold_fields"] == 45
        assert group["metrics"]["natural_model_explicit"]["gold_fields"] == 17
        reply = next(row for row in group["samples"] if row["sample_id"] == "core-007")
        emitted = reply["steps"][1]["emitted_slots"]
        assert {item["origin"] for item in emitted} == {
            "confirmed_context",
            "scripted_user_reply",
        }
        assert reply["steps"][1]["model_explicit_counts"] is None


def test_each_scored_field_keeps_gold_actual_source_and_call_reference():
    result = score_all()
    for group in result["groups"].values():
        assert len(group["samples"]) == 21
        for row in group["samples"]:
            assert row["sample_id"].startswith("core-")
            for step in row["steps"]:
                assert "gold_slots" in step
                assert "observed_slots" in step
                assert "emitted_slots" in step
                assert "counts" in step
                assert "evidence" in step
                for item in step["emitted_slots"]:
                    assert item["origin"]
                    assert item["evidence"]


def test_invalid_parameter_remains_visible_without_entering_value_pair_score():
    group = score_all()["groups"]["taskpilot_full"]
    row = next(row for row in group["samples"] if row["sample_id"] == "core-010")
    step = row["steps"][0]
    assert step["observed_slots"][0]["value"] is True
    assert step["observed_slots"][0]["origin"] == "controlled_injection"
    assert step["counts"] is None
    assert row["parameter_error_recognition"]["recognized"] is True


def test_committed_score_is_exactly_recomputed():
    assert json.loads(OUTPUT.read_text()) == score_all()
