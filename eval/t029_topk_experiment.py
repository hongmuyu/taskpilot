"""Paired, selection-only T029 experiment over the sealed v1.1 experiment split."""

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft7Validator

from app.agent.repository_investigation import RepositoryInvestigationAgent
from app.agent.toolcall import ToolCallAgent
from app.llm import LLM
from app.logger import logger
from app.schema import Function, Message, Role, ToolCall
from app.taskpilot.github_tools import (
    GitHubClient,
    GitHubCodeSearch,
    GitHubIssueDetail,
    GitHubIssueSearch,
    GitHubReadFile,
    GitHubRepositoryInfo,
    RepositoryContext,
)
from app.taskpilot.semantic_tool_retrieval import SemanticToolRetriever
from app.taskpilot.tool_embedding_index import (
    InMemoryToolIndex,
    LocalTransformerEmbeddingBackend,
)
from app.tool import Terminate, ToolCollection


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "eval/datasets/routing_v1_experiment.json"
SCHEMA = ROOT / "eval/datasets/routing_v1.schema.json"
GROUPS = ("k1", "k3", "k5", "full")
REQUEST_TIMEOUT_SECONDS = 45
DECISION_TIMEOUT_SECONDS = 60
PREVIOUS_CALLS = {
    "exp-018": ("github_issue_search", {"query": "MCP connection"}),
    "exp-020": ("github_issue_search", {"query": "timeout"}),
    "exp-028": ("github_issue_detail", {"issue_number": 1427}),
}
SECRET_PATTERN = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|Bearer\s+[A-Za-z0-9._-]{12,})"
)


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _tools_for_context(context: RepositoryContext, client: GitHubClient) -> ToolCollection:
    return ToolCollection(
        GitHubRepositoryInfo(context=context, client=client),
        GitHubIssueSearch(context=context, client=client),
        GitHubIssueDetail(context=context, client=client),
        GitHubCodeSearch(context=context, client=client),
        GitHubReadFile(context=context, client=client),
        Terminate(),
    )


