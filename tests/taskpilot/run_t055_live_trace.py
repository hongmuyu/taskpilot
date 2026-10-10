"""Opt-in real provider and GitHub read-only investigation Trace acceptance.

Run with ``PYTHONPATH=. .venv/bin/python tests/taskpilot/run_t055_live_trace.py``.
The output contains only the allowlisted Trace, public GitHub request paths,
status codes, and derived counts. No model text, tool arguments, or credentials.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.agent.repository_investigation import RepositoryInvestigationAgent
from app.config import config
from app.llm import LLM
from app.logger import logger
from app.schema import Role
from app.taskpilot.execution_trace import summarize_trace_costs, validate_trace_events
from app.taskpilot.github_tools import GitHubClient, RepositoryContext
from app.taskpilot.tool_embedding_index import LocalTransformerEmbeddingBackend


OUTPUT = (
    Path(__file__).parents[2] / "docs" / "evidence" / "t055_provider_github_trace.json"
)
REPOSITORY = "FoundationAgents/OpenManus"
PROMPT = (
    "Summarize FoundationAgents/OpenManus repository metadata and README. "
    "Identify its main language and default branch using GitHub evidence, "
    "then finish."
)


async def main() -> int:
    logger.remove()
    settings = config.llm["default"]
    llm = LLM(
        config_name="t055_live",
        llm_config={"default": settings, "t055_live": settings},
    )
    llm.client = llm.client.with_options(max_retries=0)
    http_events = []

    async def capture_http(response: httpx.Response) -> None:
        http_events.append(
            {
                "method": response.request.method,
                "path": response.request.url.path,
                "status": response.status_code,
            }
        )

    http_client = httpx.AsyncClient(
        base_url="https://api.github.com",
        timeout=20.0,
        event_hooks={"response": [capture_http]},
    )
    github = GitHubClient(token=os.getenv("GITHUB_TOKEN"), http_client=http_client)
    agent = RepositoryInvestigationAgent.create(
        RepositoryContext.parse(REPOSITORY, source="user_input"),
        client=github,
        llm=llm,
        max_steps=6,
    )
    agent.routing_top_k = 3
    error_type = None
    try:
        await asyncio.wait_for(agent.run(PROMPT), timeout=180)
    except Exception as exc:
        error_type = type(exc).__name__
    finally:
        await github.aclose()

    events = agent.execution_trace.events
    trace_valid = False
    cost = None
    try:
        validate_trace_events(events)
        cost = summarize_trace_costs(events)
        trace_valid = True
    except ValueError:
        pass
    dispatched = [
        event
        for event in events
        if event["event"] == "tool_execution" and event["status"] == "started"
    ]
    observed = [event for event in events if event["event"] == "observation"]
    provider_attempts = [
        event for event in events if event["event"] == "llm_provider_attempt"
    ]
    successful_paths = {item["path"] for item in http_events if item["status"] == 200}
    required_paths = {
        f"/repos/{REPOSITORY}",
        f"/repos/{REPOSITORY}/contents/README.md",
    }
    answer_present = any(
        message.role == Role.ASSISTANT
        and message.content
        and len(message.content.strip()) >= 40
        for message in agent.messages
    )
    passed = (
        error_type is None
        and trace_valid
        and bool(provider_attempts)
        and bool(dispatched)
        and bool(observed)
        and required_paths <= successful_paths
        and answer_present
        and events[-1]["event"] == "finish"
        and events[-1]["status"] == "success"
        and any(item["event"] == "routing" for item in events)
    )
    evidence = {
        "result": "PASS" if passed else "NOT VERIFIED",
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": llm.model,
        "provider_revision": "NOT AVAILABLE",
        "embedding_model": LocalTransformerEmbeddingBackend.model_id,
        "embedding_revision": LocalTransformerEmbeddingBackend.revision,
        "repository": REPOSITORY,
        "ref": "default_branch",
        "routing_k_business": 3,
        "github_http": http_events,
        "trace_valid": trace_valid,
        "answer_present": answer_present,
        "required_github_http_200": required_paths <= successful_paths,
        "provider_attempt_count": len(provider_attempts),
        "tool_dispatch_count": len(dispatched),
        "observation_count": len(observed),
        "cost": cost,
        "error_type": error_type,
        "trace_events": events,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(
        {
            "result": evidence["result"],
            "model": llm.model,
            "http_count": len(http_events),
            "provider_attempts": len(provider_attempts),
            "tool_dispatches": len(dispatched),
            "trace_valid": trace_valid,
            "error_type": error_type,
        }
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
