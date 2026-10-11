"""Recompute T070 provider usage and elapsed costs from frozen run evidence."""

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from app.taskpilot.execution_trace import summarize_trace_costs
from eval import t069_score_accuracy_success as t069
from eval.t068_k3_baseline import compare_fixed_contract


RESULTS = t069.RESULTS
OUTPUT = RESULTS / "t070_cost_latency_v1.json"
TOKEN_FIELDS = ("input", "output", "total")
LATENCY_FIELDS = ("end_to_end", "llm", "embedding", "tool", "retry", "human_wait")


def aggregate_measurements(values: list[int | float | None]) -> dict:
    """Preserve the eligible denominator and never turn missing data into zero."""
    known = [value for value in values if value is not None]
    count = len(values)
    return {
        "eligible": count,
        "available": len(known),
        "missing": count - len(known),
        "known_sum": sum(known),
        "total": sum(known) if len(known) == count and count else None,
        "mean": statistics.mean(known) if known else None,
        "median": statistics.median(known) if known else None,
    }


def _usage_field(attempts: list[dict], source_key: str) -> dict:
    known = []
    for attempt in attempts:
        value = (
            attempt.get(source_key)
            if attempt.get("usage_source") == "provider"
            else None
        )
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("Invalid provider usage")
        known.append(value)
    unknown = len(attempts) - len(known)
    return {
        "value": sum(known) if not unknown else None,
        "known_sum": sum(known),
        "unknown_attempts": unknown,
    }


def measure_provider_requests(requests: list[dict]) -> dict:
    """Count every original provider request, including unsuccessful requests."""
    normalized = [
        {
            "usage_source": "provider" if request.get("usage") else "unknown",
            **(
                {
                    "input_tokens": request["usage"].get("prompt_tokens"),
                    "output_tokens": request["usage"].get("completion_tokens"),
                    "total_tokens": request["usage"].get("total_tokens"),
                }
                if request.get("usage")
                else {}
            ),
        }
        for request in requests
    ]
    return {
        field: _usage_field(normalized, f"{field}_tokens") for field in TOKEN_FIELDS
    }


def _attempt_usage(attempts: list[dict]) -> dict:
    return {field: _usage_field(attempts, f"{field}_tokens") for field in TOKEN_FIELDS}


def retry_costs(trace: list[dict]) -> dict:
    """Retry diagnostics overlap with LLM/tool phase work and are not additive."""
    tool_extra = [
        event
        for event in trace
        if event["event"] == "tool_execution"
        and event["status"] != "started"
        and event.get("attempt", 1) > 1
    ]
    llm_extra = [
        event
        for event in trace
        if event["event"] == "llm_provider_attempt" and event["llm_attempt"] > 1
    ]
    waits = [
        event for event in trace if event["event"] in {"retry_wait", "llm_retry_wait"}
    ]
    additional = tool_extra + llm_extra
    missing_duration = sum("duration_ms" not in event for event in additional)
    return {
        "tool_extra_attempts": len(tool_extra),
        "llm_extra_attempts": len(llm_extra),
        "wait_ms": sum(event["duration_ms"] for event in waits),
        "extra_attempt_duration_ms": (
            None
            if missing_duration
            else sum(event["duration_ms"] for event in additional)
        ),
        "extra_attempts_missing_duration": missing_duration,
        "extra_provider_tokens": _attempt_usage(llm_extra),
    }


def _not_attempted_usage() -> dict:
    return {
        field: {"value": None, "known_sum": 0, "unknown_attempts": 0}
        for field in TOKEN_FIELDS
    }


