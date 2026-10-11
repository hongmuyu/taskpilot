"""Run one T062 real HTTP deployment case against a running Docker container.

Uses only the Python standard library. The saved evidence excludes prompts,
tool arguments, model responses, raw log lines, and credentials.
"""

import argparse
import hashlib
import json
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


REPOSITORY = "FoundationAgents/OpenManus"
DIRECT_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
TERMINAL = {"finished", "failed", "unknown", "cancelled", "clarification_timeout"}


def request(base_url: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    message = urllib.request.Request(
        base_url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with DIRECT_HTTP.open(message, timeout=10) as response:
        return response.status, json.load(response)


def poll(base_url: str, task_id: str, wanted: set[str], timeout: float = 240):
    started = time.monotonic()
    views = 0
    while time.monotonic() - started < timeout:
        code, view = request(base_url, "GET", f"/tasks/{task_id}")
        views += 1
        if code != 200 or view["status"] in wanted:
            return code, view, views
        time.sleep(1)
    return None, None, views


def logs(container_id: str, since: float) -> str:
    result = subprocess.run(
        ["docker", "logs", "--since", str(since), container_id],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    return result.stdout


def events_for_run(log_text: str, run_id: str | None) -> list[dict]:
    events = []
    for line in log_text.splitlines():
        if "execution_trace_event " not in line:
            continue
        try:
            item = json.loads(line.split("execution_trace_event ", 1)[1])
        except ValueError:
            continue
        if run_id is None or item.get("run_id") == run_id:
            events.append(item)
    return events


def github_requests(log_text: str) -> list[dict]:
    requests = []
    for line in log_text.splitlines():
        if "github_tool name=" not in line:
            continue
        match = re.search(
            r"github_tool name=(\w+).*?status=(\w+) http_status=(\d+|None)", line
        )
        if match:
            requests.append(
                {
                    "tool": match.group(1),
                    "status": match.group(2),
                    "http_status": (
                        int(match.group(3)) if match.group(3) != "None" else None
                    ),
                }
            )
    return requests


def retry_attempts(log_text: str) -> list[dict]:
    attempts = []
    for line in log_text.splitlines():
        if "tool_retry_attempt name=" not in line:
            continue
        match = re.search(
            r"tool_retry_attempt name=(\w+) number=(\d+) "
            r"classification=(\w+) http_status=(\d+|None) status=(\w+)",
            line,
        )
        if match:
            attempts.append(
                {
                    "tool": match.group(1),
                    "number": int(match.group(2)),
                    "classification": match.group(3),
                    "http_status": (
                        int(match.group(4)) if match.group(4) != "None" else None
                    ),
                    "status": match.group(5),
                }
            )
    return attempts


def trace_valid(events: list[dict], run_id: str, container_id: str) -> bool:
    linked = (
        bool(events)
        and all(
            item.get("schema_version") == "1.1"
            and item.get("run_id") == run_id
            and item.get("seq") == seq
            and item.get("event_id") == f"{run_id}:{seq}"
            for seq, item in enumerate(events, 1)
        )
        and events[0]["event"] == "task"
        and events[-1]["event"] == "finish"
    )
    if not linked:
        return False
    validator = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            container_id,
            "python",
            "-c",
            "import json,sys; from app.taskpilot.execution_trace import "
            "validate_trace_events; validate_trace_events(json.load(sys.stdin))",
        ],
        input=json.dumps(events),
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return validator.returncode == 0


def case_result(case: str, *, port: int, container_id: str) -> dict:
    base_url = f"http://127.0.0.1:{port}"
    started = time.monotonic()
    since = time.time() - 2
    health_http, health = request(base_url, "GET", "/health")
    if case == "normal":
        task = "Read README.md and summarize this repository's purpose with source evidence."
    elif case == "clarification":
        task = "Read README.md from this repository, summarize what it provides, and finish."
    else:
        task = (
            "Read the exact file "
            f"taskpilot-t062-nonexistent-{uuid4().hex}.md "
            "from this repository. If GitHub returns 404, report that the file was not found."
        )
    start_http, accepted = request(
        base_url, "POST", "/tasks", {"repository": REPOSITORY, "task": task}
    )
    task_id = accepted["task_id"]
    pending = None
    pre_reply_dispatch = None
    pre_reply_github_calls = None
    reply_http = None
    same_reply_id = None
    if case == "clarification":
        pending_http, pending, first_polls = poll(
            base_url, task_id, {"clarification_pending"} | TERMINAL
        )
        if pending and pending["status"] == "clarification_pending":
            original_id = pending["clarification"]["tool_call_id"]
            before_reply = logs(container_id, since)
            pre_reply_dispatch = sum(
                item.get("event") == "tool_execution"
                and item.get("status") == "started"
                for item in events_for_run(before_reply, None)
            )
            pre_reply_github_calls = len(github_requests(before_reply))
            reply_http, reply = request(
                base_url,
                "POST",
                f"/tasks/{task_id}/clarifications/{original_id}/reply",
                {"reply": '{"path":"README.md"}'},
            )
            same_reply_id = reply.get("tool_call_id") == original_id
            final_http, final, last_polls = poll(base_url, task_id, TERMINAL)
        else:
            final_http, final, last_polls = pending_http, pending, 0
        poll_count = first_polls + last_polls
    else:
        final_http, final, poll_count = poll(
            base_url, task_id, TERMINAL | {"clarification_pending"}
        )
    log_text = logs(container_id, since)
    run_id = (final or {}).get("run_id") or ""
    events = events_for_run(log_text, run_id)
    github = github_requests(log_text)
    attempts = retry_attempts(log_text)
    call_ref = None
    call_events = []
    if pending and pending.get("clarification") and run_id:
        raw_id = pending["clarification"]["tool_call_id"]
        call_ref = "sha256:" + hashlib.sha256(f"{run_id}:{raw_id}".encode()).hexdigest()
        call_events = [item for item in events if item.get("tool_call_id") == call_ref]
    validation = [
        item["status"] for item in call_events if item["event"] == "validation"
    ]
    clarification = [
        item["status"] for item in call_events if item["event"] == "clarification"
    ]
    call_dispatches = sum(
        item["event"] == "tool_execution" and item["status"] == "started"
        for item in call_events
    )
    read_http_200 = sum(
        item == {"tool": "github_read_file", "status": "success", "http_status": 200}
        for item in github
    )
    read_http_404 = sum(
        item == {"tool": "github_read_file", "status": "error", "http_status": 404}
        for item in github
    )
    not_found_call_refs = {
        item["tool_call_id"]
        for item in events
        if item["event"] == "tool_execution"
        and item["status"] == "failure"
        and item.get("tool") == "github_read_file"
    }
    not_found_attempts = sum(
        item["event"] == "tool_execution"
        and item["status"] == "started"
        and item.get("tool_call_id") in not_found_call_refs
        for item in events
    )
    not_found_retries = sum(
        item["event"] == "retry" and item.get("tool_call_id") in not_found_call_refs
        for item in events
    )
    valid_trace = trace_valid(events, run_id, container_id)
    common = (
        health_http == 200
        and health == {"status": "ok"}
        and start_http == 202
        and final_http == 200
        and final is not None
        and final["cleanup_status"] == "complete"
        and final["business_outcome"] == "not_verified"
        and valid_trace
        and final["run_id"] == run_id
    )
    if case == "normal":
        passed = (
            common
            and final["status"] == "finished"
            and final["execution_status"] == "success"
            and bool(final["result"])
            and read_http_200 >= 1
            and any(
                item["event"] == "tool_execution" and item["status"] == "success"
                for item in events
            )
        )
    elif case == "clarification":
        passed = (
            common
            and pending is not None
            and pending["status"] == "clarification_pending"
            and pending["clarification"]["missing_fields"] == ["path"]
            and pre_reply_dispatch == 0
            and pre_reply_github_calls == 0
            and reply_http == 202
            and same_reply_id is True
            and final["status"] == "finished"
            and call_dispatches == 1
            and read_http_200 == 1
            and "pending" in validation
            and "success" in validation
            and {"requested", "waited", "merged"} <= set(clarification)
            and sum(item["event"] == "llm_selection" for item in call_events) == 1
        )
    else:
        passed = (
            common
            and read_http_404 == 1
            and final["failure_observation_count"] >= 1
            and any(
                item["event"] == "observation" and item["status"] == "failure"
                for item in events
            )
            and len(not_found_call_refs) == 1
            and not_found_attempts == 1
            and not_found_retries == 0
            and sum(
                item
                == {
                    "tool": "github_read_file",
                    "number": 1,
                    "classification": "permanent",
                    "http_status": 404,
                    "status": "failure",
                }
                for item in attempts
            )
            == 1
        )
    return {
        "result": "PASS" if passed else "FAIL",
        "case": case,
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": REPOSITORY,
        "ref": "default_branch",
        "transport": "real HTTP to Docker-published localhost FastAPI port",
        "model_selection": "real provider",
        "omission_mode": (
            "controlled_path_removal_after_real_model_tool_selection"
            if case == "clarification"
            else "none"
        ),
        "health_http_status": health_http,
        "start_http_status": start_http,
        "poll_http_status": final_http,
        "poll_count": poll_count,
        "reply_http_status": reply_http,
        "reply_preserved_original_tool_call_id": same_reply_id,
        "pending_status": pending["status"] if pending else None,
        "pending_missing_fields": (
            pending["clarification"]["missing_fields"]
            if pending and pending.get("clarification")
            else None
        ),
        "pre_reply_dispatch_count": pre_reply_dispatch,
        "pre_reply_github_http_count": pre_reply_github_calls,
        "original_tool_call_ref": call_ref,
        "original_call_validation_statuses": validation,
        "original_call_clarification_statuses": clarification,
        "original_call_dispatch_count": call_dispatches if call_ref else None,
        "task_status": final["status"] if final else None,
        "execution_status": final["execution_status"] if final else None,
        "finish_source": final["finish_source"] if final else None,
        "business_outcome": final["business_outcome"] if final else None,
        "failure_observation_count": (
            final["failure_observation_count"] if final else None
        ),
        "not_found_attempts": not_found_attempts,
        "not_found_retries": not_found_retries,
        "cleanup_status": final["cleanup_status"] if final else None,
        "task_id_ref": "sha256:" + hashlib.sha256(task_id.encode()).hexdigest(),
        "run_id_ref": "sha256:" + hashlib.sha256(run_id.encode()).hexdigest(),
        "trace_schema_version": "1.1",
        "trace_valid": valid_trace,
        "trace_event_count": len(events),
        "routing_event_count": sum(item["event"] == "routing" for item in events),
        "tool_dispatch_count": sum(
            item["event"] == "tool_execution" and item["status"] == "started"
            for item in events
        ),
        "github_requests": github,
        "read_attempts": [
            item for item in attempts if item["tool"] == "github_read_file"
        ],
        "duration_seconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=["normal", "clarification", "not_found"])
    parser.add_argument("container_id")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = case_result(args.case, port=args.port, container_id=args.container_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: evidence[key]
                for key in (
                    "case",
                    "result",
                    "task_status",
                    "execution_status",
                    "business_outcome",
                    "cleanup_status",
                    "trace_valid",
                    "github_requests",
                )
            },
            ensure_ascii=False,
        )
    )
    return 0 if evidence["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
