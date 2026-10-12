"""T067 full-tool TaskPilot baseline over the frozen Core holdout.

This runner uses the actual Agent/Tool runtime. Controlled cases are explicitly
injected at the execution boundary, as specified by T065; natural cases use
the real provider. No routing index or embedding backend is constructed.
"""

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import logging
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from eval.t066_original_openmanus import scrub


ROOT = Path(__file__).resolve().parents[1]
HOLDOUT = ROOT / "eval/datasets/core_benchmark_v1_holdout.json"
SCORING = ROOT / "eval/datasets/core_benchmark_v1_scoring.json"
SOURCES = ROOT / "eval/datasets/core_benchmark_v1_sources.json"
MAX_STEPS = 8
RUN_TIMEOUT_SECONDS = 180
REQUEST_TIMEOUT_SECONDS = 45
CLEANUP_TIMEOUT_SECONDS = 5


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_sha256(value) -> str:
    return _sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    )


def load_frozen_holdout() -> dict:
    data = json.loads(HOLDOUT.read_text())
    if data["version"] != "1.0.0" or len(data["samples"]) != 21:
        raise ValueError("Unexpected frozen holdout version or size")
    if data["scoring_sha256"] != _sha256(SCORING.read_bytes()):
        raise ValueError("Frozen scoring contract changed")
    if data["source_catalog_sha256"] != _sha256(SOURCES.read_bytes()):
        raise ValueError("Frozen source catalog changed")
    return data


class FullToolAudit:
    """Check the complete, current ToolCollection schemas at each LLM boundary."""

    def __init__(self, agent):
        self.agent = agent
        self.original = None
        self.calls: list[dict] = []

    def install(self) -> None:
        if (
            self.agent.routing_top_k is not None
            or self.agent.routing_retriever is not None
        ):
            raise ValueError(
                "Full-tools baseline must bypass semantic candidate filtering"
            )
        self.original = self.agent.llm.ask_tool

        async def audited(*args, **kwargs):
            expected = self.agent.available_tools.to_params()
            sent = kwargs.get("tools")
            matched = sent == expected and self.agent.routing_top_k is None
            self.calls.append(
                {
                    "step": self.agent.current_step,
                    "expected_names": [item["function"]["name"] for item in expected],
                    "sent_names": (
                        [item["function"]["name"] for item in sent]
                        if isinstance(sent, list)
                        else None
                    ),
                    "expected_schema_sha256": _json_sha256(expected),
                    "sent_schema_sha256": _json_sha256(sent),
                    "full_pool_verified": matched,
                }
            )
            if not matched:
                raise ValueError("LLM did not receive full tool schemas")
            try:
                return await self.original(
                    *args,
                    **{
                        **kwargs,
                        "timeout": REQUEST_TIMEOUT_SECONDS,
                        "extra_body": {"thinking": {"type": "disabled"}},
                    },
                )
            except Exception as exc:
                error = (
                    exc.last_attempt.exception()
                    if hasattr(exc, "last_attempt")
                    else exc
                )
                chain = []
                for _ in range(5):
                    if error is None:
                        break
                    chain.append(
                        {
                            "type": type(error).__name__,
                            "errno": getattr(error, "errno", None),
                            "status_code": getattr(error, "status_code", None),
                        }
                    )
                    error = error.__cause__ or error.__context__
                self.calls[-1]["error_chain"] = chain
                raise

        self.agent.llm.ask_tool = audited

    def restore(self) -> None:
        if self.original is not None:
            self.agent.llm.ask_tool = self.original


