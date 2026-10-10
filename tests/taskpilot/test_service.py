"""T056 FastAPI boundary and task lifecycle tests."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.taskpilot.service import create_app


with patch(
    "tomllib.load",
    return_value={
        "llm": {
            "model": "test",
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": "test",
        },
        "daytona": {"daytona_api_key": "unused-test"},
    },
):
    from app.agent.repository_investigation import RepositoryInvestigationAgent
    from app.llm import LLM
    from app.schema import Function, ToolCall
    from app.taskpilot.github_tools import GitHubClient


async def wait_for_terminal(client: httpx.AsyncClient, task_id: str) -> dict:
    for _ in range(100):
        response = await client.get(f"/tasks/{task_id}")
        assert response.status_code == 200
        view = response.json()
        if view["status"] not in {"queued", "running", "cancelling"}:
            return view
        await asyncio.sleep(0.01)
    raise AssertionError("Task did not finish")


@pytest.mark.asyncio
async def test_invalid_start_never_creates_an_agent():
    created = []
    app = create_app(agent_factory=lambda context: created.append(context))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for payload in (
            {"repository": "not-a-repository", "task": "Inspect it"},
            {"repository": "octo/repo", "task": "  "},
            {"repository": "octo/repo", "task": "Inspect", "ref": "../bad"},
            {"repository": "octo/repo", "task": "Inspect", "unexpected": True},
            {"repository": 123, "task": "Inspect"},
        ):
            response = await client.post("/tasks", json=payload)
            assert response.status_code == 422
    assert created == []


@pytest.mark.asyncio
async def test_parallel_tasks_keep_context_and_trace_isolated_and_cleanup_clients():
    agents = []
    paths = []

    def make_agent(context):
        calls = 0

        async def ask_tool(**kwargs):
            nonlocal calls
            calls += 1
            name = "github_repository_info" if calls == 1 else "terminate"
            arguments = "{}" if calls == 1 else '{"status":"success"}'
            return SimpleNamespace(
                content=f"Evidence for {context.repo}",
                tool_calls=[
                    ToolCall(
                        id=f"{context.repo}-{calls}",
                        function=Function(name=name, arguments=arguments),
                    )
                ],
            )

        def respond(request):
            paths.append(request.url.path)
            return httpx.Response(
                200,
                json={
                    "full_name": f"{context.owner}/{context.repo}",
                    "language": "Python",
                },
            )

        llm = object.__new__(LLM)
        llm.ask_tool = AsyncMock(side_effect=ask_tool)
        github = GitHubClient(
            http_client=httpx.AsyncClient(
                base_url="https://api.github.com",
                transport=httpx.MockTransport(respond),
            )
        )
        agent = RepositoryInvestigationAgent.create(
            context, client=github, llm=llm, max_steps=3
        )
        agents.append(agent)
        return agent

    app = create_app(agent_factory=make_agent, routing_top_k=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post(
            "/tasks",
            json={
                "repository": "octo/first",
                "ref": "release/v1",
                "task": "Inspect first",
            },
        )
        second = await client.post(
            "/tasks", json={"repository": "octo/second", "task": "Inspect second"}
        )
        assert first.status_code == second.status_code == 202
        first_id, second_id = first.json()["task_id"], second.json()["task_id"]
        assert first_id != second_id
        first_view, second_view = await asyncio.gather(
            wait_for_terminal(client, first_id), wait_for_terminal(client, second_id)
        )

    assert {first_view["status"], second_view["status"]} == {"finished"}
    assert {first_view["execution_status"], second_view["execution_status"]} == {
        "success"
    }
    assert (
        first_view["business_outcome"]
        == second_view["business_outcome"]
        == "not_verified"
    )
    assert (
        first_view["finish_source"]
        == second_view["finish_source"]
        == "agent_declaration"
    )
    assert first_view["cleanup_status"] == second_view["cleanup_status"] == "complete"
    assert first_view["run_id"] != second_view["run_id"]
    assert "octo/first" in first_view["result"]
    assert "octo/second" in second_view["result"]
    assert paths == ["/repos/octo/first", "/repos/octo/second"]
    assert agents[0] is not agents[1]
    assert agents[0].repository_context.ref == "release/v1"
    assert agents[1].repository_context.ref is None
    assert all(agent.github_client.http_client.is_closed for agent in agents)


@pytest.mark.asyncio
async def test_missing_parameter_never_waits_for_cli_input_or_dispatches_github(
    monkeypatch,
):
    def unexpected_input(*args, **kwargs):
        raise AssertionError("Service must not call synchronous input")

    monkeypatch.setattr("builtins.input", unexpected_input)
    http_paths = []
    agents = []

    def make_agent(context):
        def respond(request):
            http_paths.append(request.url.path)
            return httpx.Response(200, json={})

        llm = object.__new__(LLM)
        llm.ask_tool = AsyncMock(
            return_value=SimpleNamespace(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="missing-path-1",
                        function=Function(name="github_read_file", arguments="{}"),
                    )
                ],
            )
        )
        agent = RepositoryInvestigationAgent.create(
            context,
            client=GitHubClient(
                http_client=httpx.AsyncClient(
                    base_url="https://api.github.com",
                    transport=httpx.MockTransport(respond),
                )
            ),
            llm=llm,
            max_steps=3,
        )
        agents.append(agent)
        return agent

    app = create_app(agent_factory=make_agent, routing_top_k=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        started = await client.post(
            "/tasks", json={"repository": "octo/repo", "task": "Read a file"}
        )
        assert started.status_code == 202
        view = await wait_for_terminal(client, started.json()["task_id"])

    assert view["status"] == "clarification_unsupported"
    assert view["error"] == "clarification_not_supported"
    assert view["business_outcome"] == "not_verified"
    assert view["cleanup_status"] == "complete"
    assert http_paths == []
    events = agents[0].execution_trace.events
    call_ref = agents[0].execution_trace.call_ref("missing-path-1")
    assert any(
        event["event"] == "clarification"
        and event["status"] == "pending"
        and event["tool_call_id"] == call_ref
        for event in events
    )


@pytest.mark.asyncio
async def test_running_task_can_be_cancelled_without_blocking_other_requests():
    waiting = asyncio.Event()
    agents = []

    def make_agent(context):
        async def ask_tool(**kwargs):
            waiting.set()
            await asyncio.Event().wait()

        llm = object.__new__(LLM)
        llm.ask_tool = AsyncMock(side_effect=ask_tool)
        agent = RepositoryInvestigationAgent.create(
            context,
            client=GitHubClient(
                http_client=httpx.AsyncClient(
                    base_url="https://api.github.com",
                    transport=httpx.MockTransport(
                        lambda _: httpx.Response(200, json={})
                    ),
                )
            ),
            llm=llm,
        )
        agents.append(agent)
        return agent

    app = create_app(agent_factory=make_agent, routing_top_k=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        started = await client.post(
            "/tasks", json={"repository": "octo/slow", "task": "Inspect slow"}
        )
        task_id = started.json()["task_id"]
        await asyncio.wait_for(waiting.wait(), timeout=1)
        active = await asyncio.wait_for(client.get(f"/tasks/{task_id}"), timeout=0.2)
        assert active.json()["status"] == "running"
        cancelled = await asyncio.wait_for(
            client.delete(f"/tasks/{task_id}"), timeout=0.2
        )
        assert cancelled.status_code == 200
        view = await wait_for_terminal(client, task_id)

    assert view["status"] == "cancelled"
    assert view["execution_status"] == "cancelled"
    assert view["finish_source"] == "runtime_exit"
    assert view["business_outcome"] == "not_verified"
    assert view["cleanup_status"] == "complete"
    assert agents[0].github_client.http_client.is_closed
    assert agents[0].execution_trace.events[-1]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_agent_exception_is_sanitized_and_cleanup_completes():
    agents = []

    def make_agent(context):
        llm = object.__new__(LLM)
        llm.ask_tool = AsyncMock(side_effect=RuntimeError("private-marker"))
        agent = RepositoryInvestigationAgent.create(
            context,
            client=GitHubClient(
                http_client=httpx.AsyncClient(
                    base_url="https://api.github.com",
                    transport=httpx.MockTransport(
                        lambda _: httpx.Response(200, json={})
                    ),
                )
            ),
            llm=llm,
        )
        agents.append(agent)
        return agent

    app = create_app(agent_factory=make_agent, routing_top_k=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        started = await client.post(
            "/tasks", json={"repository": "octo/repo", "task": "Inspect"}
        )
        view = await wait_for_terminal(client, started.json()["task_id"])
    assert view["status"] == "failed"
    assert view["execution_status"] == "failure"
    assert view["finish_source"] == "runtime_exit"
    assert view["error"] == "agent_execution_failed"
    assert "private-marker" not in json.dumps(view)
    assert view["cleanup_status"] == "complete"
    assert agents[0].github_client.http_client.is_closed


@pytest.mark.asyncio
async def test_cancel_before_agent_start_needs_no_resource_cleanup():
    created = []
    app = create_app(agent_factory=lambda context: created.append(context))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        started = await client.post(
            "/tasks", json={"repository": "octo/repo", "task": "Inspect"}
        )
        stopped = await client.delete(f"/tasks/{started.json()['task_id']}")
    assert stopped.json()["status"] == "cancelled"
    assert stopped.json()["cleanup_status"] == "not_required"
    assert created == []


@pytest.mark.asyncio
async def test_service_shutdown_cancels_running_task_and_closes_client():
    waiting = asyncio.Event()
    agents = []

    def make_agent(context):
        async def ask_tool(**kwargs):
            waiting.set()
            await asyncio.Event().wait()

        llm = object.__new__(LLM)
        llm.ask_tool = AsyncMock(side_effect=ask_tool)
        agent = RepositoryInvestigationAgent.create(
            context,
            client=GitHubClient(
                http_client=httpx.AsyncClient(
                    base_url="https://api.github.com",
                    transport=httpx.MockTransport(
                        lambda _: httpx.Response(200, json={})
                    ),
                )
            ),
            llm=llm,
        )
        agents.append(agent)
        return agent

    app = create_app(agent_factory=make_agent, routing_top_k=None)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            started = await client.post(
                "/tasks", json={"repository": "octo/repo", "task": "Wait"}
            )
            task_id = started.json()["task_id"]
            await asyncio.wait_for(waiting.wait(), timeout=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        view = await client.get(f"/tasks/{task_id}")
    assert view.json()["status"] == "cancelled"
    assert view.json()["cleanup_status"] == "complete"
    assert agents[0].github_client.http_client.is_closed


@pytest.mark.asyncio
async def test_step_budget_without_finish_is_unknown_not_business_success():
    def make_agent(context):
        llm = object.__new__(LLM)
        llm.ask_tool = AsyncMock(
            return_value=SimpleNamespace(content="No tool evidence", tool_calls=[])
        )
        return RepositoryInvestigationAgent.create(
            context,
            client=GitHubClient(
                http_client=httpx.AsyncClient(
                    base_url="https://api.github.com",
                    transport=httpx.MockTransport(
                        lambda _: httpx.Response(200, json={})
                    ),
                )
            ),
            llm=llm,
            max_steps=1,
        )

    app = create_app(agent_factory=make_agent, routing_top_k=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        started = await client.post(
            "/tasks", json={"repository": "octo/repo", "task": "Inspect"}
        )
        view = await wait_for_terminal(client, started.json()["task_id"])
    assert view["status"] == "unknown"
    assert view["execution_status"] == "unknown"
    assert view["finish_source"] == "runtime_exit"
    assert view["business_outcome"] == "not_verified"
    assert view["cleanup_status"] == "complete"


@pytest.mark.asyncio
async def test_cleanup_timeout_prevents_finished_status():
    class HangingCloseClient(httpx.AsyncClient):
        async def aclose(self):
            await asyncio.Event().wait()

    github_http = HangingCloseClient(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
    )

    def make_agent(context):
        llm = object.__new__(LLM)
        llm.ask_tool = AsyncMock(
            return_value=SimpleNamespace(
                content="Done",
                tool_calls=[
                    ToolCall(
                        id="finish-1",
                        function=Function(
                            name="terminate", arguments='{"status":"success"}'
                        ),
                    )
                ],
            )
        )
        agent = RepositoryInvestigationAgent.create(
            context, client=GitHubClient(http_client=github_http), llm=llm, max_steps=1
        )
        agent.cleanup_timeout_seconds = 0.02
        return agent

    app = create_app(agent_factory=make_agent, routing_top_k=None)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            started = await client.post(
                "/tasks", json={"repository": "octo/repo", "task": "Finish"}
            )
            view = await wait_for_terminal(client, started.json()["task_id"])
    finally:
        await httpx.AsyncClient.aclose(github_http)
    assert view["status"] == "failed"
    assert view["execution_status"] == "success"
    assert view["finish_source"] == "agent_declaration"
    assert view["cleanup_status"] == "unknown"
    assert view["error"] == "cleanup_incomplete"
    assert view["business_outcome"] == "not_verified"


@pytest.mark.asyncio
async def test_lookup_and_cancel_reject_invalid_or_missing_task_ids():
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/tasks/not-an-id")).status_code == 422
        assert (await client.delete("/tasks/not-an-id")).status_code == 422
        missing = "00000000-0000-4000-8000-000000000000"
        assert (await client.get(f"/tasks/{missing}")).status_code == 404
        assert (await client.delete(f"/tasks/{missing}")).status_code == 404


@pytest.mark.asyncio
async def test_failed_github_observation_remains_visible_after_agent_declares_success():
    def make_agent(context):
        calls = 0

        async def ask_tool(**kwargs):
            nonlocal calls
            calls += 1
            name = "github_read_file" if calls == 1 else "terminate"
            arguments = (
                '{"path":"missing.md"}' if calls == 1 else '{"status":"success"}'
            )
            return SimpleNamespace(
                content=None,
                tool_calls=[
                    ToolCall(
                        id=f"call-{calls}",
                        function=Function(name=name, arguments=arguments),
                    )
                ],
            )

        llm = object.__new__(LLM)
        llm.ask_tool = AsyncMock(side_effect=ask_tool)
        return RepositoryInvestigationAgent.create(
            context,
            client=GitHubClient(
                http_client=httpx.AsyncClient(
                    base_url="https://api.github.com",
                    transport=httpx.MockTransport(
                        lambda _: httpx.Response(404, json={"message": "Not Found"})
                    ),
                )
            ),
            llm=llm,
            max_steps=3,
        )

    app = create_app(agent_factory=make_agent, routing_top_k=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        started = await client.post(
            "/tasks", json={"repository": "octo/repo", "task": "Read missing file"}
        )
        view = await wait_for_terminal(client, started.json()["task_id"])
    assert view["status"] == "finished"
    assert view["finish_source"] == "agent_declaration"
    assert view["business_outcome"] == "not_verified"
    assert view["failure_observation_count"] == 1
    assert "404" in view["result"]