def _original_case(raw: dict, review: dict, t069_case: dict) -> dict:
    sid = raw["sample_id"]
    pointer = review["selected_attempt"]
    filename = pointer["file"]
    if filename == "t066_original_openmanus_v1.jsonl":
        selected = raw
    elif filename == "t066_original_openmanus_mcp_retest_2.jsonl":
        selected = t069.load_jsonl(RESULTS / filename)[pointer["line"] - 1]
    else:
        raise ValueError(f"{sid}: unknown selected upstream run")
    if selected["sample_id"] != sid or filename != t069_case["selected_run_file"]:
        raise ValueError(f"{sid}: T069 selected run disagrees")
    if (
        review["attempts"][-1]["file"] != filename
        or review["attempts"][-1]["line"] != pointer["line"]
    ):
        raise ValueError(
            f"{sid}: upstream selected attempt is not the final review attempt"
        )
    prior_failures = []
    for attempt in review["attempts"][:-1]:
        prior = t069.load_jsonl(RESULTS / attempt["file"])[attempt["line"] - 1]
        if (
            prior["sample_id"] != sid
            or prior["run"]["run_status"] != attempt["run_status"]
        ):
            raise ValueError(f"{sid}: prior upstream attempt pointer mismatch")
        prior_failures.append(
            {
                "source_file": attempt["file"],
                "run_status": attempt["run_status"],
                "elapsed_ms": prior["run"].get("elapsed_ms"),
                "provider_attempts": len(prior["provider_requests"]),
                "provider_tokens": measure_provider_requests(
                    prior["provider_requests"]
                ),
            }
        )
    supported = selected["support"] == "supported"
    if supported and selected["run"]["run_status"] != "returned":
        raise ValueError(f"{sid}: selected upstream run did not return")
    if not supported:
        return {
            "sample_id": sid,
            "support": "unsupported",
            "mode": "unsupported",
            "business_outcome": t069_case["business_outcome"],
            "source_file": filename,
            "prior_setup_failures": prior_failures,
            "provider_attempts": 0,
            "provider_status_counts": {},
            "tool_attempt_status_counts": None,
            "provider_tokens": _not_attempted_usage(),
            "embedding": None,
            "retry": None,
            "latency_ms": {field: None for field in LATENCY_FIELDS},
            "agent_trace_ms": None,
        }
    requests = selected["provider_requests"]
    usage = measure_provider_requests(requests)
    upstream = selected["run"]["provider_usage_from_upstream_counters"]
    for field, upstream_field in (
        ("input", "input_tokens"),
        ("output", "output_tokens"),
        ("total", "total_tokens"),
    ):
        if usage[field]["value"] is not None and upstream is not None:
            if usage[field]["value"] != upstream[upstream_field]:
                raise ValueError(f"{sid}: upstream usage counter disagrees")
    llm_elapsed = [request.get("elapsed_ms") for request in requests]
    if selected["run"].get("elapsed_ms") is None:
        raise ValueError(f"{sid}: missing upstream task clock")
    return {
        "sample_id": sid,
        "support": "supported",
        "mode": "natural_model",
        "business_outcome": t069_case["business_outcome"],
        "source_file": filename,
        "prior_setup_failures": prior_failures,
        "provider_attempts": len(requests),
        "provider_status_counts": dict(
            sorted(Counter(str(r["http_status"]) for r in requests).items())
        ),
        "tool_attempt_status_counts": None,
        "provider_tokens": usage,
        "embedding": {
            "calls": 0,
            "index_calls": 0,
            "query_calls": 0,
            "duration_ms": 0,
            "index_duration_ms": 0,
            "query_duration_ms": 0,
            "failed_calls": 0,
        },
        "retry": None,
        "latency_ms": {
            "end_to_end": selected["run"]["elapsed_ms"],
            "llm": sum(llm_elapsed)
            if all(x is not None for x in llm_elapsed)
            else None,
            "embedding": 0,
            "tool": None,
            "retry": None,
            "human_wait": None,
        },
        "agent_trace_ms": None,
    }


