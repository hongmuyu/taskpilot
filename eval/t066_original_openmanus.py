"""Run the frozen T065 holdout against unmodified OpenManus in an isolated container.

The host process owns the real provider key. A short-lived local proxy injects it;
the container receives only a disposable proxy credential. No benchmark result is
computed here. Each sample gets one fresh upstream Manus process and a raw JSONL
record, including unsupported cases.
"""

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


UPSTREAM_COMMIT = "3309bf4e416fb1c74b008f3e86494439a31bad53"
BASE_TOOLS = {"python_execute", "str_replace_editor", "ask_human", "terminate"}
SUPPORTED = {
    **{
        f"core-{number:03}": "Public GitHub evidence can be read through original PythonExecute."
        for number in range(1, 7)
    },
    "core-013": "Original Manus can connect the read-only v1 MCP fixture through its existing MCP client.",
    "core-015": "Original Manus can connect the read-only v2 MCP fixture through its existing MCP client.",
    "core-016": "Original MCP client can invoke fail_read; its isError handling is an observed baseline behavior.",
    "core-018": "Original PythonExecute can perform an unauthenticated read-only GitHub GET for the missing path.",
    "core-020": "Original PythonExecute can read the public issue and pinned source file.",
    "core-021": "Original PythonExecute can read public issue, code-search alternative, and pinned source when available.",
}
UNSUPPORTED = {
    "core-007": "No GitHub issue-detail tool or forced missing-parameter clarification boundary.",
    "core-008": "No GitHub file-read tool or forced missing-path clarification boundary.",
    "core-009": "No GitHub file-read tool to receive the malformed JSON fixture at its dispatch boundary.",
    "core-010": "No GitHub issue-detail schema validator for the invalid boolean fixture.",
    "core-011": "No T065-labelled GitHub issue/code-search tools or semantic routing candidate pool.",
    "core-012": "Original PythonExecute could reveal process environment; this unsafe request is not run.",
    "core-014": "No non-blocking forced clarification/merge boundary for a controlled missing MCP text field.",
    "core-017": "No 50 ms per-tool deadline or safe unknown-result timeout policy in original MCP client.",
    "core-019": "No original GitHub read tool or T065 controlled-first-503 transport fixture.",
}
TOKEN_SHAPES = (
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def scrub(value, known_secrets=()):
    """Redact configured secrets and common token shapes before serialization."""
    if not isinstance(value, str):
        return value
    for secret in known_secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    for pattern in TOKEN_SHAPES:
        value = pattern.sub("[REDACTED]", value)
    return value


def classify(sample):
    sample_id = sample["id"]
    if sample_id in SUPPORTED:
        return {"support": "supported", "reason": SUPPORTED[sample_id]}
    if sample_id in UNSUPPORTED:
        return {"support": "unsupported", "reason": UNSUPPORTED[sample_id]}
    raise ValueError(f"No preflight classification for {sample_id}")


def render_input(sample):
    """Use the frozen task and confirmed context, with one common safety guard."""
    context = sample["context"]
    lines = [sample["task"]]
    if context["repository"]:
        lines.append(f"Repository: {context['repository']}")
        lines.append(f"Pinned ref: {context['ref']}")
    if context["issue_number"] is not None:
        lines.append(f"Issue number: {context['issue_number']}")
    lines.append(
        "Benchmark safety constraint: use read-only operations only; do not inspect "
        "credentials or modify files. If the task cannot be completed safely, "
        "explain the limitation and finish with failure."
    )
    return "\n".join(lines)


def _message_evidence(agent, known_secrets):
    messages = []
    for message in agent.messages:
        if message.role == "user":
            continue
        entry = {"role": getattr(message.role, "value", message.role)}
        if message.content:
            content = scrub(message.content, known_secrets)
            entry["content"] = content[:12000]
            entry["content_length"] = len(content)
            entry["content_sha256"] = sha256_bytes(content.encode())
        if message.tool_call_id:
            entry["tool_call_id"] = message.tool_call_id
        if message.name:
            entry["tool_name"] = message.name
        if message.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": call.id,
                    "name": call.function.name,
                    "arguments": scrub(call.function.arguments, known_secrets)[:8000],
                    "arguments_sha256": sha256_bytes(
                        scrub(call.function.arguments, known_secrets).encode()
                    ),
                }
                for call in message.tool_calls
            ]
        messages.append(entry)
    return messages


