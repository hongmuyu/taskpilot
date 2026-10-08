"""Read-only T021 investigation regression with K=3 routing enabled."""

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.agent.repository_investigation import RepositoryInvestigationAgent
from app.llm import LLM
from app.logger import logger
from app.schema import Role
from app.taskpilot.github_tools import GitHubClient, RepositoryContext
from app.taskpilot.tool_embedding_index import LocalTransformerEmbeddingBackend
from eval.t030_routing_comparison import SECRET_PATTERN


CASES = (
    (
        "repository_understanding",
        "Summarize FoundationAgents/OpenManus from its repository metadata and README. "
        "State its main language and default branch, with evidence, then finish.",
    ),
    (
        "issue_investigation",
        "Investigate FoundationAgents/OpenManus issue #1427 and its discussion. "
        "Summarize the reported MCP isError problem with issue evidence, then finish.",
    ),
    (
        "issue_to_code",
        "Trace FoundationAgents/OpenManus issue #1427 about MCP isError to the "
        "relevant code. Cite the issue and source file evidence, then finish.",
    ),
)
REPOSITORY = "FoundationAgents/OpenManus"
MAX_STEPS = 12
CASE_TIMEOUT_SECONDS = 240


def termination_status(messages) -> str | None:
    call_ids = {
        call.id
        for message in messages
        if message.role == Role.ASSISTANT and message.tool_calls
        for call in message.tool_calls
        if call.function.name == "terminate"
    }
    for message in reversed(messages):
        if (
            message.role == Role.TOOL
            and message.name == "terminate"
            and message.tool_call_id in call_ids
        ):
            content = message.content or ""
            for status in ("success", "failure"):
                if (
                    f"The interaction has been completed with status: {status}"
                    in content
                ):
                    return status
    return None


async def run_case(case_id: str, prompt: str, llm: LLM) -> dict:
    http_events = []
    routing_events = []

    async def capture_http(response: httpx.Response):
        http_events.append(
            {
                "method": response.request.method,
                "path": response.request.url.path,
                "status_code": response.status_code,
            }
        )

    def capture_event(message):
        raw = message.record["message"]
        if raw.startswith("tool_routing_event "):
            routing_events.append(json.loads(raw.removeprefix("tool_routing_event ")))

    http_client = httpx.AsyncClient(
        base_url="https://api.github.com",
        timeout=20.0,
        event_hooks={"response": [capture_http]},
    )
    github = GitHubClient(token=os.getenv("GITHUB_TOKEN"), http_client=http_client)
    context = RepositoryContext.parse(REPOSITORY, source="user_input")
    agent = RepositoryInvestigationAgent.create(
        context, client=github, llm=llm, max_steps=MAX_STEPS
    )
    agent.routing_top_k = 3
    agent.routing_original_task = prompt
    sink_id = logger.add(capture_event, level="INFO")
    started = time.perf_counter()
    error = None
    result = ""
    try:
        result = await asyncio.wait_for(agent.run(prompt), timeout=CASE_TIMEOUT_SECONDS)
    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "status_code": getattr(exc, "status_code", None),
        }
    finally:
        logger.remove(sink_id)
        await agent.cleanup()

    calls = [
        {"name": call.function.name, "tool_call_id": call.id}
        for message in agent.messages
        if message.role == Role.ASSISTANT and message.tool_calls
        for call in message.tool_calls
    ]
    answers = [
        message.content
        for message in agent.messages
        if message.role == Role.ASSISTANT and message.content
    ]
    final_answer = SECRET_PATTERN.sub("[REDACTED]", answers[-1] if answers else "")
    completed_status = termination_status(agent.messages)
    return {
        "record_type": "case",
        "case_id": case_id,
        "repository": REPOSITORY,
        "ref": None,
        "source": "user_input",
        "routing_k_business": 3,
        "max_steps": MAX_STEPS,
        "tool_calls": calls,
        "github_http": http_events,
        "routing_events": routing_events,
        "finished": completed_status == "success",
        "termination_status": completed_status,
        "max_steps_reached": "Reached max steps" in result,
        "final_answer_excerpt": final_answer[:1000],
        "final_answer_truncated": len(final_answer) > 1000,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "error": error,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
    }


async def run_regression(output: Path):
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    llm = LLM()
    backend = LocalTransformerEmbeddingBackend()
    manifest = {
        "record_type": "manifest",
        "regression": "T030/T021",
        "model": llm.model,
        "temperature": llm.temperature,
        "max_tokens": llm.max_tokens,
        "endpoint_host": urlsplit(llm.base_url).hostname,
        "embedding_model": backend.model_id,
        "embedding_revision": backend.revision,
        "repository": REPOSITORY,
        "ref": None,
        "github_token_present": bool(os.getenv("GITHUB_TOKEN")),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    output.write_text(json.dumps(manifest, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.remove()
    with output.open("a", encoding="utf-8") as stream:
        for case_id, prompt in CASES:
            record = await run_case(case_id, prompt, llm)
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            print(
                f"{case_id} http={len(record['github_http'])} "
                f"finished={record['finished']} "
                f"error={record['error']['type'] if record['error'] else '-'}",
                flush=True,
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run_regression(args.output))


if __name__ == "__main__":
    main()