def _agent_for_sample(sample, llm: LLM, client: GitHubClient) -> ToolCallAgent:
    selected = sample["context"]
    if selected["repository"]:
        context = RepositoryContext.parse(
            selected["repository"], ref=selected["ref"], source="user_input"
        )
        agent = RepositoryInvestigationAgent.create(
            context, client=client, llm=llm, max_steps=1
        )
    else:
        # This selection-only sample has no repository; the placeholder is never dispatched.
        placeholder = RepositoryContext.parse("routing/fixture", source="user_input")
        agent = ToolCallAgent(
            llm=llm,
            available_tools=_tools_for_context(placeholder, client),
            system_prompt=(
                "You are TaskPilot, a read-only repository investigation agent. "
                "No GitHub repository has been confirmed. Ask for owner/repo before "
                "using a GitHub tool. Do not assume a repository or perform writes. "
                "When done, call terminate."
            ),
            max_steps=1,
        )

    agent.current_step = sample["step"]
    agent.routing_original_task = sample["task"]
    messages = [Message.user_message(sample["task"])]
    if sample["observation"]:
        previous_name, previous_args = PREVIOUS_CALLS[sample["id"]]
        call_id = f"fixture-{sample['id']}"
        previous_call = ToolCall(
            id=call_id,
            function=Function(name=previous_name, arguments=json.dumps(previous_args)),
        )
        messages += [
            Message(role=Role.ASSISTANT, content="", tool_calls=[previous_call]),
            Message.tool_message(
                sample["observation"], name=previous_name, tool_call_id=call_id
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
) -> dict:
    if group not in GROUPS:
        raise ValueError(f"Unknown experiment group: {group}")
    agent = _agent_for_sample(sample, llm, client)
    agent.routing_top_k = None if group == "full" else int(group[1:])
    agent.routing_retriever = None if group == "full" else retriever
    registry = [tool.name for tool in agent.available_tools]
    initial_messages = [message.to_dict() for message in agent.messages]
    captured = {}
    events = []
    original_ask_tool = llm.ask_tool

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
        return await original_ask_tool(
            *args, **kwargs, timeout=REQUEST_TIMEOUT_SECONDS
        )

    def capture_event(message):
        raw = message.record["message"]
        if raw.startswith("tool_routing_event "):
            events.append(json.loads(raw.removeprefix("tool_routing_event ")))

    sink_id = logger.add(capture_event, level="INFO")
    llm.ask_tool = capture_ask_tool
    started = time.perf_counter()
    error = None
    try:
        await asyncio.wait_for(agent.think(), timeout=DECISION_TIMEOUT_SECONDS)
    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "status_code": getattr(exc, "status_code", None),
        }
    finally:
        llm.ask_tool = original_ask_tool
        logger.remove(sink_id)

    content = ""
    if agent.messages and agent.messages[-1].role == Role.ASSISTANT:
        content = agent.messages[-1].content or ""
    content = SECRET_PATTERN.sub("[REDACTED]", content)
    sent_tools = captured.get("sent_tools", [])
    bypassed = group == "full" and sent_tools == registry and not events
    if group == "full" and not bypassed and error is None:
        error = {"type": "FullBaselineFilteringDetected", "status_code": None}
    selected = [call.function.name for call in agent.tool_calls]
    return {
        "sample_id": sample["id"],
        "group": group,
        "k_business_limit": agent.routing_top_k,
        "context": sample["context"],
        "expected_action": sample["expected_action"],
        "acceptable_tools": sample["acceptable_tools"],
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
        "selected_tools": selected,
        "selected_call_ids": [call.id for call in agent.tool_calls],
        "public_content": content[:1000],
        "public_content_truncated": len(content) > 1000,
        "routing_events": events,
        "filter_bypassed": bypassed,
        "tool_dispatch_count": 0,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "error": error,
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
        "experiment": "T029",
        "dataset": "eval/datasets/routing_v1_experiment.json",
        "dataset_version": data["version"],
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "evaluation_split_accessed": False,
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


async def run_experiment(output: Path, limit: int | None = None, resume: bool = False):
    dataset_bytes = DATASET.read_bytes()
    data = json.loads(dataset_bytes)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    if list(Draft7Validator(schema).iter_errors(data)):
        raise ValueError("Experiment dataset failed schema validation")
    if data["split"] != "experiment" or data["version"] != "1.1.0":
        raise ValueError("T029 requires the v1.1.0 experiment split")
    samples = data["samples"][:limit] if limit is not None else data["samples"]
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
        if not old or old[0]["dataset_sha256"] != manifest["dataset_sha256"]:
            raise ValueError("Resume dataset does not match the current experiment")
        if old[0]["model_config_sha256"] != manifest["model_config_sha256"]:
            raise ValueError("Resume model configuration changed")
        completed = {(item["sample_id"], item["group"]) for item in old[1:]}
    else:
        output.write_text(json.dumps(manifest, ensure_ascii=False) + "\n", encoding="utf-8")

    logger.remove()
    client = GitHubClient(http_client=httpx.AsyncClient(base_url="https://api.github.com"))
    total = len(samples) * len(GROUPS)
    try:
        with output.open("a", encoding="utf-8") as stream:
            for index, sample in enumerate(samples):
                groups = GROUPS[index % 4 :] + GROUPS[: index % 4]
                for group in groups:
                    if (sample["id"], group) in completed:
                        continue
                    record = await run_case(sample, group, llm, client, retriever)
                    record["record_type"] = "run"
                    record["model_config_sha256"] = manifest["model_config_sha256"]
                    record["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    stream.flush()
                    completed.add((sample["id"], group))
                    print(
                        f"{len(completed)}/{total} {sample['id']} {group} "
                        f"selected={','.join(record['selected_tools']) or '-'} "
                        f"error={record['error']['type'] if record['error'] else '-'}",
                        flush=True,
                    )
    finally:
        await client.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    asyncio.run(run_experiment(args.output, args.limit, args.resume))


if __name__ == "__main__":
    main()