async def _worker_run(sample, probe=False):
    from app.agent.manus import Manus
    from app.config import config
    from app.logger import logger

    logger.remove()
    known = [config.llm["default"].api_key]
    prompt = (
        "Call terminate(status=failure) now; this is a provider transport probe."
        if probe
        else render_input(sample)
    )
    record = {
        "sample_id": "transport-probe" if probe else sample["id"],
        "started_at_utc": utc_now(),
        "prompt": prompt,
        "prompt_sha256": sha256_bytes(prompt.encode()),
        "agent_class": "app.agent.manus.Manus",
        "agent_source": "/srv/taskpilot/app/agent/manus.py",
        "max_steps": 1 if probe else 6,
        "task_timeout_seconds": 90,
        "thinking_mode": "disabled_via_provider_request_only",
        "messages": [],
        "mcp_audit": [],
        "provider_usage_from_upstream_counters": None,
        "run_status": "not_started",
        "error": None,
        "cleanup_status": "not_started",
    }
    agent = None
    started = time.perf_counter()
    try:
        agent = await asyncio.wait_for(Manus.create(max_steps=record["max_steps"]), 20)
        if sample and sample["profile"].startswith("mcp_stdio_"):
            variant = sample["profile"].removeprefix("mcp_stdio_")
            await asyncio.wait_for(
                agent.connect_mcp_server(
                    "/usr/local/bin/python",
                    "fixture",
                    use_stdio=True,
                    stdio_args=["/runner/stdio_mcp_server.py"],
                    stdio_env={
                        "TASKPILOT_MCP_AUDIT_PATH": "/tmp/mcp-audit.jsonl",
                        "TASKPILOT_MCP_VARIANT": variant,
                    },
                ),
                timeout=15,
            )
            record[
                "mcp_attachment"
            ] = "original_Manus.connect_mcp_server_with_stdio_env"
        actual_tools = set(agent.available_tools.tool_map)
        if probe or sample["profile"] == "github_readonly":
            expected_tools = BASE_TOOLS
        else:
            expected_tools = BASE_TOOLS | {
                "mcp_fixture_echo_read",
                "mcp_fixture_fail_read",
                "mcp_fixture_slow_read",
                "mcp_fixture_server_pid",
                "mcp_fixture_legacy_read"
                if sample["profile"] == "mcp_stdio_v1"
                else "mcp_fixture_new_read",
            }
        record["tool_pool"] = sorted(actual_tools)
        if actual_tools != expected_tools:
            raise RuntimeError("Original Manus tool pool did not match preflight")

        original_ask_tool = agent.llm.ask_tool

        async def nonthinking_request(**kwargs):
            return await original_ask_tool(
                **kwargs, extra_body={"thinking": {"type": "disabled"}}
            )

        agent.llm.ask_tool = nonthinking_request
        try:
            result = await asyncio.wait_for(agent.run(prompt), timeout=90)
            record["run_status"] = "returned"
            cleaned = scrub(result, known)
            record["run_return"] = cleaned[:12000]
            record["run_return_sha256"] = sha256_bytes(cleaned.encode())
        except asyncio.TimeoutError:
            record["run_status"] = "timeout_unknown"
            record["error"] = {
                "type": "TimeoutError",
                "message": "Agent task budget exhausted",
            }
        except BaseException as exc:
            record["run_status"] = "exception"
            record["error"] = {
                "type": type(exc).__name__,
                "message": scrub(str(exc), known)[:1000],
            }
        record["messages"] = _message_evidence(agent, known)
        record["provider_usage_from_upstream_counters"] = {
            "input_tokens": agent.llm.total_input_tokens,
            "output_tokens": agent.llm.total_completion_tokens,
            "total_tokens": agent.llm.total_input_tokens
            + agent.llm.total_completion_tokens,
            "source": "upstream_counters_updated_from_provider_response_usage",
        }
    except BaseException as exc:
        record["run_status"] = "setup_error"
        record["error"] = {
            "type": type(exc).__name__,
            "message": scrub(str(exc), known)[:1000],
        }
    finally:
        if agent is not None:
            try:
                await asyncio.wait_for(agent.cleanup(), timeout=5)
                await asyncio.wait_for(agent.llm.client.close(), timeout=5)
                record["cleanup_status"] = "complete"
            except BaseException as exc:
                record["cleanup_status"] = "error"
                record["cleanup_error_type"] = type(exc).__name__
        audit = Path(os.environ.get("TASKPILOT_MCP_AUDIT_PATH", "/tmp/no-audit"))
        if audit.exists():
            record["mcp_audit"] = [
                json.loads(line) for line in audit.read_text().splitlines()
            ]
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        record["finished_at_utc"] = utc_now()
    return record


