"""T068 real K=3 comparison run over the frozen Core holdout.

Reuses the T067 Agent/Tool/HTTP/MCP execution harness and changes only the
semantic schema selection boundary. Controlled cases remain explicitly labelled.
"""

import argparse
import asyncio
import importlib.metadata
import json
import logging
import os
import platform
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from eval import t067_full_tool_baseline as full
from eval.t066_original_openmanus import scrub


T067_RAW = full.ROOT / "eval/results/t067_full_tools_v1.jsonl"
K_BUSINESS = 3


def compare_fixed_contract(prior: dict, current: dict) -> None:
    """Fail before live execution if anything except semantic filtering changes."""
    for field in (
        "dataset_sha256",
        "scoring_sha256",
        "source_catalog_sha256",
        "product_app_tree",
        "profiles",
        "model_config",
    ):
        if prior[field] != current[field]:
            raise ValueError(f"T067/T068 {field} differs")
    prior_execution = {
        key: value
        for key, value in prior["fixed_execution"].items()
        if key != "full_tool_filter"
    }
    current_execution = {
        key: value
        for key, value in current["fixed_execution"].items()
        if key != "full_tool_filter"
    }
    if prior_execution != current_execution:
        raise ValueError("T067/T068 fixed_execution differs")
    if (
        prior["fixed_execution"]["full_tool_filter"]
        != ("routing_top_k=None; routing_retriever=None")
        or current["fixed_execution"]["full_tool_filter"] != "routing_top_k=3"
    ):
        raise ValueError("T067/T068 routing filter contract differs")