def _taskpilot_case(raw: dict, t069_case: dict) -> dict:
    sid = raw["sample_id"]
    if raw["trace_validation"] != "valid" or raw["cleanup_status"] != "complete":
        raise ValueError(f"{sid}: Trace or cleanup invalid")
    measured = summarize_trace_costs(raw["trace"])
    if measured != raw["trace_costs"]:
        raise ValueError(f"{sid}: saved T055 costs differ from Trace")
    if raw["elapsed_ms"] is None:
        raise ValueError(f"{sid}: missing complete task clock")
    if raw["elapsed_ms"] < measured["latency_ms"]["end_to_end"]:
        raise ValueError(f"{sid}: outer clock shorter than Agent Trace")
    attempts = [
        event for event in raw["trace"] if event["event"] == "llm_provider_attempt"
    ]
    if raw["actual_llm_tool_calls"] != len(raw["schema_calls"]):
        raise ValueError(f"{sid}: LLM call audit mismatch")
    if measured["provider_attempts"] != len(attempts):
        raise ValueError(f"{sid}: provider attempt count mismatch")
    if attempts and raw["provider_usage"] != measured["tokens"]:
        raise ValueError(f"{sid}: provider usage differs from Trace")
    embeddings = [event for event in raw["trace"] if event["event"] == "embedding"]
    if len(embeddings) != raw["embedding_events"]:
        raise ValueError(f"{sid}: embedding event count mismatch")
    missing_embedding_duration = any("duration_ms" not in item for item in embeddings)
    clarification_requested = any(
        event["event"] == "clarification" and event["status"] == "requested"
        for event in raw["trace"]
    )
    wait_measured = any(
        event["event"] == "clarification"
        and event["status"] in {"waited", "cancelled"}
        and "duration_ms" in event
        for event in raw["trace"]
    )
    latency = {**measured["latency_ms"], "end_to_end": raw["elapsed_ms"]}
    if clarification_requested and not wait_measured:
        # Frozen replies are injected immediately; no actual human wait clock.
        latency["human_wait"] = None
    if missing_embedding_duration:
        latency["embedding"] = None
    index_events = [e for e in embeddings if e["embedding_phase"] == "index"]
    query_events = [e for e in embeddings if e["embedding_phase"] == "query"]
    tool_results = [
        e
        for e in raw["trace"]
        if e["event"] == "tool_execution" and e["status"] != "started"
    ]
    return {
        "sample_id": sid,
        "support": "supported",
        "mode": t069_case["mode"],
        "business_outcome": t069_case["business_outcome"],
        "source_file": "t067_full_tools_v1.jsonl"
        if raw["routing_top_k"] is None
        else "t068_k3_v1.jsonl",
        "prior_setup_failures": [],
        "provider_attempts": len(attempts),
        "provider_status_counts": dict(
            sorted(Counter(e["status"] for e in attempts).items())
        ),
        "tool_attempt_status_counts": dict(
            sorted(Counter(e["status"] for e in tool_results).items())
        ),
        "provider_tokens": measured["tokens"],
        "embedding": {
            "calls": len(embeddings),
            "index_calls": sum(e["embedding_phase"] == "index" for e in embeddings),
            "query_calls": sum(e["embedding_phase"] == "query" for e in embeddings),
            "duration_ms": latency["embedding"],
            "index_duration_ms": (
                sum(e["duration_ms"] for e in index_events)
                if all("duration_ms" in e for e in index_events)
                else None
            ),
            "query_duration_ms": (
                sum(e["duration_ms"] for e in query_events)
                if all("duration_ms" in e for e in query_events)
                else None
            ),
            "failed_calls": sum(e["status"] != "success" for e in embeddings),
        },
        "retry": retry_costs(raw["trace"]),
        "latency_ms": {field: latency[field] for field in LATENCY_FIELDS},
        "agent_trace_ms": measured["latency_ms"]["end_to_end"],
    }


def _summarize_cases(cases: list[dict]) -> dict:
    attempted = [case for case in cases if case["support"] == "supported"]
    return {
        "eligible": len(attempted),
        "provider_attempts": aggregate_measurements(
            [case["provider_attempts"] for case in attempted]
        ),
        "tokens": {
            field: aggregate_measurements(
                [case["provider_tokens"][field]["value"] for case in attempted]
            )
            for field in TOKEN_FIELDS
        },
        "unknown_provider_attempts": {
            field: sum(
                case["provider_tokens"][field]["unknown_attempts"] for case in attempted
            )
            for field in TOKEN_FIELDS
        },
        "latency_ms": {
            field: aggregate_measurements(
                [case["latency_ms"][field] for case in attempted]
            )
            for field in LATENCY_FIELDS
        },
        "embedding_calls": aggregate_measurements(
            [
                case["embedding"]["calls"] if case["embedding"] else None
                for case in attempted
            ]
        ),
        "embedding_index_calls": aggregate_measurements(
            [
                case["embedding"]["index_calls"] if case["embedding"] else None
                for case in attempted
            ]
        ),
        "embedding_query_calls": aggregate_measurements(
            [
                case["embedding"]["query_calls"] if case["embedding"] else None
                for case in attempted
            ]
        ),
        "embedding_index_duration_ms": aggregate_measurements(
            [
                case["embedding"]["index_duration_ms"] if case["embedding"] else None
                for case in attempted
            ]
        ),
        "embedding_query_duration_ms": aggregate_measurements(
            [
                case["embedding"]["query_duration_ms"] if case["embedding"] else None
                for case in attempted
            ]
        ),
        "retry_tool_extra_attempts": aggregate_measurements(
            [
                case["retry"]["tool_extra_attempts"] if case["retry"] else None
                for case in attempted
            ]
        ),
        "retry_llm_extra_attempts": aggregate_measurements(
            [
                case["retry"]["llm_extra_attempts"] if case["retry"] else None
                for case in attempted
            ]
        ),
        "retry_extra_attempt_duration_ms": aggregate_measurements(
            [
                case["retry"]["extra_attempt_duration_ms"] if case["retry"] else None
                for case in attempted
            ]
        ),
        "retry_extra_provider_tokens": {
            field: aggregate_measurements(
                [
                    case["retry"]["extra_provider_tokens"][field]["value"]
                    if case["retry"]
                    else None
                    for case in attempted
                ]
            )
            for field in TOKEN_FIELDS
        },
    }