def worker_main(args):
    import warnings

    warnings.filterwarnings("ignore", category=DeprecationWarning)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    try:
        with open(os.devnull, "w", encoding="utf-8") as quiet:
            with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                sample = None
                if not args.probe:
                    holdout = json.loads(Path("/runner/holdout.json").read_text())
                    sample = next(
                        x for x in holdout["samples"] if x["id"] == args.sample_id
                    )
                result = asyncio.run(_worker_run(sample, probe=args.probe))
    except BaseException as exc:
        frames = traceback.extract_tb(exc.__traceback__)
        result = {
            "sample_id": "transport-probe" if args.probe else args.sample_id,
            "run_status": "worker_error",
            "error": {
                "type": type(exc).__name__,
                "message": scrub(str(exc))[:500],
                "origin": (
                    {"file": Path(frames[-1].filename).name, "line": frames[-1].lineno}
                    if frames
                    else None
                ),
            },
            "elapsed_ms": None,
            "messages": [],
            "mcp_audit": [],
        }
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
    print(json.dumps(result, ensure_ascii=False))


class ProviderProxy(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, upstream_url, api_key, proxy_key):
        super().__init__(address, ProxyHandler)
        self.upstream_url = upstream_url
        self.api_key = api_key
        self.proxy_key = proxy_key
        self.calls = []
        self.calls_lock = threading.Lock()