class TopKAudit:
    """Check every LLM request against current schemas and K=3 controls."""

    def __init__(self, agent):
        self.agent = agent
        self.original = None
        self.calls: list[dict] = []

    def install(self) -> None:
        if (
            self.agent.routing_top_k != K_BUSINESS
            or self.agent.routing_retriever is None
        ):
            raise ValueError("K=3 retriever not configured")
        self.original = self.agent.llm.ask_tool

        async def audited(*args, **kwargs):
            pool = self.agent.available_tools.to_params()
            by_name = {item["function"]["name"]: item for item in pool}
            sent = kwargs.get("tools")
            names = (
                [item["function"]["name"] for item in sent]
                if isinstance(sent, list)
                else []
            )
            business = [name for name in names if name != "terminate"]
            controls = [name for name in names if name == "terminate"]
            verified = (
                isinstance(sent, list)
                and len(names) == len(set(names))
                and all(by_name.get(name) == item for name, item in zip(names, sent))
                and len(business) <= K_BUSINESS
                and controls == ["terminate"]
            )
            self.calls.append(
                {
                    "step": self.agent.current_step,
                    "sent_names": names,
                    "sent_labels": [
                        self.agent._routing_tool_label(name) for name in names
                    ]
                    if verified
                    else [],
                    "sent_schema_sha256": full._json_sha256(sent),
                    "full_pool_names": list(by_name),
                    "full_pool_schema_sha256": full._json_sha256(pool),
                    "business_names": business,
                    "control_names": controls,
                    "top_k_verified": verified,
                }
            )
            if not verified:
                raise ValueError("Invalid K=3 schema boundary")
            try:
                return await self.original(
                    *args,
                    **{
                        **kwargs,
                        "timeout": full.REQUEST_TIMEOUT_SECONDS,
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


def _routing_audit(calls: list[dict], events: list[dict]) -> dict:
    matched = 0
    for call in calls:
        event = next(
            (
                item
                for item in events
                if item["step"] == call["step"]
                and item["selection_status"] != "not_called"
            ),
            None,
        )
        if event is None:
            continue
        exposed = [item["name"] for item in event["exposed_tools"]]
        if exposed != call["sent_labels"]:
            continue
        if not all(
            isinstance(item["score"], (int, float)) for item in event["candidates"]
        ):
            continue
        matched += 1
    return {
        "llm_calls": len(calls),
        "matching_routing_events": matched,
        "all_calls_correlated": matched == len(calls),
        "not_called_events": sum(
            item["selection_status"] == "not_called" for item in events
        ),
    }


async def run_case(sample: dict, holdout: dict, catalog: dict, retriever) -> dict:
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
        "started_at_utc": full._utc_now(),
        "task_sha256": full._sha256(sample["task"].encode()),
        "context": sample["context"],
        "routing_top_k": K_BUSINESS,
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
    llm = agent = transport = mcp_clients = None
    routing_events = []

    def capture_routing(message):
        raw = message.record["message"]
        if raw.startswith("tool_routing_event "):
            routing_events.append(json.loads(raw.removeprefix("tool_routing_event ")))

    sink_id = logger.add(capture_routing, level="INFO")
    with tempfile.TemporaryDirectory(prefix=f"t068-{sample['id']}-") as directory:
        scratch = Path(directory)
        try:
            llm = full.new_case_llm(sample["id"])
            agent, transport, mcp_clients = await full._build_agent(
                sample, llm, scratch
            )
            row["tool_pool"] = full._verify_profile(agent, sample, holdout, catalog)
            agent.routing_top_k = K_BUSINESS
            agent.routing_retriever = retriever
            if sample["setup"]["mode"] == "natural_task":
                audit = TopKAudit(agent)
                row["run"] = await full._run_natural(agent, sample, audit)
                row["schema_calls"] = audit.calls
            else:
                row["run"] = await full._run_controlled(
                    agent, sample, transport, scratch
                )
        except BaseException as exc:
            row["run"] = {
                "run_status": "setup_or_harness_error",
                "error_type": type(exc).__name__,
                "error": scrub(str(exc), full._known_secrets())[:1000],
            }
        finally:
            if agent is not None:
                row["messages"] = full._safe_messages(agent, full._known_secrets())
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
                        agent.cleanup(), timeout=full.CLEANUP_TIMEOUT_SECONDS
                    )
                except BaseException as exc:
                    row["cleanup_error_type"] = type(exc).__name__
            if mcp_clients is not None:
                try:
                    await asyncio.wait_for(mcp_clients.disconnect(), timeout=5)
                except BaseException as exc:
                    row["cleanup_error_type"] = type(exc).__name__
                row["mcp_audit"] = full._mcp_audit(scratch / "mcp_audit.jsonl")
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
    row["routing_audit"] = _routing_audit(row["schema_calls"], routing_events)
    row["embedding_attempts"] = [
        {
            "status": item["status"],
            "phase": item.get("embedding_phase"),
            "duration_ms": item.get("duration_ms"),
        }
        for item in row["trace"]
        if item["event"] == "embedding"
    ]
    row["embedding_events"] = len(row["embedding_attempts"])
    row["index_versions"] = [
        item["index_version"]
        for item in routing_events
        if item["index_version"] is not None
    ]
    row["actual_llm_tool_calls"] = len(row["schema_calls"])
    row["top_k_schema_exposure"] = (
        all(call["top_k_verified"] for call in row["schema_calls"])
        and row["routing_audit"]["all_calls_correlated"]
        if row["schema_calls"]
        else "NO_LLM_TOOL_CALL"
    )
    row["provider_usage"] = (
        row.get("trace_costs", {}).get("tokens", "NOT AVAILABLE")
        if row["schema_calls"]
        else "NO_LLM_CALL"
    )
    row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    row["finished_at_utc"] = full._utc_now()
    return row


async def run_all(output: Path, ids: set[str] | None = None) -> None:
    from app.config import config
    from app.logger import logger
    from app.taskpilot.semantic_tool_retrieval import SemanticToolRetriever
    from app.taskpilot.tool_embedding_index import (
        DOCUMENT_VERSION,
        InMemoryToolIndex,
        LocalTransformerEmbeddingBackend,
    )

    logger.remove()
    logging.disable(logging.CRITICAL)
    holdout = full.load_frozen_holdout()
    catalog = json.loads(full.SOURCES.read_text())
    fixture_path = full.ROOT / catalog["mcp_server"]["source_path"]
    if (
        full._sha256(fixture_path.read_bytes())
        != catalog["mcp_server"]["source_sha256"]
    ):
        raise ValueError("MCP fixture changed since T065")
    configured = config.llm["default"]
    if not configured.api_key or configured.api_key == "YOUR_API_KEY":
        raise ValueError("Real provider credential unavailable")
    prior = json.loads(T067_RAW.open().readline())
    if prior["record_type"] != "manifest" or prior["selected_sample_count"] != 21:
        raise ValueError("T067 full-tools manifest incomplete")
    selected = [
        sample for sample in holdout["samples"] if ids is None or sample["id"] in ids
    ]
    if ids is not None and {item["id"] for item in selected} != ids:
        raise ValueError("Unknown sample ID")
    source_check = await full._source_check(catalog)
    if not all(
        item["matched"] is True
        for item in source_check["issues"] + source_check["pinned_files"]
    ):
        raise ValueError("Frozen GitHub source snapshot changed or unavailable")
    backend = LocalTransformerEmbeddingBackend()
    retriever = SemanticToolRetriever(InMemoryToolIndex(backend))
    manifest = {
        "record_type": "manifest",
        "baseline": "T068 TaskPilot K=3 semantic Top-K",
        "dataset_version": holdout["version"],
        "dataset_sha256": full._sha256(full.HOLDOUT.read_bytes()),
        "scoring_sha256": holdout["scoring_sha256"],
        "source_catalog_sha256": holdout["source_catalog_sha256"],
        "taskpilot_head": full._git("rev-parse", "HEAD"),
        "product_app_tree": full._git("rev-parse", "HEAD:app"),
        "runner_sha256": full._sha256(Path(__file__).read_bytes()),
        "t067_manifest_sha256": full._sha256(T067_RAW.open("rb").readline()),
        "started_at_utc": full._utc_now(),
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
        "embedding": {
            "model_id": backend.model_id,
            "model_revision": backend.revision,
            "vector_version": f"{DOCUMENT_VERSION}/{backend.vector_version}",
            "index_lifecycle": "one in-memory index shared sequentially across 21 isolated Agents",
        },
        "fixed_execution": {
            **prior["fixed_execution"],
            "full_tool_filter": "routing_top_k=3",
        },
        "profiles": holdout["profiles"],
        "mcp_fixture_sha256": catalog["mcp_server"]["source_sha256"],
        "source_check": source_check,
        "selected_sample_count": len(selected),
    }
    compare_fixed_contract(prior, manifest)
    if manifest["environment"] != prior["environment"]:
        raise ValueError("T067/T068 environment differs")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False) + "\n")
        stream.flush()
        for sample in selected:
            row = await run_case(sample, holdout, catalog, retriever)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            print(
                f"{sample['id']} run={row['run']['run_status']} "
                f"llm={row['actual_llm_tool_calls']} routing={len(row['routing_events'])} "
                f"http={len(row['github_http'])} mcp={len(row['mcp_audit'])} "
                f"cleanup={row['cleanup_status']}",
                flush=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids", help="Explicit comma-separated diagnostic subset")
    args = parser.parse_args()
    ids = set(args.ids.split(",")) if args.ids else None
    asyncio.run(run_all(args.output, ids))


if __name__ == "__main__":
    main()
