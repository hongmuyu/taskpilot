"""Build a human-reviewed index over the immutable T066 raw attempts.

This records per-sample evidence and outcomes. Formal benchmark metrics belong
to T069-T071 and are intentionally absent here.
"""

import hashlib
import json
from pathlib import Path

from eval.t066_original_openmanus import classify


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval/results"
ATTEMPTS = (
    "t066_original_openmanus_v1.jsonl",
    "t066_original_openmanus_mcp_retest.jsonl",
    "t066_original_openmanus_mcp_retest_2.jsonl",
)
OUTCOMES = {
    "core-001": (
        "success",
        "Pinned README fetch and final answer cite two capabilities at the pinned ref.",
    ),
    "core-002": (
        "failure",
        "Six-step budget ended without the required pinned source explanation.",
    ),
    "core-003": (
        "success",
        "Issue #895 body was fetched and the answer cites the issue and reported cookie conflict.",
    ),
    "core-004": (
        "failure",
        "Six-step budget ended without verified issue investigation output.",
    ),
    "core-005": (
        "failure",
        "Six-step budget ended without linked issue-to-code evidence.",
    ),
    "core-006": (
        "failure",
        "Six-step budget ended without linked issue-to-code evidence.",
    ),
    "core-013": (
        "success",
        "v1 MCP audit has one echo_read(text=holdout-v1); returned echo:holdout-v1.",
    ),
    "core-015": (
        "success",
        "v2 MCP audit has one echo_read(text=base,suffix=tail); returned echo:base:tail.",
    ),
    "core-016": (
        "failure",
        "Server returned a controlled isError failure, but original MCP ToolResult lost failure status and Agent declared terminate(success).",
    ),
    "core-018": (
        "failure",
        "Agent did not obtain or classify an actual GitHub 404 before step budget ended.",
    ),
    "core-020": (
        "failure",
        "Six-step budget ended without the required next-step issue/source evidence.",
    ),
    "core-021": (
        "failure",
        "Six-step budget ended without the required multi-step issue/source evidence.",
    ),
}


def build_review():
    dataset_path = ROOT / "eval/datasets/core_benchmark_v1_holdout.json"
    scoring_path = ROOT / "eval/datasets/core_benchmark_v1_scoring.json"
    dataset = json.loads(dataset_path.read_text())
    if (
        dataset["scoring_sha256"]
        != hashlib.sha256(scoring_path.read_bytes()).hexdigest()
    ):
        raise ValueError("Frozen scoring hash changed")
    attempts = {}
    for name in ATTEMPTS:
        for line_number, line in enumerate(
            (RESULTS / name).read_text().splitlines(), 1
        ):
            row = json.loads(line)
            attempts.setdefault(row["sample_id"], []).append((name, line_number, row))
    reviews = []
    for sample in dataset["samples"]:
        sample_id = sample["id"]
        decision = classify(sample)
        rows = attempts[sample_id]
        final_name, final_line, final = rows[-1]
        if final["support"] != decision["support"]:
            raise ValueError(f"Support classification changed: {sample_id}")
        run = final["run"]
        calls = [
            {"tool_call_id": call["id"], "tool": call["name"]}
            for message in run.get("messages", [])
            for call in message.get("tool_calls", [])
        ]
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        usage_available = bool(final["provider_requests"])
        for request in final["provider_requests"]:
            provider_usage = request.get("usage")
            if provider_usage is None or any(
                provider_usage.get(key) is None
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            ):
                usage_available = False
                continue
            usage["input_tokens"] += provider_usage["prompt_tokens"]
            usage["output_tokens"] += provider_usage["completion_tokens"]
            usage["total_tokens"] += provider_usage["total_tokens"]
        outcome, rationale = (
            OUTCOMES[sample_id]
            if decision["support"] == "supported"
            else ("unsupported", decision["reason"])
        )
        reviews.append(
            {
                "sample_id": sample_id,
                "support": decision["support"],
                "outcome": outcome,
                "review_reason": rationale,
                "attempts": [
                    {
                        "file": name,
                        "line": line_number,
                        "run_status": row["run"]["run_status"],
                    }
                    for name, line_number, row in rows
                ],
                "selected_attempt": {"file": final_name, "line": final_line},
                "run_status": run["run_status"],
                "cleanup_status": run.get("cleanup_status", "not_started"),
                "tool_calls": calls,
                "mcp_server_dispatches": len(run.get("mcp_audit", [])),
                "provider_http_statuses": [
                    r["http_status"] for r in final["provider_requests"]
                ],
                "provider_reported_usage": usage
                if usage_available
                else "NOT AVAILABLE",
                "elapsed_ms": run.get("elapsed_ms"),
            }
        )
    return {
        "review_kind": "T066 original OpenManus per-sample evidence index, not formal benchmark metrics",
        "dataset_version": dataset["version"],
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "scoring_sha256": dataset["scoring_sha256"],
        "upstream_commit": "3309bf4e416fb1c74b008f3e86494439a31bad53",
        "samples": reviews,
    }


if __name__ == "__main__":
    target = RESULTS / "t066_original_openmanus_review.json"
    target.write_text(json.dumps(build_review(), ensure_ascii=False, indent=2) + "\n")
    print(target)