class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_POST(self):
        if self.path not in {"/chat/completions", "/v1/chat/completions"}:
            self.send_error(404)
            return
        if self.headers.get("Authorization") != f"Bearer {self.server.proxy_key}":
            self.send_error(401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if not 0 < length <= 2_000_000:
            self.send_error(413)
            return
        body = self.rfile.read(length)
        started = time.perf_counter()
        call = {"request_sha256": sha256_bytes(body), "started_at_utc": utc_now()}
        try:
            import httpx

            response = httpx.post(
                self.server.upstream_url,
                content=body,
                headers={
                    "Authorization": f"Bearer {self.server.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=65,
            )
            payload = response.content
            code = response.status_code
            call["http_status"] = code
            try:
                parsed = response.json()
                usage = parsed.get("usage") if isinstance(parsed, dict) else None
                call["usage"] = (
                    {
                        key: usage.get(key)
                        for key in (
                            "prompt_tokens",
                            "completion_tokens",
                            "total_tokens",
                        )
                    }
                    if isinstance(usage, dict)
                    else None
                )
                call["provider_model"] = (
                    parsed.get("model") if isinstance(parsed, dict) else None
                )
            except ValueError:
                call["usage"] = None
                call["provider_model"] = None
        except Exception as exc:
            code = 502
            payload = b'{"error":{"message":"provider proxy transport failure","type":"proxy_error"}}'
            call["http_status"] = code
            call["error_type"] = type(exc).__name__
            call["usage"] = None
        call["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        with self.server.calls_lock:
            self.server.calls.append(call)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _docker_gateway():
    return subprocess.check_output(
        [
            "docker",
            "network",
            "inspect",
            "bridge",
            "--format",
            "{{(index .IPAM.Config 0).Gateway}}",
        ],
        text=True,
    ).strip()


def _baseline_checkout_ok(path):
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=path, text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=path, text=True
    ).strip()
    if commit != UPSTREAM_COMMIT or dirty:
        raise RuntimeError("Baseline checkout is not the pinned clean upstream commit")


def _docker_run(
    sample, probe, *, upstream, image, config_path, mcp_path, runner_dir, timeout
):
    variant = "v2" if sample and sample["profile"] == "mcp_stdio_v2" else "v1"
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "bridge",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "128",
        "--memory",
        "1g",
        "--cpus",
        "2",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=64m",
        "--tmpfs",
        "/srv/taskpilot/logs:rw,nosuid,nodev,size=16m",
        "-e",
        "OPENMANUS_DISABLE_BROWSER_USE=1",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        "PYTHONPATH=/srv/taskpilot",
        "-e",
        "TIKTOKEN_CACHE_DIR=/var/cache/tiktoken",
        "-e",
        "TASKPILOT_MCP_AUDIT_PATH=/tmp/mcp-audit.jsonl",
        "-e",
        f"TASKPILOT_MCP_VARIANT={variant}",
        "-v",
        f"{upstream}:/srv/taskpilot:ro",
        "-v",
        f"{config_path}:/srv/taskpilot/config/config.toml:ro",
        "-v",
        f"{mcp_path}:/srv/taskpilot/config/mcp.json:ro",
        "-v",
        "/tmp/data-gym-cache:/var/cache/tiktoken:ro",
        "-v",
        f"{runner_dir}:/runner:ro",
        "-w",
        "/srv/taskpilot",
        "--entrypoint",
        "python",
        image,
        "/runner/t066_original_openmanus.py",
        "--worker",
    ]
    command += ["--probe"] if probe else ["--sample-id", sample["id"]]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return {
            "run_status": "container_timeout_unknown",
            "error": {"type": "TimeoutExpired"},
            "messages": [],
            "mcp_audit": [],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    try:
        result = json.loads(completed.stdout)
    except ValueError:
        result = {
            "run_status": "container_error",
            "error": {"type": "InvalidWorkerOutput"},
            "messages": [],
            "mcp_audit": [],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    result["container_exit_code"] = completed.returncode
    result["container_stderr_sha256"] = sha256_bytes(completed.stderr.encode())
    return result


def host_main(args):
    root = Path(__file__).resolve().parents[1]
    dataset = (root / "eval/datasets/core_benchmark_v1_holdout.json").resolve()
    scoring = root / "eval/datasets/core_benchmark_v1_scoring.json"
    fixture = (root / "tests/taskpilot/fixtures/stdio_mcp_server.py").resolve()
    holdout = json.loads(dataset.read_text())
    if holdout["scoring_sha256"] != sha256_bytes(scoring.read_bytes()):
        raise RuntimeError("Frozen scoring hash does not match holdout")
    if holdout["version"] != "1.0.0" or len(holdout["samples"]) != 21:
        raise RuntimeError("Frozen holdout version/count mismatch")
    upstream = Path(args.upstream).resolve()
    _baseline_checkout_ok(upstream)
    for mountpoint in (
        upstream / "config/config.toml",
        upstream / "config/mcp.json",
        upstream / "logs",
    ):
        if not mountpoint.exists():
            raise RuntimeError(
                f"Required empty baseline mountpoint is missing: {mountpoint.name}"
            )
    config = tomllib.loads(Path(args.config).read_text())
    llm = config["llm"]
    api_key = llm.get("api_key", "")
    if not api_key or api_key == "YOUR_API_KEY":
        raise RuntimeError(
            "A real provider credential is required in ignored local config"
        )
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True
    ).strip()
    gateway = _docker_gateway()
    proxy_key = secrets.token_urlsafe(24)
    proxy = ProviderProxy(
        (gateway, 0),
        llm["base_url"].rstrip("/") + "/chat/completions",
        api_key,
        proxy_key,
    )
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    records = []
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8"):
        pass
    try:
        with tempfile.TemporaryDirectory(prefix="t066-openmanus-") as directory:
            temp = Path(directory)
            proxy_config = temp / "config.toml"
            proxy_config.write_text(
                "[llm]\n"
                f'model = {json.dumps(llm["model"])}\n'
                f'base_url = {json.dumps(f"http://{gateway}:{proxy.server_port}")}\n'
                f"api_key = {json.dumps(proxy_key)}\n"
                f'max_tokens = {int(llm.get("max_tokens", 1024))}\n'
                f'temperature = {float(llm.get("temperature", 0.0))}\n'
                'api_type = "openai"\napi_version = ""\n'
                '[daytona]\ndaytona_api_key = "unused-placeholder"\n'
                "[sandbox]\nuse_sandbox = false\n"
            )
            # This file holds only a disposable proxy token; CAP_DAC_OVERRIDE is
            # removed from the container, so it must be readable after bind mount.
            proxy_config.chmod(0o644)
            empty_mcp = temp / "empty-mcp.json"
            empty_mcp.write_text('{"mcpServers":{}}\n')
            runner_dir = temp / "runner"
            runner_dir.mkdir()
            shutil.copyfile(
                Path(__file__).resolve(), runner_dir / "t066_original_openmanus.py"
            )
            shutil.copyfile(dataset, runner_dir / "holdout.json")
            shutil.copyfile(fixture, runner_dir / "stdio_mcp_server.py")
            cases = [None] if args.probe else holdout["samples"]
            if args.ids:
                requested = set(args.ids.split(","))
                cases = [
                    sample for sample in cases if sample and sample["id"] in requested
                ]
                if {sample["id"] for sample in cases} != requested:
                    raise ValueError("Unknown or missing sample ID in --ids")
            for sample in cases:
                decision = (
                    {
                        "support": "supported",
                        "reason": "Transport-only probe outside holdout.",
                    }
                    if args.probe
                    else classify(sample)
                )
                sample_id = "transport-probe" if args.probe else sample["id"]
                entry = {
                    "sample_id": sample_id,
                    "run_label": args.label,
                    "support": decision["support"],
                    "support_reason": decision["reason"],
                    "dataset_version": holdout["version"],
                    "dataset_sha256": sha256_bytes(dataset.read_bytes()),
                    "scoring_sha256": holdout["scoring_sha256"],
                    "upstream_commit": UPSTREAM_COMMIT,
                    "container_image_id": image_id,
                    "python_host": platform.python_version(),
                    "provider_model_config": {
                        "model": llm["model"],
                        "temperature": llm.get("temperature", 0.0),
                        "max_tokens": llm.get("max_tokens", 1024),
                        "thinking": "disabled",
                        "provider_endpoint_scheme": llm["base_url"].split(":", 1)[0],
                        "transport": "host_local_key_injecting_proxy; no real key in container",
                    },
                    "execution_budget": {
                        "max_steps": 1 if args.probe else 6,
                        "worker_seconds": 90,
                        "container_seconds": args.timeout,
                    },
                    "run": None,
                    "provider_requests": [],
                }
                if decision["support"] == "supported":
                    with proxy.calls_lock:
                        start_index = len(proxy.calls)
                    entry["run"] = _docker_run(
                        sample,
                        args.probe,
                        upstream=upstream,
                        image=args.image,
                        config_path=proxy_config,
                        mcp_path=empty_mcp,
                        runner_dir=runner_dir,
                        timeout=args.timeout,
                    )
                    with proxy.calls_lock:
                        entry["provider_requests"] = list(proxy.calls[start_index:])
                else:
                    entry["run"] = {
                        "run_status": "not_run_unsupported",
                        "tool_pool": "original_default_not_dispatched",
                    }
                records.append(entry)
                with output.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
                run_status = entry["run"]["run_status"]
                print(
                    f"{sample_id} support={decision['support']} run={run_status} provider_requests={len(entry['provider_requests'])}",
                    flush=True,
                )
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=3)
    print(f"records={len(records)} output={output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--sample-id")
    parser.add_argument(
        "--ids", help="Comma-separated frozen sample IDs for an explicit retest"
    )
    parser.add_argument("--label", default="initial")
    parser.add_argument("--upstream")
    parser.add_argument("--config", default="config/config.toml")
    parser.add_argument("--image", default="taskpilot-service:pr08c-nocache")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.worker:
        worker_main(args)
    else:
        if not args.upstream or not args.output:
            parser.error("--upstream and --output are required on host")
        host_main(args)


if __name__ == "__main__":
    main()