def _group(cases: list[dict], provenance: dict) -> dict:
    prior = [attempt for case in cases for attempt in case["prior_setup_failures"]]
    provider_statuses = Counter()
    tool_statuses = Counter()
    for case in cases:
        provider_statuses.update(case["provider_status_counts"])
        tool_statuses.update(case["tool_attempt_status_counts"] or {})
    return {
        "provenance": provenance,
        "prior_setup_failures": {
            "attempts": len(prior),
            "elapsed_ms": aggregate_measurements([a["elapsed_ms"] for a in prior]),
            "provider_attempts": sum(a["provider_attempts"] for a in prior),
        },
        "provider_attempt_status_counts": dict(sorted(provider_statuses.items())),
        "tool_attempt_status_counts": dict(sorted(tool_statuses.items()))
        if tool_statuses
        else None,
        "coverage": {
            "holdout": len(cases),
            "supported_attempted": sum(c["support"] == "supported" for c in cases),
            "unsupported": sum(c["support"] == "unsupported" for c in cases),
            "natural_model": sum(c["mode"] == "natural_model" for c in cases),
            "controlled_zero_llm": sum(
                c["mode"] == "controlled_zero_llm" for c in cases
            ),
            "business_outcomes": dict(
                sorted(Counter(c["business_outcome"] for c in cases).items())
            ),
        },
        "all_supported": _summarize_cases(cases),
        "natural_model": _summarize_cases(
            [c for c in cases if c["mode"] == "natural_model"]
        ),
        "controlled_zero_llm": _summarize_cases(
            [c for c in cases if c["mode"] == "controlled_zero_llm"]
        ),
        "samples": cases,
    }


def _pair_group(rows: list[dict]) -> dict:
    metrics = {
        "end_to_end_ms": "end_to_end",
        "llm_ms": "llm",
        "embedding_ms": "embedding",
        "tool_ms": "tool",
        "retry_wait_ms": "retry",
        "human_wait_ms": "human_wait",
    }
    result = {"eligible": len(rows), "k3_minus_full": {}}
    for label, field in metrics.items():
        values = [row["delta_latency_ms"][field] for row in rows]
        result["k3_minus_full"][label] = {
            **aggregate_measurements(values),
            "k3_higher": sum(value > 0 for value in values if value is not None),
            "k3_lower": sum(value < 0 for value in values if value is not None),
            "same": sum(value == 0 for value in values if value is not None),
        }
    for field in TOKEN_FIELDS:
        values = [row["delta_tokens"][field] for row in rows]
        result["k3_minus_full"][f"{field}_tokens"] = {
            **aggregate_measurements(values),
            "k3_higher": sum(value > 0 for value in values if value is not None),
            "k3_lower": sum(value < 0 for value in values if value is not None),
            "same": sum(value == 0 for value in values if value is not None),
        }
    return result


