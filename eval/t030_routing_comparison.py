"""Paired K=3/full selection comparison on the v1.1.0 evaluation split."""

import argparse
import asyncio
import hashlib
import json
import re
import statistics
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft7Validator

from app.agent.repository_investigation import RepositoryInvestigationAgent
from app.llm import LLM
from app.logger import logger
from app.schema import Function, Message, Role, ToolCall
from app.taskpilot.github_tools import GitHubClient, RepositoryContext
from app.taskpilot.semantic_tool_retrieval import SemanticToolRetriever
from app.taskpilot.tool_embedding_index import (
    InMemoryToolIndex,
    LocalTransformerEmbeddingBackend,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "eval/datasets/routing_v1_evaluation.json"
SCHEMA = ROOT / "eval/datasets/routing_v1.schema.json"
GROUPS = ("k3", "full")
REQUEST_TIMEOUT_SECONDS = 45
DECISION_TIMEOUT_SECONDS = 60
SECRET_PATTERN = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|Bearer\s+[A-Za-z0-9._-]{12,})"
)


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def reported_usage(response) -> dict[str, int | None]:
    """Keep only fields actually returned by the completion provider."""
    usage = getattr(response, "usage", None)
    return {
        "input_tokens": getattr(usage, "prompt_tokens", None),
        "output_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


class ProviderAttemptRecorder:
    def __init__(self, create):
        self.create = create
        self.attempts: list[dict] = []

    async def __call__(self, **kwargs):
        started = time.perf_counter()
        try:
            response = await self.create(**kwargs)
        except Exception as exc:
            self.attempts.append(
                {
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    "error_type": type(exc).__name__,
                    "status_code": getattr(exc, "status_code", None),
                    "usage": None,
                    "response_model": None,
                }
            )
            raise
        self.attempts.append(
            {
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "error_type": None,
                "status_code": None,
                "usage": reported_usage(response),
                "response_model": getattr(response, "model", None),
            }
        )
        return response


class EmbeddingCallRecorder:
    def __init__(self, embed):
        self.embed = embed
        self.calls: list[dict] = []

    async def __call__(self, texts):
        started = time.perf_counter()
        try:
            vectors = await self.embed(texts)
        except Exception as exc:
            self.calls.append(
                {
                    "input_texts": len(texts),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    "error_type": type(exc).__name__,
                }
            )
            raise
        self.calls.append(
            {
                "input_texts": len(texts),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "error_type": None,
            }
        )
        return vectors


def build_agent(sample: dict, llm: LLM, client: GitHubClient):
    selected = sample["context"]
    context = RepositoryContext.parse(
        selected["repository"], ref=selected["ref"], source="user_input"
    )
    agent = RepositoryInvestigationAgent.create(
        context, client=client, llm=llm, max_steps=1
    )
    agent.current_step = sample["step"]
    agent.routing_original_task = sample["task"]
    messages = [Message.user_message(sample["task"])]
    if sample["observation"]:
        if sample["id"] != "eva-005":
            raise ValueError("Unexpected evaluation Observation fixture")
        call_id = "fixture-eva-005"
        messages += [
            Message(
                role=Role.ASSISTANT,
                content="",
                tool_calls=[
                    ToolCall(
                        id=call_id,
                        function=Function(
                            name="github_code_search",
                            arguments=json.dumps({"query": "Session.prepare_request"}),
                        ),
                    )
                ],
            ),
            Message.tool_message(
                sample["observation"],
                name="github_code_search",
                tool_call_id=call_id,
            ),
        ]
    agent.messages = messages
    return agent


async def run_case(
    sample: dict,
    group: str,
    llm: LLM,
    client: GitHubClient,
    retriever: SemanticToolRetriever,
    backend: LocalTransformerEmbeddingBackend,
) -> dict:
    if group not in GROUPS:
        raise ValueError(f"Unknown comparison group: {group}")
    agent = build_agent(sample, llm, client)
    agent.routing_top_k = 3 if group == "k3" else None
    agent.routing_retriever = retriever if group == "k3" else None
    registry = [tool.name for tool in agent.available_tools]
    initial_messages = [message.to_dict() for message in agent.messages]
    captured: dict = {}
    events: list[dict] = []
    original_ask_tool = llm.ask_tool
    original_embed = backend.embed
    provider = ProviderAttemptRecorder(llm.client.chat.completions.create)
    embedding = EmbeddingCallRecorder(original_embed)

    async def capture_ask_tool(*args, **kwargs):
        captured["sent_tools"] = [
            schema["function"]["name"] for schema in kwargs["tools"]
        ]
        captured["submitted_messages_sha256"] = _digest(
            {
                "messages": [message.to_dict() for message in kwargs["messages"]],
                "system": [message.to_dict() for message in kwargs["system_msgs"]],
            }
        )
        return await original_ask_tool(*args, **kwargs, timeout=REQUEST_TIMEOUT_SECONDS)

    def capture_event(message):
        raw = message.record["message"]
        if raw.startswith("tool_routing_event "):
            events.append(json.loads(raw.removeprefix("tool_routing_event ")))

    sink_id = logger.add(capture_event, level="INFO")
    llm.ask_tool = capture_ask_tool
    backend.embed = embedding
    started = time.perf_counter()
    error = None
    try:
        with patch.object(llm.client.chat.completions, "create", provider):
            await asyncio.wait_for(agent.think(), timeout=DECISION_TIMEOUT_SECONDS)
    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "status_code": getattr(exc, "status_code", None),
        }
    finally:
        llm.ask_tool = original_ask_tool
        backend.embed = original_embed
        logger.remove(sink_id)

    content = ""
    if agent.messages and agent.messages[-1].role == Role.ASSISTANT:
        content = agent.messages[-1].content or ""
    content = SECRET_PATTERN.sub("[REDACTED]", content)
    sent_tools = captured.get("sent_tools", [])
    bypassed = group == "full" and sent_tools == registry and not events
    if group == "full" and not bypassed and error is None:
        error = {"type": "FullBaselineFilteringDetected", "status_code": None}
    return {
        "sample_id": sample["id"],
        "group": group,
        "expected_action": sample["expected_action"],
        "acceptable_tools": sample["acceptable_tools"],
        "context": sample["context"],
        "tool_registry": registry,
        "control_tools": ["terminate"],
        "input_messages_sha256": _digest(
            {
                "messages": initial_messages,
                "next_step_prompt": agent.next_step_prompt,
                "system_prompt": agent.system_prompt,
            }
        ),
        "submitted_messages_sha256": captured.get("submitted_messages_sha256"),
        "model_called": "sent_tools" in captured,
        "sent_tools": sent_tools,
        "selected_tools": [call.function.name for call in agent.tool_calls],
        "selected_call_ids": [call.id for call in agent.tool_calls],
        "public_content": content[:1000],
        "public_content_truncated": len(content) > 1000,
        "routing_events": events,
        "filter_bypassed": bypassed,
        "provider_attempts": provider.attempts,
        "retry_count": max(0, len(provider.attempts) - 1),
        "embedding_calls": embedding.calls,
        "end_to_end_latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "tool_dispatch_count": 0,
        "error": error,
    }


