"""Index frozen benchmark failures and evidence without changing prior scores."""

import argparse
import json

from eval import t069_score_accuracy_success as t069
from eval import t070_cost_latency as t070
from eval import t071_slot_metrics as t071


OUTPUT = t069.RESULTS / "t072_failure_audit_v1.json"
CLASSIFICATIONS = {
    "original_openmanus": {
        "core-002": ["Context / Evidence", "Timeout / Retry / Budget"],
        "core-004": ["Context / Evidence", "Timeout / Retry / Budget"],
        "core-005": ["Context / Evidence", "Timeout / Retry / Budget"],
        "core-006": ["Context / Evidence", "Timeout / Retry / Budget"],
        "core-016": ["Tool / MCP"],
        "core-018": ["Context / Evidence", "Timeout / Retry / Budget"],
        "core-020": ["Context / Evidence", "Timeout / Retry / Budget"],
        "core-021": ["Context / Evidence", "Timeout / Retry / Budget"],
    },
    "taskpilot_full": {
        "core-005": [
            "Context / Evidence",
            "Permission / Network",
            "Timeout / Retry / Budget",
        ],
        "core-006": [
            "Routing",
            "Parameter / Slot",
            "Context / Evidence",
            "Permission / Network",
        ],
        "core-011": ["Context / Evidence"],
    },
    "taskpilot_k3": {
        "core-005": [
            "Routing",
            "Parameter / Slot",
            "Context / Evidence",
            "Permission / Network",
            "Timeout / Retry / Budget",
        ],
        "core-006": [
            "Routing",
            "Parameter / Slot",
            "Context / Evidence",
            "Permission / Network",
        ],
        "core-020": ["Context / Evidence", "Parameter / Slot"],
    },
}
PROBES = ("core-016", "core-017", "core-018", "core-019")


def _evidence(group: str, sid: str, raw: dict, review: dict) -> dict:
    if group == "original_openmanus":
        selected, filename = t069._selected_run(group, raw, review)
        pointer = review["selected_attempt"]
        return {
            "benchmark_run_id": f"t066:{sid}:{filename}:{pointer['line']}",
            "runtime_run_id": None,
            "source": f"eval/results/{filename}:{pointer['line']}",
            "runtime_trace": "NOT AVAILABLE in pinned upstream",
            "call_ids": [
                call.get("id")
                for message in selected["run"].get("messages", [])
                for call in message.get("tool_calls", [])
                if call.get("id")
            ],
            "provider_http_statuses": [
                x.get("http_status") for x in selected.get("provider_requests", [])
            ],
            "mcp_dispatches": selected["run"].get("mcp_audit", []),
        }
    trace = raw["trace"]
    run_id = trace[0]["run_id"]
    terminal = [
        event
        for event in trace
        if event["event"] in {"tool_execution", "validation", "finish", "observation"}
        and event.get("status") in {"failure", "unknown", "cancelled"}
    ]
    if not terminal:
        terminal = [event for event in trace if event["event"] == "tool_execution"][:1]
    return {
        "benchmark_run_id": f"{group}:{sid}",
        "runtime_run_id": run_id,
        "source": f"eval/results/{t069.SOURCES[group][0]}:{sid}",
        "trace_event_ids": [event["event_id"] for event in terminal[:8]],
        "tool_call_ids": list(
            dict.fromkeys(
                event["tool_call_id"] for event in terminal if event.get("tool_call_id")
            )
        ),
        "github_http_statuses": [
            event.get("http_status") for event in raw["github_http"]
        ],
        "mcp_tool_names": [event.get("tool") for event in raw["mcp_audit"]],
        "routing_event_count": len(raw["routing_events"]),
        "schema_call_count": len(raw["schema_calls"]),
        "cleanup_status": raw["cleanup_status"],
    }


