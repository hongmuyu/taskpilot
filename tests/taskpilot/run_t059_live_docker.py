"""Real HTTP + Provider + GitHub smoke against a running T059 container.

Usage: python tests/taskpilot/run_t059_live_docker.py CONTAINER_ID
The output intentionally excludes prompts, Tool arguments, results and keys.
"""

import argparse
import hashlib
import json
import re
import subprocess
import time
import tomllib
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from app.taskpilot.execution_trace import validate_trace_events


ROOT = Path(__file__).parents[2]
OUTPUT = ROOT / "docs/evidence/t059_docker_github_smoke.json"
REPOSITORY = "FoundationAgents/OpenManus"
TASK = "Read README.md and summarize this repository's purpose with source evidence."
DIRECT_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(base_url: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base_url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with DIRECT_HTTP.open(req, timeout=10) as response:
        return response.status, json.load(response)


def docker_output(*args: str) -> str:
    return subprocess.check_output(
        ["docker", *args], text=True, stderr=subprocess.DEVNULL
    )


def docker_logs(container_id: str, since: float) -> str:
    result = subprocess.run(
        ["docker", "logs", "--since", str(since), container_id],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("container_id")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    started = time.monotonic()
    log_since = time.time() - 1
    health_status, health = request(args.base_url, "GET", "/health")
    start_status, task = request(
        args.base_url, "POST", "/tasks", {"repository": REPOSITORY, "task": TASK}
    )
    task_id = task["task_id"]
    poll_count = 0
    poll_status = None
    reply_status = None
    view = None
    while time.monotonic() - started < 240:
        poll_status, view = request(args.base_url, "GET", f"/tasks/{task_id}")
        poll_count += 1
        if view["status"] == "clarification_pending":
            clarification = view["clarification"]
            if clarification["missing_fields"] != ["path"]:
                break
            if reply_status is None:
                reply_status, _ = request(
                    args.base_url,
                    "POST",
                    f"/tasks/{task_id}/clarifications/{clarification['tool_call_id']}/reply",
                    {"reply": '{"path":"README.md"}'},
                )
        elif view["status"] not in {"queued", "running", "cancelling"}:
            break
        time.sleep(1)

    log_text = docker_logs(args.container_id, log_since)
    events = []
    github_statuses = []
    for line in log_text.splitlines():
        if "execution_trace_event " in line:
            try:
                item = json.loads(line.split("execution_trace_event ", 1)[1])
            except ValueError:
                continue
            if item.get("run_id") == (view or {}).get("run_id"):
                events.append(item)
        if "github_tool name=" in line:
            match = re.search(r"\bhttp_status=(\d+)\b", line)
            if match:
                github_statuses.append(int(match.group(1)))

    trace_valid = False
    try:
        validate_trace_events(events)
        trace_valid = bool(events)
    except ValueError:
        pass

    model_config = ROOT / "config/config.toml"
    with model_config.open("rb") as handle:
        model = tomllib.load(handle)["llm"]["model"]
    image = docker_output(
        "inspect", "--format", "{{.Image}}", args.container_id
    ).strip()
    evidence = {
        "result": "PASS"
        if (
            health_status == 200
            and health == {"status": "ok"}
            and start_status == 202
            and poll_status == 200
            and view is not None
            and view["status"] == "finished"
            and view["execution_status"] == "success"
            and view["business_outcome"] == "not_verified"
            and bool(view["result"])
            and view["cleanup_status"] == "complete"
            and trace_valid
            and 200 in github_statuses
        )
        else "FAIL",
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "image_id": image,
        "model": model,
        "provider_revision": "NOT AVAILABLE",
        "repository": REPOSITORY,
        "ref": "default_branch",
        "transport": "real HTTP to Docker-published FastAPI port",
        "health_http_status": health_status,
        "start_http_status": start_status,
        "poll_http_status": poll_status,
        "poll_count": poll_count,
        "clarification_reply_http_status": reply_status,
        "task_id_ref": "sha256:" + hashlib.sha256(task_id.encode()).hexdigest(),
        "run_id_ref": "sha256:"
        + hashlib.sha256(((view or {}).get("run_id") or "").encode()).hexdigest(),
        "task_status": (view or {}).get("status"),
        "execution_status": (view or {}).get("execution_status"),
        "finish_source": (view or {}).get("finish_source"),
        "business_outcome": (view or {}).get("business_outcome"),
        "result_present": bool((view or {}).get("result")),
        "cleanup_status": (view or {}).get("cleanup_status"),
        "trace_valid": trace_valid,
        "trace_event_count": len(events),
        "routing_event_count": sum(item["event"] == "routing" for item in events),
        "tool_dispatch_count": sum(
            item["event"] == "tool_execution" and item["status"] == "started"
            for item in events
        ),
        "github_http_statuses_from_container_log": github_statuses,
        "duration_seconds": round(time.monotonic() - started, 3),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                key: evidence[key]
                for key in (
                    "result",
                    "task_status",
                    "cleanup_status",
                    "trace_valid",
                    "trace_event_count",
                    "github_http_statuses_from_container_log",
                )
            },
            ensure_ascii=False,
        )
    )
    return 0 if evidence["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
