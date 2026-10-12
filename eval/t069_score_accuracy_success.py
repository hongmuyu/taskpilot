"""Recompute T069 metrics from the frozen holdout and T066–T068 evidence.

Business evidence is adjudicated in the committed review files. This script
checks their provenance and records the one explicit frozen-criterion conflict;
it never treats an Agent finish declaration as evidence of business success.
"""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "eval/datasets/core_benchmark_v1_holdout.json"
SCORING = ROOT / "eval/datasets/core_benchmark_v1_scoring.json"
RESULTS = ROOT / "eval/results"
SOURCES = {
    "original_openmanus": (
        "t066_original_openmanus_v1.jsonl",
        "t066_original_openmanus_review.json",
    ),
    "taskpilot_full": ("t067_full_tools_v1.jsonl", "t067_full_tools_review.json"),
    "taskpilot_k3": ("t068_k3_v1.jsonl", "t068_k3_review.json"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def score_selected_tools(selected: list[str], acceptable: list[str]) -> bool:
    """One gold step scores once; every parallel choice must be acceptable."""
    return bool(selected) and all(name in acceptable for name in selected)


def selected_assistant_turns(messages: list[dict]) -> list[list[dict]]:
    return [
        message.get("tool_calls", [])
        for message in messages
        if message["role"] == "assistant"
    ]


def _index_unique(rows: list[dict], ids: set[str], label: str) -> dict[str, dict]:
    indexed = {row["sample_id"]: row for row in rows}
    if len(indexed) != len(rows) or set(indexed) != ids:
        raise ValueError(f"{label}: duplicate or missing sample ID")
    return indexed


def _load_group(
    group: str, sample_ids: set[str], holdout: dict
) -> tuple[dict, dict, dict]:
    raw_name, review_name = SOURCES[group]
    raw_path, review_path = RESULTS / raw_name, RESULTS / review_name
    rows = load_jsonl(raw_path)
    manifest = rows[0] if group != "original_openmanus" else None
    if manifest is not None:
        if manifest["record_type"] != "manifest":
            raise ValueError(f"{group}: missing manifest")
        rows = rows[1:]
    raw = _index_unique(rows, sample_ids, raw_name)
    review_doc = json.loads(review_path.read_text())
    review = _index_unique(review_doc["samples"], sample_ids, review_name)
    expected_hash = sha256(DATASET)
    hash_bearers = rows + [review_doc] if manifest is None else [manifest, review_doc]
    for item in hash_bearers:
        if item["dataset_version"] != holdout["version"]:
            raise ValueError(f"{group}: dataset version mismatch")
        if item["dataset_sha256"] != expected_hash:
            raise ValueError(f"{group}: dataset hash mismatch")
        if item["scoring_sha256"] != holdout["scoring_sha256"]:
            raise ValueError(f"{group}: scoring hash mismatch")
    return (
        raw,
        review,
        {
            "raw_file": raw_name,
            "raw_sha256": sha256(raw_path),
            "review_file": review_name,
            "review_sha256": sha256(review_path),
        },
    )


def _selected_run(group: str, raw: dict, review: dict) -> tuple[dict, str]:
    if group != "original_openmanus":
        return raw, SOURCES[group][0]
    pointer = review["selected_attempt"]
    filename = pointer["file"]
    if filename == SOURCES[group][0]:
        if pointer["line"] != int(raw["sample_id"].split("-")[1]):
            raise ValueError("Original selected attempt line mismatch")
        return raw, filename
    if filename != "t066_original_openmanus_mcp_retest_2.jsonl":
        raise ValueError("Unknown original selected attempt")
    rows = load_jsonl(RESULTS / filename)
    selected = rows[pointer["line"] - 1]
    if (
        selected["sample_id"] != raw["sample_id"]
        or selected["run"]["run_status"] != "returned"
    ):
        raise ValueError("Original MCP retest pointer mismatch")
    if any(
        selected[field] != raw[field]
        for field in (
            "dataset_version",
            "dataset_sha256",
            "scoring_sha256",
            "upstream_commit",
        )
    ):
        raise ValueError("Original MCP retest provenance mismatch")
    return selected, filename


def _business_outcome(
    group: str, sample: dict, raw: dict, review: dict
) -> tuple[str, str | None]:
    outcome = review["outcome"]
    if outcome not in {"success", "failure", "not_verified", "unsupported"}:
        raise ValueError(f"Unexpected outcome: {outcome}")
    if raw["support"] == "unsupported":
        if outcome != "unsupported":
            raise ValueError("Unsupported raw run and review disagree")
        return outcome, None
    if outcome == "unsupported":
        raise ValueError("Supported raw run marked unsupported")
    if not review.get("reason", review.get("review_reason")):
        raise ValueError("Business adjudication lacks reason")
    if group != "original_openmanus":
        if raw["trace_validation"] != "valid" or raw["cleanup_status"] != "complete":
            raise ValueError("TaskPilot trace or cleanup invalid")
    if group == "taskpilot_full" and sample["id"] == "core-011":
        # Frozen business_evidence requires a routing candidate and score.
        # Full-tools intentionally has no routing event; T067's prior review
        # called this success. Preserve that review and expose the conflict.
        if outcome != "success" or raw["routing_events"]:
            raise ValueError("core-011 Full-tools evidence conflict changed")
        criteria = [e["criterion"] for e in sample["business_evidence"]]
        if not any("candidate" in text and "score" in text for text in criteria):
            raise ValueError("core-011 frozen routing criterion changed")
        return (
            "not_verified",
            "T067 review says success; frozen routing-score evidence is absent in true full-tools",
        )
    return outcome, None


def score_group(group: str, samples: list[dict], holdout: dict) -> dict:
    sample_ids = {sample["id"] for sample in samples}
    raw_rows, reviews, provenance = _load_group(group, sample_ids, holdout)
    details = []
    for sample in samples:
        sid = sample["id"]
        raw, review = raw_rows[sid], reviews[sid]
        selected_run, selected_file = _selected_run(group, raw, review)
        if selected_run["support"] != raw["support"]:
            raise ValueError("Selected attempt support differs")
        outcome, conflict = _business_outcome(group, sample, raw, review)
        if raw["support"] == "supported":
            messages = (
                selected_run["run"]["messages"]
                if group == "original_openmanus"
                else raw["messages"]
            )
            turns = selected_assistant_turns(messages)
            if group != "original_openmanus":
                count = raw["actual_llm_tool_calls"]
                if len(raw["schema_calls"]) != count or (count and len(turns) != count):
                    raise ValueError(f"{group}/{sid}: model turn count mismatch")
                natural = raw["setup_mode"] == "natural_task"
                if natural != (count > 0):
                    raise ValueError(
                        f"{group}/{sid}: setup mode and LLM calls disagree"
                    )
                if not natural and any(
                    step["selection_eligible"] for step in sample["steps"]
                ):
                    raise ValueError(
                        f"{group}/{sid}: controlled sample has autonomous gold step"
                    )
            mode = (
                "controlled_zero_llm"
                if group != "original_openmanus" and raw["actual_llm_tool_calls"] == 0
                else "natural_model"
            )
        else:
            turns, mode = [], "unsupported"
        step_scores = []
        followup_turns = []
        if mode == "natural_model":
            for step in sample["steps"]:
                if not step["selection_eligible"]:
                    continue
                index = step["index"]
                if group != "original_openmanus" and index <= len(raw["schema_calls"]):
                    if raw["schema_calls"][index - 1]["step"] != index:
                        raise ValueError(f"{group}/{sid}: schema step mismatch")
                calls = turns[index - 1] if index <= len(turns) else []
                names = [call["name"] for call in calls]
                correct = score_selected_tools(names, step["acceptable_tools"])
                step_scores.append(
                    {
                        "gold_step": index,
                        "acceptable_tools": step["acceptable_tools"],
                        "selected_tools": names,
                        "tool_call_ids": [call["id"] for call in calls],
                        "correct": correct,
                        "evidence": f"{selected_file}:{sid}:assistant_turn_{index}",
                    }
                )
            last_gold_index = max(
                (step["index"] for step in sample["steps"]), default=0
            )
            followup_turns = [
                {
                    "assistant_turn": index,
                    "selected_tools": [call["name"] for call in calls],
                    "tool_call_ids": [call["id"] for call in calls],
                }
                for index, calls in enumerate(turns, 1)
                if index > last_gold_index and calls
            ]
        details.append(
            {
                "sample_id": sid,
                "category": sample["category"],
                "support": raw["support"],
                "mode": mode,
                "source_review_outcome": review["outcome"],
                "business_outcome": outcome,
                "business_evidence_requirements": [
                    e["criterion"] for e in sample["business_evidence"]
                ],
                "business_review_reason": review.get(
                    "reason", review.get("review_reason")
                ),
                "adjudication_conflict": conflict,
                "selected_run_file": selected_file,
                "selection_steps": step_scores,
                "selection_task_correct": all(item["correct"] for item in step_scores)
                if step_scores
                else None,
                "unlabelled_followup_turns": followup_turns,
            }
        )
    supported = [x for x in details if x["support"] == "supported"]
    selection_tasks = [x for x in supported if x["selection_steps"]]
    selection_steps = [step for x in selection_tasks for step in x["selection_steps"]]
    counts = Counter(x["business_outcome"] for x in details)
    return {
        "provenance": provenance,
        "sample_count": len(details),
        "supported_attempted": len(supported),
        "unsupported": counts["unsupported"],
        "natural_model_tasks": sum(x["mode"] == "natural_model" for x in details),
        "controlled_zero_llm_tasks": sum(
            x["mode"] == "controlled_zero_llm" for x in details
        ),
        "tool_selection_steps": {
            "correct": sum(x["correct"] for x in selection_steps),
            "eligible": len(selection_steps),
        },
        "tool_selection_tasks_all_steps_correct": {
            "correct": sum(x["selection_task_correct"] for x in selection_tasks),
            "eligible": len(selection_tasks),
        },
        "task_success_supported": {
            "success": counts["success"],
            "eligible": len(supported),
        },
        "task_success_all_21_coverage": {
            "success": counts["success"],
            "eligible": len(details),
        },
        "business_outcomes": dict(sorted(counts.items())),
        "samples": details,
    }


def score_all() -> dict:
    holdout = json.loads(DATASET.read_text())
    if holdout["version"] != "1.0.0" or len(holdout["samples"]) != 21:
        raise ValueError("Frozen holdout version or size changed")
    if holdout["scoring_sha256"] != sha256(SCORING):
        raise ValueError("Frozen scoring hash changed")
    if len({x["id"] for x in holdout["samples"]}) != 21:
        raise ValueError("Duplicate sample ID")
    return {
        "metric_version": "t069-v1",
        "dataset_version": holdout["version"],
        "dataset_sha256": sha256(DATASET),
        "scoring_sha256": sha256(SCORING),
        "selection_policy": "Gold step index maps to same numbered actual assistant turn; all parallel selected tools must be acceptable; no selection is wrong. Controlled zero-LLM cases are excluded.",
        "success_policy": "All frozen business evidence required; reviewed outcomes checked against raw provenance; finish declaration alone is insufficient. Unsupported excluded only from supported denominator.",
        "groups": {
            name: score_group(name, holdout["samples"], holdout) for name in SOURCES
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=RESULTS / "t069_accuracy_success_v1.json"
    )
    args = parser.parse_args()
    result = score_all()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    for name, group in result["groups"].items():
        print(
            name,
            group["tool_selection_steps"],
            group["task_success_supported"],
            group["business_outcomes"],
        )


if __name__ == "__main__":
    main()