def _usage_summary(attempts: list[dict]) -> dict:
    successful = [attempt for attempt in attempts if attempt["error_type"] is None]
    result = {}
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        values = [
            attempt["usage"].get(field)
            for attempt in successful
            if attempt["usage"] is not None
        ]
        available = [value for value in values if value is not None]
        result[field] = {
            "sum": sum(available) if available or not successful else None,
            "reported_attempts": len(available),
            "successful_attempts": len(successful),
        }
    return result


def score_group(rows: list[dict]) -> dict:
    tool_rows = [row for row in rows if row["expected_action"] == "tool"]
    clarify_rows = [row for row in rows if row["expected_action"] == "clarify"]
    no_match_rows = [row for row in rows if row["expected_action"] == "no_match"]
    candidate_misses = [
        row["sample_id"]
        for row in tool_rows
        if not set(row["sent_tools"]) & set(row["acceptable_tools"])
    ]
    selection_failures = [
        row["sample_id"]
        for row in tool_rows
        if not row["selected_tools"]
        or not set(row["selected_tools"]) <= set(row["acceptable_tools"])
    ]
    attempts = [attempt for row in rows for attempt in row["provider_attempts"]]
    embeddings = [call for row in rows for call in row["embedding_calls"]]
    retry_attempts = [
        attempt for row in rows for attempt in row["provider_attempts"][1:]
    ]
    latency = [row["end_to_end_latency_ms"] for row in rows]
    return {
        "runs": len(rows),
        "candidate_recall": {
            "numerator": len(tool_rows) - len(candidate_misses),
            "denominator": len(tool_rows),
        },
        "candidate_misses": candidate_misses,
        "tool_selection_accuracy": {
            "numerator": len(tool_rows) - len(selection_failures),
            "denominator": len(tool_rows),
        },
        "tool_selection_failures": selection_failures,
        "extra_tool_samples": [
            row["sample_id"]
            for row in tool_rows
            if set(row["selected_tools"]) - set(row["acceptable_tools"])
        ],
        "repeated_tool_samples": [
            row["sample_id"]
            for row in tool_rows
            if len(set(row["selected_tools"])) < len(row["selected_tools"])
        ],
        "clarify_samples": [row["sample_id"] for row in clarify_rows],
        "clarify_business_calls": [
            row["sample_id"]
            for row in clarify_rows
            if any(name != "terminate" for name in row["selected_tools"])
        ],
        "no_match_samples": [row["sample_id"] for row in no_match_rows],
        "no_match_terminate": {
            "numerator": sum(
                row["selected_tools"] == ["terminate"] for row in no_match_rows
            ),
            "denominator": len(no_match_rows),
        },
        "provider_attempts": len(attempts),
        "provider_failures": sum(
            attempt["error_type"] is not None for attempt in attempts
        ),
        "failed_attempt_elapsed_ms": round(
            sum(
                attempt["elapsed_ms"]
                for attempt in attempts
                if attempt["error_type"] is not None
            ),
            1,
        ),
        "provider_usage": _usage_summary(attempts),
        "retry_count": len(retry_attempts),
        "retry_elapsed_ms": round(
            sum(attempt["elapsed_ms"] for attempt in retry_attempts), 1
        ),
        "retry_usage": _usage_summary(retry_attempts),
        "embedding_calls": len(embeddings),
        "embedding_input_texts": sum(call["input_texts"] for call in embeddings),
        "embedding_elapsed_ms": round(
            sum(call["elapsed_ms"] for call in embeddings), 1
        ),
        "end_to_end_latency_ms": {
            "sum": round(sum(latency), 1),
            "mean": round(statistics.mean(latency), 1) if latency else None,
            "median": round(statistics.median(latency), 1) if latency else None,
            "denominator": len(latency),
        },
        "run_errors": [row["sample_id"] for row in rows if row["error"]],
    }


