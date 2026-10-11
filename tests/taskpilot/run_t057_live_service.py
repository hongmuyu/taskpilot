"""Opt-in real provider + GitHub service clarification E2E for T057.

The provider selects the tool. This harness removes its first read_file path to
exercise the missing-parameter boundary without claiming natural omission.
Saved evidence excludes prompts, responses, arguments, and credentials.
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


OUTPUT = Path(__file__).parents[2] / "docs/evidence/t057_service_clarification.json"
REPOSITORY = "FoundationAgents/OpenManus"
TASK = "Read README.md from this repository, summarize what it provides, and finish."


class ControlledOmissionLLM(LLM):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.injected = False
        self.provider_calls = 0

    async def ask_tool(self, *args, **kwargs):
        response = await super().ask_tool(*args, **kwargs)
        self.provider_calls += 1
        if response and response.tool_calls and not self.injected:
            for call in response.tool_calls:
                if call.function.name == "github_read_file":
                    call.function.arguments = "{}"
                    self.injected = True
                    break
        return response


async def poll(client: httpx.AsyncClient, task_id: str, statuses: set[str]):
    for _ in range(90):
        response = await client.get(f"/tasks/{task_id}")
        if response.status_code != 200:
            return None
        view = response.json()
        if view["status"] in statuses:
            return view
        await asyncio.sleep(1)
    return None


async def main() -> int:
    logger.remove()
    settings = config.llm["default"]
    llm = ControlledOmissionLLM(
        config_name="t057_service_live",
        llm_config={"default": settings, "t057_service_live": settings},
    )
    llm.client = llm.client.with_options(max_retries=0)
    http_events = []
    agents = []

    async def capture_http(response: httpx.Response):
        http_events.append(
            {
                "method": response.request.method,
                "path": response.request.url.path,
                "status": response.status_code,
            }
        )

    def make_agent(context):
        agent = RepositoryInvestigationAgent.create(
            context,
            client=GitHubClient(
                token=os.getenv("GITHUB_TOKEN"),
                http_client=httpx.AsyncClient(
                    base_url="https://api.github.com",
                    timeout=20.0,
                    event_hooks={"response": [capture_http]},
                ),
            ),
            llm=llm,
            max_steps=5,
        )
        agents.append(agent)
        return agent

    app = create_app(agent_factory=make_agent, clarification_timeout_seconds=90.0)
    started_status = None
    reply_status = None
    pending = None
    final = None
    original_id = None
    pre_reply_original_dispatch = None
    pre_reply_http_count = None
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://taskpilot.local"
        ) as client:
            started = await client.post(
                "/tasks", json={"repository": REPOSITORY, "task": TASK}
            )
            started_status = started.status_code
            if started_status == 202:
                task_id = started.json()["task_id"]
                pending = await poll(
                    client,
                    task_id,
                    {"clarification_pending", "finished", "failed", "unknown"},
                )
                if pending and pending["status"] == "clarification_pending":
                    clarification = pending["clarification"]
                    original_id = clarification["tool_call_id"]
                    trace = agents[0].execution_trace
                    call_ref = trace.call_ref(original_id)
                    pre_reply_original_dispatch = sum(
                        event["event"] == "tool_execution"
                        and event["status"] == "started"
                        and event.get("tool_call_id") == call_ref
                        for event in trace.events
                    )
                    pre_reply_http_count = len(http_events)
                    response = await client.post(
                        f"/tasks/{task_id}/clarifications/{original_id}/reply",
                        json={"reply": '{"path":"README.md"}'},
                    )
                    reply_status = response.status_code
                    final = await poll(
                        client,
                        task_id,
                        {
                            "finished",
                            "failed",
                            "unknown",
                            "cancelled",
                            "clarification_timeout",
                        },
                    )
                else:
                    final = pending

    events = agents[0].execution_trace.events if agents else []
    trace_valid = False
    try:
        validate_trace_events(events)
        trace_valid = bool(events)
    except ValueError:
        pass
    call_ref = (
        agents[0].execution_trace.call_ref(original_id)
        if agents and original_id
        else None
    )
    selected = [
        event
        for event in events
        if event["event"] == "llm_selection" and event.get("tool_call_id") == call_ref
    ]
    validation = [
        event["status"]
        for event in events
        if event["event"] == "validation" and event.get("tool_call_id") == call_ref
    ]
    clarification = [
        event["status"]
        for event in events
        if event["event"] == "clarification" and event.get("tool_call_id") == call_ref
    ]
    original_dispatch = sum(
        event["event"] == "tool_execution"
        and event["status"] == "started"
        and event.get("tool_call_id") == call_ref
        for event in events
    )
    original_messages = (
        [
            message
            for message in agents[0].memory.messages
            if message.tool_call_id == original_id
        ]
        if agents and original_id
        else []
    )
    read_http = [
        event
        for event in http_events
        if event["path"] == f"/repos/{REPOSITORY}/contents/README.md"
    ]
    passed = (
        llm.injected
        and started_status == 202
        and pending is not None
        and pending["status"] == "clarification_pending"
        and pending["clarification"]["missing_fields"] == ["path"]
        and reply_status == 202
        and final is not None
        and final["status"] == "finished"
        and final["cleanup_status"] == "complete"
        and final["business_outcome"] == "not_verified"
        and pre_reply_original_dispatch == 0
        and pre_reply_http_count == 0
        and original_dispatch == 1
        and len(original_messages) == 1
        and "pending" in validation
        and "success" in validation
        and {"requested", "waited", "merged"} <= set(clarification)
        and len(selected) == 1
        and len(read_http) == 1
        and read_http[0]["status"] == 200
        and trace_valid
    )
    evidence = {
        "result": "PASS" if passed else "NOT VERIFIED",
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": llm.model,
        "provider_revision": "NOT AVAILABLE",
        "repository": REPOSITORY,
        "ref": "default_branch",
        "routing_k_business": 3,
        "omission_mode": "controlled_path_removal_after_real_model_tool_selection",
        "model_selected_read_file": bool(selected),
        "model_calls": llm.provider_calls,
        "api_start_http_status": started_status,
        "api_reply_http_status": reply_status,
        "pending_status": pending["status"] if pending else None,
        "pending_missing_fields": (
            pending["clarification"]["missing_fields"]
            if pending and pending.get("clarification")
            else None
        ),
        "final_status": final["status"] if final else None,
        "finish_source": final["finish_source"] if final else None,
        "business_outcome": final["business_outcome"] if final else "not_verified",
        "cleanup_status": final["cleanup_status"] if final else None,
        "original_tool_call_ref": call_ref,
        "pre_reply_original_dispatch_count": pre_reply_original_dispatch,
        "pre_reply_http_count": pre_reply_http_count,
        "original_dispatch_count": original_dispatch,
        "original_tool_message_count": len(original_messages),
        "validation_statuses": validation,
        "clarification_statuses": clarification,
        "github_http": http_events,
        "trace_event_count": len(events),
        "trace_valid": trace_valid,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(
        {
            "result": evidence["result"],
            "pending_status": evidence["pending_status"],
            "final_status": evidence["final_status"],
            "original_dispatch_count": evidence["original_dispatch_count"],
            "read_http_count": len(read_http),
            "trace_valid": trace_valid,
        }
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