class First503Transport(httpx.AsyncBaseTransport):
    """Record actual HTTP attempts; optionally replace one exact GET with 503."""

    def __init__(self, downstream: httpx.AsyncBaseTransport, path: str | None):
        self.downstream = downstream
        self.path = path
        self.injected = False
        self.events: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        started = time.perf_counter()
        if (
            self.path is not None
            and not self.injected
            and request.method == "GET"
            and request.url.path == self.path
        ):
            self.injected = True
            response = httpx.Response(
                503,
                request=request,
                json={"message": "controlled first response"},
                headers={"Retry-After": "0"},
            )
            origin = "controlled_first_503"
        else:
            response = await self.downstream.handle_async_request(request)
            origin = "downstream_transport"
        self.events.append(
            {
                "method": request.method,
                "path": request.url.path,
                "query_sha256": _sha256(request.url.query),
                "http_status": response.status_code,
                "origin": origin,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        )
        return response

    async def aclose(self) -> None:
        await self.downstream.aclose()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _known_secrets() -> list[str]:
    from app.config import config

    return [config.llm["default"].api_key, os.getenv("GITHUB_TOKEN", "")]


def new_case_llm(sample_id: str):
    """Bypass OpenManus's config-name singleton for each benchmark task."""
    from app.config import config
    from app.llm import LLM

    settings = config.llm["default"]
    name = f"t067_{sample_id.replace('-', '_')}_{uuid4().hex}"
    return LLM(config_name=name, llm_config={"default": settings, name: settings})


def _safe_messages(agent, secrets: list[str]) -> list[dict]:
    rows = []
    for message in agent.messages:
        if message.role == "user":
            continue
        item = {"role": getattr(message.role, "value", message.role)}
        if message.content:
            cleaned = scrub(message.content, secrets)
            item.update(
                content=cleaned[:8000],
                content_chars=len(cleaned),
                content_sha256=_sha256(cleaned.encode()),
            )
        if message.tool_call_id:
            item["tool_call_id"] = message.tool_call_id
        if message.name:
            item["tool_name"] = message.name
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.id,
                    "name": call.function.name,
                    "arguments": scrub(call.function.arguments, secrets)[:4000],
                }
                for call in message.tool_calls
            ]
        rows.append(item)
    return rows