def _manifest(dataset_bytes: bytes, data: dict, llm: LLM, backend) -> dict:
    safe_config = {
        "model": llm.model,
        "temperature": llm.temperature,
        "max_tokens": llm.max_tokens,
        "api_type": llm.api_type,
        "endpoint_host": urlsplit(llm.base_url).hostname,
        "tool_choice": "auto",
        "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
    }
    return {
        "record_type": "manifest",
        "comparison": "T030",
        "dataset": "eval/datasets/routing_v1_evaluation.json",
        "dataset_version": data["version"],
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "experiment_split_accessed": False,
        "model_config": safe_config,
        "model_config_sha256": _digest(safe_config),
        "embedding": {
            "model_id": backend.model_id,
            "revision": backend.revision,
            "vector_version": backend.vector_version,
        },
        "business_tools": data["tool_pool"],
        "control_tools": ["terminate"],
        "groups": list(GROUPS),
        "normalization": "T028 original task and synthetic observation verbatim",
        "budget": {"decision_steps": 1, "tool_dispatches": 0},
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "base_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
    }


async def run_evaluation(output: Path, resume: bool = False):
    dataset_bytes = DATASET.read_bytes()
    data = json.loads(dataset_bytes)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    if list(Draft7Validator(schema).iter_errors(data)):
        raise ValueError("Evaluation dataset failed schema validation")
    if data["split"] != "evaluation" or data["version"] != "1.1.0":
        raise ValueError("T030 requires the v1.1.0 evaluation split")
    if len(data["samples"]) != 12:
        raise ValueError("T030 requires all 12 sealed evaluation samples")
    llm = LLM()
    backend = LocalTransformerEmbeddingBackend()
    retriever = SemanticToolRetriever(InMemoryToolIndex(backend))
    manifest = _manifest(dataset_bytes, data, llm, backend)
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = set()
    if output.exists():
        if not resume:
            raise FileExistsError(output)
        with output.open(encoding="utf-8") as stream:
            old = [json.loads(line) for line in stream if line.strip()]
        if not old or any(
            old[0][key] != manifest[key]
            for key in ("dataset_sha256", "model_config_sha256", "runner_sha256")
        ):
            raise ValueError(
                "Resume configuration differs from the recorded evaluation"
            )
        completed = {(item["sample_id"], item["group"]) for item in old[1:]}
    else:
        output.write_text(
            json.dumps(manifest, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    logger.remove()
    client = GitHubClient(
        http_client=httpx.AsyncClient(base_url="https://api.github.com")
    )
    try:
        with output.open("a", encoding="utf-8") as stream:
            for index, sample in enumerate(data["samples"]):
                groups = GROUPS if index % 2 == 0 else GROUPS[::-1]
                for group in groups:
                    if (sample["id"], group) in completed:
                        continue
                    record = await run_case(
                        sample, group, llm, client, retriever, backend
                    )
                    record["record_type"] = "run"
                    record["model_config_sha256"] = manifest["model_config_sha256"]
                    record["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    stream.flush()
                    completed.add((sample["id"], group))
                    print(
                        f"{len(completed)}/24 {sample['id']} {group} "
                        f"selected={','.join(record['selected_tools']) or '-'} "
                        f"error={record['error']['type'] if record['error'] else '-'}",
                        flush=True,
                    )
    finally:
        await client.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    asyncio.run(run_evaluation(args.output, args.resume))


if __name__ == "__main__":
    main()
