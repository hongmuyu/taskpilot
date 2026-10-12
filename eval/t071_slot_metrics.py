"""Score frozen critical slots from T066–T068 calls without running a model.

Only typed tool arguments are comparable. Original OpenManus GitHub work used
arbitrary Python code; its nine such runs remain visible but have no typed slot
denominator. Context and scripted replies are recorded as separate channels.
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from eval import t069_score_accuracy_success as t069


OUTPUT = t069.RESULTS / "t071_slot_metrics_v1.json"
SLOT_FIELDS = {
    "repository",
    "ref",
    "issue_number",
    "path",
    "query",
    "text",
    "suffix",
    "wait_ms",
}
GITHUB_PREFIX = "github_"


def _same(field: str, actual: object, gold: object) -> bool:
    if type(actual) is not type(gold):
        return False
    if field == "repository":
        return isinstance(actual, str) and actual.casefold() == gold.casefold()
    if field == "query":
        if not isinstance(actual, str):
            return False
        terms = re.findall(r"[\w]+", gold.casefold())
        found = set(re.findall(r"[\w]+", actual.casefold()))
        return bool(terms) and all(term in found for term in terms)
    return actual == gold


def pair_counts(gold: dict, emitted: list[dict]) -> dict[str, int]:
    """One gold field can match once; distinct wrong values remain false positives."""
    unique = []
    for entry in emitted:
        if not any(
            entry["field"] == other["field"]
            and type(entry["value"]) is type(other["value"])
            and entry["value"] == other["value"]
            for other in unique
        ):
            unique.append(entry)
    matched = set()
    fp = 0
    for entry in unique:
        field = entry["field"]
        if field in gold and _same(field, entry["value"], gold[field]):
            matched.add(field)
        else:
            fp += 1
    return {"tp": len(matched), "fp": fp, "fn": len(gold) - len(matched)}


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _metrics(counts: dict[str, int]) -> dict:
    tp, fp, fn = (counts[key] for key in ("tp", "fp", "fn"))
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else (0.0 if precision is not None and recall is not None else None)
    )
    return {
        **counts,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "gold_fields": tp + fn,
        "emitted_pairs": tp + fp,
    }


def _sum_counts(items: list[dict]) -> dict:
    return _metrics(
        {key: sum(item[key] for item in items) for key in ("tp", "fp", "fn")}
    )


def _assistant_turns(messages: list[dict]) -> list[list[dict]]:
    return [m.get("tool_calls", []) for m in messages if m["role"] == "assistant"]


def _field_entries(call: dict, evidence: str, origin: str) -> list[dict]:
    try:
        args = json.loads(call["arguments"])
    except (ValueError, TypeError):
        return []
    if not isinstance(args, dict):
        return []
    if "owner" in args and "repo" in args and "repository" not in args:
        args["repository"] = f"{args['owner']}/{args['repo']}"
    return [
        {
            "field": key,
            "value": value,
            "origin": origin,
            "tool": call["name"],
            "tool_call_id": call["id"],
            "evidence": evidence,
        }
        for key, value in args.items()
        if key in SLOT_FIELDS
    ]


def _dedupe(entries: list[dict]) -> list[dict]:
    result = []
    for entry in entries:
        if not any(
            entry["field"] == prior["field"]
            and type(entry["value"]) is type(prior["value"])
            and entry["value"] == prior["value"]
            and entry["origin"] == prior["origin"]
            for prior in result
        ):
            result.append(entry)
    return result


def _context_entries(
    raw: dict, calls: list[dict], explicit: list[dict], source: str
) -> list[dict]:
    context = raw["context"]
    if context["provenance"] != "confirmed_user" or not any(
        call["name"].startswith(GITHUB_PREFIX) for call in calls
    ):
        return []
    explicit_fields = {entry["field"] for entry in explicit}
    return [
        {
            "field": field,
            "value": context[field],
            "origin": "confirmed_context",
            "tool": None,
            "tool_call_id": calls[0]["id"],
            "evidence": f"{source}:{raw['sample_id']}:context",
        }
        for field in ("repository", "ref")
        if context[field] is not None and field not in explicit_fields
    ]


def _reply_entries(
    sample: dict, raw: dict, calls: list[dict], source: str
) -> list[dict]:
    run = raw["run"]
    if run.get("reply_source") != "frozen_holdout_controlled_reply":
        return []
    reply = sample["setup"].get("clarification_reply", {})
    origins = run.get("parameter_sources", {})
    if any(origins.get(field) != "user_clarification" for field in reply):
        raise ValueError(f"{sample['id']}: scripted reply provenance mismatch")
    return [
        {
            "field": field,
            "value": value,
            "origin": "scripted_user_reply",
            "tool": calls[0]["name"],
            "tool_call_id": calls[0]["id"],
            "evidence": f"{source}:{sample['id']}:run.parameter_sources",
        }
        for field, value in reply.items()
        if field in SLOT_FIELDS
    ]


def _gold_values(step: dict) -> dict:
    return {
        field: label["value"]
        for field, label in step["critical_slots"].items()
        if label["source"] not in ("missing", "invalid")
    }


def _step_detail(
    group: str,
    sample: dict,
    raw: dict,
    selected: dict,
    source: str,
    step: dict,
    comparable: bool,
) -> dict:
    index = step["index"]
    mode = sample["setup"]["mode"]
    messages = (
        selected["run"].get("messages", [])
        if group == "original_openmanus"
        else raw["messages"]
    )
    turns = _assistant_turns(messages)
    calls = (
        (turns[index - 1] if index <= len(turns) else [])
        if mode == "natural_task"
        else (turns[0] if turns else [])
    )
    calls = [call for call in calls if call["name"] != "terminate"]
    evidence = f"{source}:{sample['id']}:assistant_turn_{index if mode == 'natural_task' else 1}"
    origin = "model_tool_call" if mode == "natural_task" else "controlled_injection"
    explicit = _dedupe(
        [entry for call in calls for entry in _field_entries(call, evidence, origin)]
    )
    resolved = list(explicit)
    if group != "original_openmanus" and step["expected_action"] == "tool":
        resolved += _context_entries(raw, calls, explicit, source)
        if index > 1 and mode == "controlled_tool_call":
            resolved += _reply_entries(sample, raw, calls, source)
    resolved = _dedupe(resolved)
    gold = _gold_values(step)
    model_gold = {
        field: value
        for field, value in gold.items()
        if step["critical_slots"][field]["source"] in ("task", "observation")
    }
    scoreable = comparable and step["expected_action"] == "tool"
    values = resolved if scoreable else []
    wrong_gold_values = [
        entry
        for entry in values
        if entry["field"] in gold
        and not _same(entry["field"], entry["value"], gold[entry["field"]])
    ]
    return {
        "step": index,
        "expected_action": step["expected_action"],
        "score_status": "scored"
        if scoreable and gold
        else ("zero_gold" if scoreable else "not_applicable"),
        "gold_slots": step["critical_slots"],
        "observed_slots": explicit,
        "emitted_slots": values,
        "model_explicit_slots": explicit
        if scoreable and mode == "natural_task"
        else [],
        "counts": pair_counts(gold, values) if scoreable else None,
        "model_explicit_counts": pair_counts(
            model_gold,
            [
                entry
                for entry in explicit
                if entry["field"] not in gold or entry["field"] in model_gold
            ],
        )
        if scoreable and mode == "natural_task"
        else None,
        "wrong_gold_values": len(_dedupe(wrong_gold_values)),
        "evidence": evidence,
    }


def _boundary_detail(
    group: str, sample: dict, raw: dict, source: str
) -> tuple[dict | None, dict | None, dict | None]:
    if (
        group == "original_openmanus"
        or sample["setup"]["mode"] != "controlled_tool_call"
    ):
        return None, None, None
    sid = sample["id"]
    action = sample["steps"][0]["expected_action"]
    trace = raw["trace"]
    validations = [e for e in trace if e["event"] == "validation"]
    clarifications = [e for e in trace if e["event"] == "clarification"]
    executions = [
        e for e in trace if e["event"] == "tool_execution" and e["status"] != "started"
    ]
    run = raw["run"]
    if action == "clarify":
        missing = set(run["pending_missing_fields"])
        expected = {
            field
            for field, label in sample["steps"][0]["critical_slots"].items()
            if label["source"] == "missing"
        }
        pending = [
            e
            for e in validations
            if e["status"] == "pending" and e.get("reason") == "required_missing"
        ]
        valid = [e for e in validations if e["status"] == "success"]
        call_id = run["injected_tool_call_id"]
        call_hashes = {
            e["tool_call_id"] for e in pending + valid + clarifications + executions
        }
        preserved = len(call_hashes) == 1 and run["tool_message_call_ids"] == [call_id]
        return (
            {
                "expected": sorted(expected),
                "detected": sorted(missing),
                "counts": {
                    "tp": len(expected & missing),
                    "fp": len(missing - expected),
                    "fn": len(expected - missing),
                },
                "zero_dispatch_before_reply": run["dispatch_before_reply"] == 0
                and bool(pending),
                "evidence": f"{source}:{sid}:run.pending_missing_fields+trace.validation",
            },
            {
                "scripted_reply": True,
                "valid_after_reply": bool(valid and executions)
                and [e["status"] for e in clarifications] == ["requested", "merged"],
                "original_call_id_preserved": preserved,
                "execution_count": len(executions),
                "evidence": f"{source}:{sid}:run.reply_source+trace.clarification+trace.validation+trace.tool_execution",
            },
            None,
        )
    if action == "reject":
        try:
            injected = json.loads(run["injected_arguments_json"])
        except ValueError:
            expected = "invalid_json"
        else:
            invalid = {
                key: slot["value"]
                for key, slot in sample["steps"][0]["critical_slots"].items()
                if slot["source"] == "invalid"
            }
            if len(invalid) != 1 or any(
                type(injected.get(key)) is not type(value) or injected[key] != value
                for key, value in invalid.items()
            ):
                raise ValueError(f"{sid}: invalid slot and injected input disagree")
            expected = "invalid_type"
        observed_reason = validations[0].get("reason") if validations else None
        recognized = observed_reason == (
            "invalid_json" if expected == "invalid_json" else "schema_rejected"
        )
        if expected == "invalid_type":
            recognized = recognized and ": type" in run["observation"]
        return (
            None,
            None,
            {
                "expected": expected,
                "observed_validation_reason": observed_reason,
                "recognized": recognized and not executions,
                "zero_dispatch": not executions,
                "evidence": f"{source}:{sid}:run.injected_arguments_json+trace.validation",
            },
        )
    return None, None, None


def score_group(group: str, samples: list[dict], holdout: dict) -> dict:
    ids = {sample["id"] for sample in samples}
    raws, reviews, provenance = t069._load_group(group, ids, holdout)
    rows = []
    for sample in samples:
        sid = sample["id"]
        raw = raws[sid]
        selected, source = t069._selected_run(group, raw, reviews[sid])
        if raw["support"] == "unsupported":
            mode, comparable = "unsupported", False
        elif group == "original_openmanus":
            mode = "natural_model"
            comparable = sample["profile"].startswith("mcp_stdio")
        else:
            mode = (
                "natural_model"
                if raw["actual_llm_tool_calls"]
                else "controlled_zero_llm"
            )
            if (mode == "natural_model") != (sample["setup"]["mode"] == "natural_task"):
                raise ValueError(f"{group}/{sid}: mode mismatch")
            comparable = True
        steps = [
            _step_detail(group, sample, raw, selected, source, step, comparable)
            for step in sample["steps"]
        ]
        missing, clarification, error = _boundary_detail(group, sample, raw, source)
        rows.append(
            {
                "sample_id": sid,
                "support": raw["support"],
                "mode": mode,
                "typed_slot_comparable": comparable,
                "source_file": source,
                "steps": steps,
                "missing_detection": missing,
                "clarification": clarification,
                "parameter_error_recognition": error,
            }
        )
    metrics = {}
    step_accuracy = {}
    for label, predicate, key in (
        ("natural_resolved", lambda row: row["mode"] == "natural_model", "counts"),
        (
            "natural_model_explicit",
            lambda row: row["mode"] == "natural_model",
            "model_explicit_counts",
        ),
        (
            "controlled_resolved",
            lambda row: row["mode"] == "controlled_zero_llm",
            "counts",
        ),
        ("all_typed_resolved", lambda row: True, "counts"),
    ):
        relevant = [
            step[key]
            for row in rows
            if predicate(row)
            for step in row["steps"]
            if step[key] is not None and step["score_status"] == "scored"
        ]
        metrics[label] = {"scored_steps": len(relevant), **_sum_counts(relevant)}
        selected = [
            step[key]
            for row in rows
            if predicate(row)
            for step in row["steps"]
            if step[key] is not None and step["score_status"] == "scored"
        ]
        step_accuracy[label] = {
            "all_gold_correct": sum(item["fn"] == 0 for item in selected),
            "strict_correct_no_extra": sum(
                item["fn"] == 0 and item["fp"] == 0 for item in selected
            ),
            "scored_steps": len(selected),
        }
    detected = [row["missing_detection"] for row in rows if row["missing_detection"]]
    detection_counts = {
        key: sum(row["counts"][key] for row in detected) for key in ("tp", "fp", "fn")
    }
    clarifications = [row["clarification"] for row in rows if row["clarification"]]
    errors = [
        row["parameter_error_recognition"]
        for row in rows
        if row["parameter_error_recognition"]
    ]
    coverage = Counter(row["mode"] for row in rows)
    source_channels = Counter()
    for row in rows:
        for step in row["steps"]:
            if step["score_status"] != "scored":
                continue
            for field, gold in step["gold_slots"].items():
                if gold["source"] in ("missing", "invalid"):
                    continue
                matched = next(
                    (
                        entry
                        for entry in step["emitted_slots"]
                        if entry["field"] == field
                        and _same(field, entry["value"], gold["value"])
                    ),
                    None,
                )
                if matched:
                    source_channels[f"{gold['source']} <- {matched['origin']}"] += 1
    return {
        "provenance": provenance,
        "selected_source_sha256": {
            source: t069.sha256(t069.RESULTS / source)
            for source in sorted({row["source_file"] for row in rows})
        },
        "coverage": {
            "total": len(rows),
            "unsupported": coverage["unsupported"],
            "natural_model": coverage["natural_model"],
            "controlled_zero_llm": coverage["controlled_zero_llm"],
            "unstructured_git_calls": sum(
                row["support"] == "supported" and not row["typed_slot_comparable"]
                for row in rows
            ),
        },
        "metrics": metrics,
        "matched_source_channels": dict(sorted(source_channels.items())),
        "critical_parameter_accuracy": {
            label: {
                "correct": value["tp"],
                "gold_fields": value["gold_fields"],
                "accuracy": _ratio(value["tp"], value["gold_fields"]),
                "step_accuracy": step_accuracy[label],
            }
            for label, value in metrics.items()
        },
        "missing_detection": {
            "cases": len(detected),
            "expected_fields": sum(len(item["expected"]) for item in detected),
            **_metrics(detection_counts),
            "zero_dispatch_cases": sum(
                item["zero_dispatch_before_reply"] for item in detected
            ),
        },
        "clarification": {
            "scripted_reply_cases": len(clarifications),
            "valid_after_reply": sum(
                item["valid_after_reply"]
                and item["execution_count"] == 1
                and item["original_call_id_preserved"]
                for item in clarifications
            ),
        },
        "parameter_error_recognition": {
            "expected_cases": len(errors),
            "recognized": sum(item["recognized"] for item in errors),
        },
        "unsupported_guess": {
            "confirmed_wrong_gold_values": sum(
                step["wrong_gold_values"]
                for row in rows
                for step in row["steps"]
                if row["mode"] == "natural_model"
            ),
            "controlled_wrong_gold_values": sum(
                step["wrong_gold_values"]
                for row in rows
                for step in row["steps"]
                if row["mode"] == "controlled_zero_llm"
            ),
            "unlabelled_extra_parameter_basis": "NOT_VERIFIED",
        },
        "samples": rows,
    }


def score_all() -> dict:
    holdout = json.loads(t069.DATASET.read_text())
    if holdout["version"] != "1.0.0" or holdout["scoring_sha256"] != t069.sha256(
        t069.SCORING
    ):
        raise ValueError("Frozen holdout or scoring contract mismatch")
    samples = holdout["samples"]
    if len(samples) != 21 or len({sample["id"] for sample in samples}) != 21:
        raise ValueError("Frozen holdout is incomplete")
    return {
        "metric": "T071_parameter_slot_v1",
        "dataset_version": holdout["version"],
        "dataset_sha256": t069.sha256(t069.DATASET),
        "scoring_sha256": holdout["scoring_sha256"],
        "groups": {
            group: score_group(group, samples, holdout) for group in t069.SOURCES
        },
        "limitations": [
            "Original OpenManus GitHub python_execute code has no comparable typed slot arguments",
            "Nine TaskPilot controlled samples contain zero model calls; replies are scripted",
            "No frozen natural multi-turn parameter correction or human waiting sample",
            "Model cognitive source cannot be proven from a value match; channel provenance is audited separately",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare committed result")
    args = parser.parse_args()
    result = score_all()
    if args.check:
        if not OUTPUT.exists() or json.loads(OUTPUT.read_text()) != result:
            raise SystemExit("T071 committed metrics differ from frozen evidence")
        print("T071 committed metrics match frozen evidence")
    else:
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