def _mcp_audit(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def _schema_fingerprint(schema) -> str:
    return _json_sha256(schema)


def _verify_profile(agent, sample: dict, holdout: dict, catalog: dict) -> dict:
    profile = next(
        item for item in holdout["profiles"] if item["id"] == sample["profile"]
    )
    tools = list(agent.available_tools)
    expected = set(profile["tools"] + profile["control_tools"])
    actual = {tool.name for tool in tools}
    if actual != expected or len(tools) != len(expected):
        raise ValueError(f"Tool profile mismatch for {sample['id']}")
    if profile["control_tools"] != ["terminate"]:
        raise ValueError("Unexpected control tool profile")
    hashes = {tool.name: _schema_fingerprint(tool.parameters) for tool in tools}
    if sample["profile"] == "github_readonly":
        frozen = {
            item["name"]: item["schema_sha256"] for item in catalog["local_tools"]
        }
        if {name: hashes[name] for name in frozen} != frozen:
            raise ValueError("Local Tool schemas changed since T065")
    else:
        variant_name = sample["profile"].removeprefix("mcp_stdio_")
        variant = next(
            item
            for item in catalog["mcp_server"]["variants"]
            if item["name"] == variant_name
        )
        for frozen_tool in variant["tools"]:
            tool = agent.available_tools.get_tool(frozen_tool["public_name"])
            if (
                tool.original_name != frozen_tool["original_name"]
                or tool.parameters != frozen_tool["input_schema"]
            ):
                raise ValueError("MCP Tool discovery differs from T065")
    return {
        "business_tools": profile["tools"],
        "control_tools": profile["control_tools"],
        "all_tools": [tool.name for tool in tools],
        "schema_sha256": hashes,
    }


async def _source_check(catalog: dict) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    result = {"checked_at_utc": _utc_now(), "issues": [], "pinned_files": []}
    async with httpx.AsyncClient(
        base_url="https://api.github.com", headers=headers, timeout=20
    ) as client:
        for repo in catalog["github_repositories"]:
            slug = repo["repository"]
            try:
                tree_response = await client.get(
                    f"/repos/{slug}/git/trees/{repo['ref']}", params={"recursive": 1}
                )
                tree_response.raise_for_status()
                tree = {
                    item["path"]: item["sha"]
                    for item in tree_response.json().get("tree", [])
                }
                for item in repo["files"]:
                    result["pinned_files"].append(
                        {
                            "repository": slug,
                            "path": item["path"],
                            "http_status": tree_response.status_code,
                            "matched": tree.get(item["path"]) == item["blob_sha"],
                        }
                    )
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                for item in repo["files"]:
                    result["pinned_files"].append(
                        {
                            "repository": slug,
                            "path": item["path"],
                            "matched": None,
                            "error_type": type(exc).__name__,
                        }
                    )
            for item in repo["issues"]:
                try:
                    response = await client.get(
                        f"/repos/{slug}/issues/{item['number']}"
                    )
                    response.raise_for_status()
                    live = response.json()
                    matched = (
                        live["updated_at"] == item["updated_at"]
                        and _sha256((live.get("body") or "").encode())
                        == item["body_sha256"]
                    )
                    result["issues"].append(
                        {
                            "repository": slug,
                            "number": item["number"],
                            "http_status": response.status_code,
                            "matched": matched,
                        }
                    )
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    result["issues"].append(
                        {
                            "repository": slug,
                            "number": item["number"],
                            "matched": None,
                            "error_type": type(exc).__name__,
                        }
                    )
    return result


async def _build_agent(sample: dict, llm, scratch: Path):
    from app.agent.repository_investigation import RepositoryInvestigationAgent
    from app.agent.toolcall import ToolCallAgent
    from app.taskpilot.github_tools import GitHubClient, RepositoryContext
    from app.tool import Terminate, ToolCollection
    from app.tool.mcp import MCPClients

    transport = None
    mcp_clients = None
    if sample["profile"] == "github_readonly":
        context = sample["context"]
        selected = RepositoryContext.parse(
            context["repository"], ref=context["ref"], source="user_input"
        )
        fault_path = (
            f"/repos/{context['repository']}/contents/README.md"
            if sample["id"] == "core-019"
            else None
        )
        proxy = os.getenv("https_proxy") or os.getenv("HTTPS_PROXY")
        transport = First503Transport(httpx.AsyncHTTPTransport(proxy=proxy), fault_path)
        http_client = httpx.AsyncClient(
            base_url="https://api.github.com", transport=transport, timeout=20
        )
        github = GitHubClient(token=os.getenv("GITHUB_TOKEN"), http_client=http_client)
        agent = RepositoryInvestigationAgent.create(
            selected, client=github, llm=llm, max_steps=MAX_STEPS
        )
    else:
        variant = sample["profile"].removeprefix("mcp_stdio_")
        mcp_clients = MCPClients(connect_timeout_seconds=5, cleanup_timeout_seconds=2)
        await mcp_clients.connect_stdio(
            sys.executable,
            [str(ROOT / "tests/taskpilot/fixtures/stdio_mcp_server.py")],
            server_id="fixture",
            env={
                "TASKPILOT_MCP_AUDIT_PATH": str(scratch / "mcp_audit.jsonl"),
                "TASKPILOT_MCP_VARIANT": variant,
            },
        )
        tools = ToolCollection(*mcp_clients.tools, Terminate())
        agent = ToolCallAgent(
            name="TaskPilotMCPFullToolsBaseline",
            llm=llm,
            available_tools=tools,
            max_steps=MAX_STEPS,
            system_prompt=(
                "You are TaskPilot using a read-only MCP tool pool. Use only the "
                "available tools. Do not inspect credentials, perform writes, or "
                "invent missing parameters. Base your answer on actual tool results "
                "and call terminate when finished."
            ),
        )
    agent.routing_top_k = None
    agent.routing_retriever = None
    agent.interactive_clarification = False
    agent.run_timeout_seconds = RUN_TIMEOUT_SECONDS
    agent.cleanup_timeout_seconds = CLEANUP_TIMEOUT_SECONDS
    if sample["id"] == "core-017":
        agent.available_tools.get_tool("mcp_fixture_slow_read").timeout_seconds = 0.05
    return agent, transport, mcp_clients


async def _run_controlled(agent, sample: dict, transport, scratch: Path) -> dict:
    from app.schema import Function, Message, ToolCall
    from app.taskpilot.execution_trace import ExecutionTrace

    setup = sample["setup"]
    call = ToolCall(
        id=f"t067-{sample['id']}-controlled",
        function=Function(name=setup["tool"], arguments=setup["arguments_json"]),
    )
    agent.execution_trace = ExecutionTrace()
    agent.current_step = 1
    agent.memory.add_message(Message.user_message(sample["task"]))
    # Explicitly labelled harness injection, not a claimed model selection.
    agent.memory.add_message(Message.from_tool_calls(content="", tool_calls=[call]))
    agent.tool_calls = [call]
    agent._trace_event(
        "task",
        "started",
        task_sha256=_sha256(sample["task"].encode()),
        task_chars=len(sample["task"]),
    )
    agent._trace_event("step", "started")
    loop = asyncio.get_running_loop()
    agent._run_deadline = loop.time() + RUN_TIMEOUT_SECONDS
    details = {
        "mode": setup["mode"],
        "injected_tool_call_id": call.id,
        "injected_tool": setup["tool"],
        "injected_arguments_json": setup["arguments_json"],
        "reply_source": "frozen_holdout_controlled_reply"
        if "clarification_reply" in setup
        else None,
        "dispatch_before_reply": None,
        "pending_missing_fields": [],
    }
    if "clarification_reply" in setup:

        async def controlled_reply(call_id: str) -> str:
            pending = agent.pending_tool_calls[call_id]
            details["pending_missing_fields"] = pending.missing_fields.copy()
            details["dispatch_before_reply"] = (
                len(transport.events)
                if transport is not None
                else len(_mcp_audit(scratch / "mcp_audit.jsonl"))
            )
            return await agent.submit_clarification_reply(
                call_id,
                json.dumps(setup["clarification_reply"], separators=(",", ":")),
                record_tool_message=False,
            )

        agent.async_clarification_handler = controlled_reply
    started = time.perf_counter()
    try:
        observation = await asyncio.wait_for(agent.act(), timeout=RUN_TIMEOUT_SECONDS)
        details["observation"] = observation
        details["run_status"] = "controlled_boundary_returned"
    except BaseException as exc:
        details["run_status"] = "controlled_boundary_error"
        details["error_type"] = type(exc).__name__
    finally:
        agent._run_deadline = None
        agent._trace_event("step", agent._trace_step_status)
        agent._trace_event(
            "finish",
            "unknown",
            finish_source="runtime_exit",
            business_outcome="not_verified",
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    details["parameter_sources"] = agent.tool_call_sources.get(call.id, {}).copy()
    details["tool_message_call_ids"] = [
        message.tool_call_id
        for message in agent.messages
        if message.name == setup["tool"] and message.tool_call_id
    ]
    return details


async def _run_natural(agent, sample: dict, audit: FullToolAudit) -> dict:
    audit.install()
    try:
        result = await asyncio.wait_for(
            agent.run(sample["task"]),
            timeout=RUN_TIMEOUT_SECONDS + CLEANUP_TIMEOUT_SECONDS + 5,
        )
        cleaned = scrub(result, _known_secrets())
        return {
            "run_status": "returned",
            "run_return": cleaned[:8000],
            "run_return_chars": len(cleaned),
            "max_steps_reached": "Reached max steps" in result,
        }
    except BaseException as exc:
        return {
            "run_status": "cancelled"
            if isinstance(exc, asyncio.CancelledError)
            else "error",
            "error_type": type(exc).__name__,
            "error": scrub(str(exc), _known_secrets())[:1000],
        }
    finally:
        audit.restore()


async def run_case(sample: dict, holdout: dict, catalog: dict) -> dict:
    from app.logger import logger
    from app.taskpilot.execution_trace import (
        summarize_trace_costs,
        validate_trace_events,
    )

    started = time.perf_counter()
    row = {
        "record_type": "case",
        "sample_id": sample["id"],
        "profile": sample["profile"],
        "category": sample["category"],
        "setup_mode": sample["setup"]["mode"],
        "started_at_utc": _utc_now(),
        "task_sha256": _sha256(sample["task"].encode()),
        "context": sample["context"],
        "routing_top_k": None,
        "routing_events": [],
        "schema_calls": [],
        "mcp_audit": [],
        "github_http": [],
        "trace": [],
        "messages": [],
        "support": "supported",
        "run": {"run_status": "not_started"},
        "cleanup_status": "not_started",
    }
    llm = None
    agent = None
    transport = None
    mcp_clients = None
    routing_events = []

    def capture_routing(message):
        raw = message.record["message"]
        if raw.startswith("tool_routing_event "):
            routing_events.append(json.loads(raw.removeprefix("tool_routing_event ")))

    sink_id = logger.add(capture_routing, level="INFO")
    with tempfile.TemporaryDirectory(prefix=f"t067-{sample['id']}-") as directory:
        scratch = Path(directory)
        try:
            llm = new_case_llm(sample["id"])
            agent, transport, mcp_clients = await _build_agent(sample, llm, scratch)
            row["tool_pool"] = _verify_profile(agent, sample, holdout, catalog)
            if sample["setup"]["mode"] == "natural_task":
                audit = FullToolAudit(agent)
                row["run"] = await _run_natural(agent, sample, audit)
                row["schema_calls"] = audit.calls
            else:
                row["run"] = await _run_controlled(agent, sample, transport, scratch)
        except BaseException as exc:
            row["run"] = {
                "run_status": "setup_or_harness_error",
                "error_type": type(exc).__name__,
                "error": scrub(str(exc), _known_secrets())[:1000],
            }
        finally:
            if agent is not None:
                row["messages"] = _safe_messages(agent, _known_secrets())
                if agent.execution_trace is not None:
                    row["trace"] = agent.execution_trace.events.copy()
                    try:
                        validate_trace_events(row["trace"])
                        row["trace_validation"] = "valid"
                        row["trace_costs"] = summarize_trace_costs(row["trace"])
                    except ValueError as exc:
                        row["trace_validation"] = type(exc).__name__
                try:
                    await asyncio.wait_for(
                        agent.cleanup(), timeout=CLEANUP_TIMEOUT_SECONDS
                    )
                except BaseException as exc:
                    row["cleanup_error_type"] = type(exc).__name__
            if mcp_clients is not None:
                try:
                    await asyncio.wait_for(mcp_clients.disconnect(), timeout=5)
                except BaseException as exc:
                    row["cleanup_error_type"] = type(exc).__name__
                row["mcp_audit"] = _mcp_audit(scratch / "mcp_audit.jsonl")
            if transport is not None:
                row["github_http"] = transport.events.copy()
            if llm is not None:
                try:
                    await asyncio.wait_for(llm.client.close(), timeout=5)
                except BaseException as exc:
                    row["cleanup_error_type"] = type(exc).__name__
            github_closed = (
                agent is None
                or sample["profile"] != "github_readonly"
                or agent.github_client.http_client.is_closed
            )
            mcp_closed = mcp_clients is None or not mcp_clients.sessions
            row["cleanup_status"] = (
                "complete"
                if github_closed and mcp_closed and "cleanup_error_type" not in row
                else "incomplete"
            )
        logger.remove(sink_id)
    row["routing_events"] = routing_events
    row["embedding_events"] = sum(item["event"] == "embedding" for item in row["trace"])
    row["actual_llm_tool_calls"] = len(row["schema_calls"])
    row["full_schema_exposure"] = (
        all(call["full_pool_verified"] for call in row["schema_calls"])
        and not row["routing_events"]
        and row["embedding_events"] == 0
        if row["schema_calls"]
        else "NO_LLM_TOOL_CALL"
    )
    row["provider_usage"] = (
        row.get("trace_costs", {}).get("tokens", "NOT AVAILABLE")
        if row["schema_calls"]
        else "NO_LLM_CALL"
    )
    row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    row["finished_at_utc"] = _utc_now()
    return row


async def run_all(output: Path, ids: set[str] | None = None) -> None:
    from app.config import config
    from app.logger import logger

    logger.remove()
    logging.disable(logging.CRITICAL)
    holdout = load_frozen_holdout()
    catalog = json.loads(SOURCES.read_text())
    fixture_path = ROOT / catalog["mcp_server"]["source_path"]
    if _sha256(fixture_path.read_bytes()) != catalog["mcp_server"]["source_sha256"]:
        raise ValueError("MCP fixture source changed since T065")
    configured = config.llm["default"]
    if not configured.api_key or configured.api_key == "YOUR_API_KEY":
        raise ValueError("Real provider credential unavailable")
    selected = [
        sample for sample in holdout["samples"] if ids is None or sample["id"] in ids
    ]
    if ids is not None and {item["id"] for item in selected} != ids:
        raise ValueError("Unknown sample ID")
    source_check = await _source_check(catalog)
    manifest = {
        "record_type": "manifest",
        "baseline": "T067 TaskPilot full tools; semantic candidate filtering disabled",
        "dataset_version": holdout["version"],
        "dataset_sha256": _sha256(HOLDOUT.read_bytes()),
        "scoring_sha256": holdout["scoring_sha256"],
        "source_catalog_sha256": holdout["source_catalog_sha256"],
        "taskpilot_head": _git("rev-parse", "HEAD"),
        "product_app_tree": _git("rev-parse", "HEAD:app"),
        "runner_sha256": _sha256(Path(__file__).read_bytes()),
        "started_at_utc": _utc_now(),
        "environment": {
            "python": platform.python_version(),
            "mcp_sdk": importlib.metadata.version("mcp"),
            "httpx": importlib.metadata.version("httpx"),
            "platform": platform.platform(),
            "github_token_present": bool(os.getenv("GITHUB_TOKEN")),
            "https_proxy_configured": bool(
                os.getenv("https_proxy") or os.getenv("HTTPS_PROXY")
            ),
        },
        "model_config": {
            "model": configured.model,
            "temperature": configured.temperature,
            "max_tokens": configured.max_tokens,
            "endpoint_host": urlsplit(configured.base_url).hostname,
            "thinking": "disabled",
            "provider_revision": "NOT AVAILABLE",
        },
        "fixed_execution": {
            "max_steps": MAX_STEPS,
            "agent_timeout_seconds": RUN_TIMEOUT_SECONDS,
            "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "cleanup_timeout_seconds": CLEANUP_TIMEOUT_SECONDS,
            "github_http_timeout_seconds": 20,
            "mcp_connect_timeout_seconds": 5,
            "mcp_cleanup_timeout_seconds": 2,
            "normalization": "frozen_scoring_sha256",
            "control_tools": ["terminate"],
            "full_tool_filter": "routing_top_k=None; routing_retriever=None",
            "controlled_reply": "frozen_dataset_json; not natural model omission",
        },
        "profiles": holdout["profiles"],
        "mcp_fixture_sha256": catalog["mcp_server"]["source_sha256"],
        "source_check": source_check,
        "selected_sample_count": len(selected),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False) + "\n")
        stream.flush()
        for sample in selected:
            row = await run_case(sample, holdout, catalog)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            print(
                f"{sample['id']} run={row['run']['run_status']} "
                f"llm={row['actual_llm_tool_calls']} http={len(row['github_http'])} "
                f"mcp={len(row['mcp_audit'])} cleanup={row['cleanup_status']}",
                flush=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--ids", help="Explicit comma-separated sample subset for diagnosis"
    )
    args = parser.parse_args()
    ids = set(args.ids.split(",")) if args.ids else None
    asyncio.run(run_all(args.output, ids))


if __name__ == "__main__":
    main()
