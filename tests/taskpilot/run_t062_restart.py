"""Verify T062 Compose stop, offline restart, cache reuse and task isolation."""

import argparse
import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


DIRECT_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
COMPOSE = ["docker", "compose", "-p", "taskpilot-t062", "-f", "compose.taskpilot.yaml"]


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


def compose(*args: str) -> int:
    result = subprocess.run(
        [*COMPOSE, *args], capture_output=True, text=True, timeout=90
    )
    return result.returncode


def image_id() -> str:
    container = subprocess.check_output(
        [*COMPOSE, "ps", "-q", "service"], text=True
    ).strip()
    return subprocess.check_output(
        ["docker", "inspect", "--format", "{{.Image}}", container], text=True
    ).strip()


def wait_health(base_url: str, timeout: float = 90) -> tuple[bool, float]:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        try:
            code, body = request(base_url, "GET", "/health")
            if code == 200 and body == {"status": "ok"}:
                return True, round(time.monotonic() - started, 2)
        except OSError:
            pass
        time.sleep(1)
    return False, round(time.monotonic() - started, 2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base_url = f"http://127.0.0.1:{args.port}"
    start_code, accepted = request(
        base_url,
        "POST",
        "/tasks",
        {
            "repository": "FoundationAgents/OpenManus",
            "task": "Read README.md and state this repository purpose with source evidence.",
        },
    )
    task_id = accepted["task_id"]
    started = time.monotonic()
    final = None
    while time.monotonic() - started < 180:
        _, final = request(base_url, "GET", f"/tasks/{task_id}")
        if final["status"] in {"finished", "failed", "unknown", "cancelled"}:
            break
        time.sleep(1)
    initial_image = image_id()
    down_code = compose("down")
    try:
        request(base_url, "GET", "/health")
        stopped = False
    except OSError:
        stopped = True
    volume_kept = (
        subprocess.run(
            ["docker", "volume", "inspect", "taskpilot-t062_model-cache"],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )
    up_code = compose("up", "-d", "--no-build")
    healthy, restart_seconds = wait_health(base_url)
    old_task_status = None
    if healthy:
        try:
            request(base_url, "GET", f"/tasks/{task_id}")
        except urllib.error.HTTPError as exc:
            old_task_status = exc.code
    final_image = image_id() if healthy else None
    passed = (
        start_code == 202
        and final is not None
        and final["status"] == "finished"
        and final["cleanup_status"] == "complete"
        and down_code == 0
        and stopped
        and volume_kept
        and up_code == 0
        and healthy
        and old_task_status == 404
        and initial_image == final_image
    )
    evidence = {
        "result": "PASS" if passed else "FAIL",
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "start_http_status": start_code,
        "pre_restart_task_status": final["status"] if final else None,
        "pre_restart_cleanup_status": final["cleanup_status"] if final else None,
        "task_id_ref": "sha256:" + hashlib.sha256(task_id.encode()).hexdigest(),
        "compose_down_exit": down_code,
        "http_unavailable_while_stopped": stopped,
        "cache_volume_retained": volume_kept,
        "compose_up_exit": up_code,
        "restart_http_healthy": healthy,
        "restart_seconds": restart_seconds,
        "old_task_http_after_restart": old_task_status,
        "image_id_unchanged": initial_image == final_image,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(
        {
            key: evidence[key]
            for key in (
                "result",
                "pre_restart_cleanup_status",
                "http_unavailable_while_stopped",
                "restart_http_healthy",
                "old_task_http_after_restart",
                "image_id_unchanged",
            )
        }
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