def _paired(full: list[dict], k3: list[dict]) -> dict:
    by_id = {case["sample_id"]: case for case in k3}
    if len(by_id) != 21 or {c["sample_id"] for c in full} != set(by_id):
        raise ValueError("TaskPilot sample IDs do not pair exactly")
    rows = []
    for first in full:
        second = by_id[first["sample_id"]]
        if first["support"] != second["support"] or first["mode"] != second["mode"]:
            raise ValueError("TaskPilot paired support or mode differs")
        rows.append(
            {
                "sample_id": first["sample_id"],
                "mode": first["mode"],
                "full_business_outcome": first["business_outcome"],
                "k3_business_outcome": second["business_outcome"],
                "delta_tokens": {
                    field: (
                        second["provider_tokens"][field]["value"]
                        - first["provider_tokens"][field]["value"]
                        if first["provider_tokens"][field]["value"] is not None
                        and second["provider_tokens"][field]["value"] is not None
                        else None
                    )
                    for field in TOKEN_FIELDS
                },
                "delta_latency_ms": {
                    field: (
                        second["latency_ms"][field] - first["latency_ms"][field]
                        if first["latency_ms"][field] is not None
                        and second["latency_ms"][field] is not None
                        else None
                    )
                    for field in LATENCY_FIELDS
                },
            }
        )
    return {
        "all_21": _pair_group(rows),
        "natural_12": _pair_group([r for r in rows if r["mode"] == "natural_model"]),
        "controlled_9": _pair_group(
            [r for r in rows if r["mode"] == "controlled_zero_llm"]
        ),
        "samples": rows,
    }


def score_all() -> dict:
    prior = t069.score_all()
    prior_cases = {
        name: {case["sample_id"]: case for case in group["samples"]}
        for name, group in prior["groups"].items()
    }
    original_raw = {
        row["sample_id"]: row
        for row in t069.load_jsonl(RESULTS / "t066_original_openmanus_v1.jsonl")
    }
    original_review = {
        row["sample_id"]: row
        for row in json.loads(
            (RESULTS / "t066_original_openmanus_review.json").read_text()
        )["samples"]
    }
    ids = [
        sample["sample_id"]
        for sample in prior["groups"]["original_openmanus"]["samples"]
    ]
    original = [
        _original_case(
            original_raw[sid],
            original_review[sid],
            prior_cases["original_openmanus"][sid],
        )
        for sid in ids
    ]
    full_rows = t069.load_jsonl(RESULTS / "t067_full_tools_v1.jsonl")
    k3_rows = t069.load_jsonl(RESULTS / "t068_k3_v1.jsonl")
    compare_fixed_contract(full_rows[0], k3_rows[0])
    full_raw = {row["sample_id"]: row for row in full_rows[1:]}
    k3_raw = {row["sample_id"]: row for row in k3_rows[1:]}
    full = [
        _taskpilot_case(full_raw[sid], prior_cases["taskpilot_full"][sid])
        for sid in ids
    ]
    k3 = [_taskpilot_case(k3_raw[sid], prior_cases["taskpilot_k3"][sid]) for sid in ids]
    paired = _paired(full, k3)
    paired["fixed_contract_verified"] = True
    original_provenance = {
        **prior["groups"]["original_openmanus"]["provenance"],
        "selected_mcp_retest_sha256": t069.sha256(
            RESULTS / "t066_original_openmanus_mcp_retest_2.jsonl"
        ),
    }
    return {
        "metric_version": "t070-v1",
        "dataset_version": prior["dataset_version"],
        "dataset_sha256": prior["dataset_sha256"],
        "scoring_sha256": prior["scoring_sha256"],
        "measurement_policy": {
            "end_to_end": "runner monotonic elapsed through cleanup; supported attempted only",
            "phase": "T055 trace duration sums are work time and may overlap; original LLM phase is provider-request elapsed only",
            "missing": "None means NOT AVAILABLE or not attempted; zero only when no corresponding call occurred",
            "human_wait": "controlled immediate replies have no measured human wait and remain NOT AVAILABLE",
            "retry": "additional attempts plus backoff are diagnostic subsets of LLM/tool/end-to-end, never additive",
            "outcome": "T069 adjudications carried unchanged; core-011 routing and business-versus-expected-outcome questions remain for T072",
        },
        "groups": {
            "original_openmanus": _group(original, original_provenance),
            "taskpilot_full": _group(
                full, prior["groups"]["taskpilot_full"]["provenance"]
            ),
            "taskpilot_k3": _group(k3, prior["groups"]["taskpilot_k3"]["provenance"]),
        },
        "taskpilot_paired": paired,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = score_all()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    for name, group in result["groups"].items():
        summary = group["all_supported"]
        print(
            name,
            "tasks",
            summary["eligible"],
            "tokens",
            summary["tokens"]["total"]["total"],
            "elapsed_ms",
            summary["latency_ms"]["end_to_end"]["total"],
        )


if __name__ == "__main__":
    main()