def build_audit() -> dict:
    scored = t069.score_all()
    cost = json.loads((t069.RESULTS / "t070_cost_latency_v1.json").read_text())
    slots = json.loads((t069.RESULTS / "t071_slot_metrics_v1.json").read_text())
    if scored != json.loads(
        (t069.RESULTS / "t069_accuracy_success_v1.json").read_text()
    ):
        raise ValueError("T069 committed result no longer matches frozen evidence")
    if cost != t070.score_all() or slots != t071.score_all():
        raise ValueError("T070/T071 committed results no longer match frozen evidence")
    if (
        scored["dataset_sha256"] != cost["dataset_sha256"]
        or scored["dataset_sha256"] != slots["dataset_sha256"]
    ):
        raise ValueError("Metric dataset hash mismatch")
    cases = []
    unsupported = []
    probes = []
    for group, group_result in scored["groups"].items():
        raw, reviews, _ = t069._load_group(
            group,
            set(x["sample_id"] for x in group_result["samples"]),
            json.loads(t069.DATASET.read_text()),
        )
        actual_non_success = {
            x["sample_id"]
            for x in group_result["samples"]
            if x["business_outcome"] in {"failure", "not_verified"}
        }
        if actual_non_success != set(CLASSIFICATIONS[group]):
            raise ValueError(
                f"{group}: failure classifications are incomplete or stale"
            )
        for sample in group_result["samples"]:
            sid, outcome = sample["sample_id"], sample["business_outcome"]
            if outcome in {"failure", "not_verified"}:
                cases.append(
                    {
                        "group": group,
                        "sample_id": sid,
                        "outcome": outcome,
                        "categories": CLASSIFICATIONS[group][sid],
                        "reason": sample["business_review_reason"],
                        "causal_limit": (
                            "Verified from server result or frozen criterion"
                            if (group, sid)
                            in {
                                ("original_openmanus", "core-016"),
                                ("taskpilot_full", "core-011"),
                                ("taskpilot_k3", "core-020"),
                            }
                            else "Proximate failure verified; why the model chose this path is UNKNOWN"
                        ),
                        "evidence": _evidence(group, sid, raw[sid], reviews[sid]),
                    }
                )
            if outcome == "unsupported":
                unsupported.append(
                    {
                        "group": group,
                        "sample_id": sid,
                        "reason": raw[sid]["support_reason"],
                        "source": f"eval/results/{t069.SOURCES[group][0]}:{sid}",
                    }
                )
            if group != "original_openmanus" and sid in PROBES:
                probes.append(
                    {
                        "group": group,
                        "sample_id": sid,
                        "expected_outcome_scored": outcome,
                        "evidence": _evidence(group, sid, raw[sid], reviews[sid]),
                        "tool_attempt_status_counts": next(
                            x
                            for x in cost["groups"][group]["samples"]
                            if x["sample_id"] == sid
                        )["tool_attempt_status_counts"],
                    }
                )
    losses = []
    for sid in ("core-004", "core-011", "core-020"):
        full = next(
            x
            for x in cost["groups"]["taskpilot_full"]["samples"]
            if x["sample_id"] == sid
        )
        k3 = next(
            x
            for x in cost["groups"]["taskpilot_k3"]["samples"]
            if x["sample_id"] == sid
        )
        losses.append(
            {
                "sample_id": sid,
                "k3_minus_full_provider_tokens": k3["provider_tokens"]["total"]["value"]
                - full["provider_tokens"]["total"]["value"],
                "k3_minus_full_end_to_end_ms": round(
                    k3["latency_ms"]["end_to_end"] - full["latency_ms"]["end_to_end"], 1
                ),
                "provider_attempts_full": full["provider_attempts"],
                "provider_attempts_k3": k3["provider_attempts"],
            }
        )
    slot_errors = []
    for group in ("taskpilot_full", "taskpilot_k3"):
        for sample in slots["groups"][group]["samples"]:
            for step in sample["steps"]:
                counts = step.get("counts") or {}
                if counts.get("fp", 0) or counts.get("fn", 0):
                    slot_errors.append(
                        {
                            "group": group,
                            "sample_id": sample["sample_id"],
                            "step": step["step"],
                            "counts": counts,
                            "gold_fields": list(step["gold_slots"]),
                            "emitted_fields_and_sources": [
                                {"field": item["field"], "origin": item["origin"]}
                                for item in step["emitted_slots"]
                            ],
                            "source": step["evidence"],
                        }
                    )
    return {
        "audit_version": "t072-v1",
        "dataset_version": scored["dataset_version"],
        "dataset_sha256": scored["dataset_sha256"],
        "scoring_sha256": scored["scoring_sha256"],
        "metric_versions": [
            scored["metric_version"],
            cost["metric_version"],
            slots["metric"],
        ],
        "cases": cases,
        "unsupported": unsupported,
        "expected_fault_probes": probes,
        "k3_negative_pairs": losses,
        "slot_error_steps": slot_errors,
        "core011_full_formal_outcome": "not_verified",
        "core011_full_diagnostic_business_evidence_without_routing_criterion": "satisfied per T067 review; not a formal rescore",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = build_audit()
    if args.check:
        if not OUTPUT.exists() or json.loads(OUTPUT.read_text()) != result:
            raise SystemExit("T072 audit differs from frozen evidence")
    else:
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        "T072 audit",
        len(result["cases"]),
        "non-success,",
        len(result["unsupported"]),
        "unsupported,",
        len(result["expected_fault_probes"]),
        "fault probes",
    )


if __name__ == "__main__":
    main()
