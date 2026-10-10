"""Opt-in FastAPI + real provider + GitHub read-only smoke for T056.

Run with ``PYTHONPATH=. .venv/bin/python tests/taskpilot/run_t056_live_service.py``.
The saved evidence contains no prompt, model response, tool arguments, or keys.
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
from app.taskpilot.execution_trace import validate_trace_events
from app.taskpilot.github_tools import GitHubClient
from app.taskpilot.service import create_app
from app.taskpilot.tool_embedding_index import LocalTransformerEmbeddingBackend


OUTPUT = Path(__file__).parents[2] / "docs/evidence/t056_service_github_smoke.json"
REPOSITORY = "FoundationAgents/OpenManus"
PROMPT = (
    "Summarize this repository using its GitHub metadata and README. "
    "State its main language and default branch with source evidence, then finish."
)


async def main() -> int:
    logger.remove()
    settings = config.llm["default"]
    llm = LLM(
        config_name="t056_service_live",
        llm_config={"default": settings, "t056_service_live": settings},
    )
    llm.client = llm.client.with_options(max_retries=0)
    http_events: list[dict] = []
    agents = []

    async def capture_http(response: httpx.Response) -> None:
        http_events.append(
            {
                "method": response.request.method,
                "path": response.request.url.path,
                "status": response.status_code,
            }
        )

    def make_agent(context):
        github = GitHubClient(
            token=os.getenv("GITHUB_TOKEN"),
            http_client=httpx.AsyncClient(
                base_url="https://api.github.com",
                timeout=20.0,
                event_hooks={"response": [capture_http]},
            ),
        )
        agent = RepositoryInvestigationAgent.create(
            context, client=github, llm=llm, max_steps=6
        )
        agents.append(agent)
        return agent

    app = create_app(agent_factory=make_agent)
    view = None
    api_statuses = []
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://taskpilot.local"
        ) as client:
            started = await client.post(
                "/tasks", json={"repository": REPOSITORY, "task": PROMPT}
            )
            api_statuses.append(started.status_code)
            if started.status_code == 202:
                task_id = started.json()["task_id"]
                for _ in range(180):
                    polled = await client.get(f"/tasks/{task_id}")
                    api_statuses.append(polled.status_code)
                    view = polled.json()
                    if view["status"] not in {"queued", "running", "cancelling"}:
                        break
                    await asyncio.sleep(1)

    events = agents[0].execution_trace.events if agents else []
    trace_valid = False
    try:
        validate_trace_events(events)
        trace_valid = bool(events)
    except ValueError:
        pass
    metadata_and_readme_paths = {
        f"/repos/{REPOSITORY}",
        f"/repos/{REPOSITORY}/contents/README.md",
    }
    successful_paths = {x["path"] for x in http_events if x["status"] == 200}
    repository_http_200 = any(
        path == f"/repos/{REPOSITORY}" or path.startswith(f"/repos/{REPOSITORY}/")
        for path in successful_paths
    )
    tool_dispatch_count = sum(
        x["event"] == "tool_execution" and x["status"] == "started" for x in events
    )
    passed = (
        view is not None
        and view["status"] == "finished"
        and view["execution_status"] == "success"
        and view["finish_source"] == "agent_declaration"
        and view["business_outcome"] == "not_verified"
        and view["cleanup_status"] == "complete"
        and bool(view["result"])
        and trace_valid
        and any(x["event"] == "routing" for x in events)
        and tool_dispatch_count > 0
        and repository_http_200
        and all(x["method"] == "GET" for x in http_events)
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
        "api_http_statuses": api_statuses,
        "task_status": view["status"] if view else None,
        "agent_finish_status": view["execution_status"] if view else None,
        "finish_source": view["finish_source"] if view else None,
        "business_outcome": view["business_outcome"] if view else "not_verified",
        "failure_observation_count": view["failure_observation_count"]
        if view
        else None,
        "cleanup_status": view["cleanup_status"] if view else None,
        "trace_valid": trace_valid,
        "trace_event_count": len(events),
        "routing_event_count": sum(x["event"] == "routing" for x in events),
        "tool_dispatch_count": tool_dispatch_count,
        "github_http": http_events,
        "repository_http_200": repository_http_200,
        "metadata_and_readme_http_200": metadata_and_readme_paths <= successful_paths,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(
        {
            "result": evidence["result"],
            "model": evidence["model"],
            "task_status": evidence["task_status"],
            "http_count": len(http_events),
            "trace_event_count": len(events),
            "cleanup_status": evidence["cleanup_status"],
        }
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
