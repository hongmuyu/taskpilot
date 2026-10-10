"""T057 service clarification lifecycle tests."""

import asyncio
import base64
import gc
import math
import weakref
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


async def wait_for_status(client, task_id, expected):
    for _ in range(100):
        view = (await client.get(f"/tasks/{task_id}")).json()
        if view["status"] == expected:
            return view
        await asyncio.sleep(0.01)
    raise AssertionError(f"Task did not enter {expected}")


async def wait_for_view(client, task_id, predicate):
    for _ in range(100):
        view = (await client.get(f"/tasks/{task_id}")).json()
        if predicate(view):
            return view
        await asyncio.sleep(0.01)
    raise AssertionError("Task did not reach the expected state")


def read_service(*, timeout=1.0):
    agents = {}
    requests = []
    llm_calls = {}

    def make_agent(context):
        call_count = 0

        async def select_tool(**kwargs):
            nonlocal call_count
            call_count += 1
            llm_calls[context.repo] = call_count
            name = "github_read_file" if call_count == 1 else "terminate"
            arguments = "{}" if call_count == 1 else '{"status":"success"}'
            return SimpleNamespace(
                content=None,
                tool_calls=[
                    ToolCall(
                        id=f"{context.repo}-{call_count}",
                        function=Function(name=name, arguments=arguments),
                    )
                ],
            )

        def respond(request):
            requests.append(request.url.path)
            return httpx.Response(
                200,
                json={
                    "path": "README.md",
                    "content": base64.b64encode(b"Repository evidence").decode(),
                },
            )

        llm = object.__new__(LLM)
        llm.ask_tool = AsyncMock(side_effect=select_tool)
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
        agents[context.repo] = agent
        return agent

    return (
        create_app(
            agent_factory=make_agent,
            routing_top_k=None,
            clarification_timeout_seconds=timeout,
        ),
        agents,
        requests,
        llm_calls,
    )


@pytest.mark.parametrize("timeout", [0, -1, math.inf, math.nan])
def test_clarification_timeout_configuration_must_be_finite_and_positive(timeout):
    with pytest.raises(ValueError):
        create_app(clarification_timeout_seconds=timeout)


@pytest.mark.asyncio
async def test_pending_reply_revalidates_and_executes_original_call_once():
    requests = []
    agents = []
    llm_calls = []

    def make_agent(context):
        async def select_tool(**kwargs):
            llm_calls.append(kwargs)
            if len(llm_calls) == 1:
                return SimpleNamespace(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="original-read-call",
                            function=Function(name="github_read_file", arguments="{}"),
                        )
                    ],
                )
            return SimpleNamespace(
                content="Read completed",
                tool_calls=[
                    ToolCall(
                        id="finish-call",
                        function=Function(
                            name="terminate", arguments='{"status":"success"}'
                        ),
                    )
                ],
            )

        def respond(request):
            requests.append(request.url.path)
            return httpx.Response(
                200,
                json={
                    "path": "README.md",
                    "html_url": "https://github.com/octo/repo/blob/main/README.md",
                    "content": base64.b64encode(b"Repository evidence").decode(),
                },
            )

        llm = object.__new__(LLM)
        llm.ask_tool = AsyncMock(side_effect=select_tool)
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
            "/tasks", json={"repository": "octo/repo", "task": "Read the file"}
        )
        task_id = started.json()["task_id"]
        pending = await wait_for_status(client, task_id, "clarification_pending")
        assert pending["clarification"] == {
            "tool_call_id": "original-read-call",
            "tool_name": "github_read_file",
            "missing_fields": ["path"],
        }
        assert requests == []
        assert len(llm_calls) == 1
        response = await client.post(
            f"/tasks/{task_id}/clarifications/original-read-call/reply",
            json={"reply": '{"path":"README.md"}'},
        )
        assert response.status_code == 202
        finished = await wait_for_status(client, task_id, "finished")

    assert finished["business_outcome"] == "not_verified"
    assert finished["cleanup_status"] == "complete"
    assert requests == ["/repos/octo/repo/contents/README.md"]
    assert len(llm_calls) == 2
    assert agents[0].github_client.http_client.is_closed
    observations = [
        message
        for message in agents[0].memory.messages
        if message.tool_call_id == "original-read-call"
    ]
    assert len(observations) == 1
    assert "Repository evidence" in observations[0].content
    assert any(
        message.tool_call_id == "original-read-call"
        for message in llm_calls[1]["messages"]
        if message.role == "tool"
    )


@pytest.mark.asyncio
async def test_invalid_and_incomplete_replies_stay_pending_without_dispatch():
    app, agents, requests, llm_calls = read_service()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        started = await client.post(
            "/tasks", json={"repository": "octo/invalid", "task": "Read a file"}
        )
        task_id = started.json()["task_id"]
        await wait_for_status(client, task_id, "clarification_pending")
        endpoint = f"/tasks/{task_id}/clarifications/invalid-1/reply"
        assert (await client.post(endpoint, json={"reply": "  "})).status_code == 422
        assert (
            await client.post(endpoint, json={"reply": '{"other":"x"}'})
        ).status_code == 202
        await wait_for_view(
            client, task_id, lambda view: view["error"] == "clarification_still_pending"
        )
        assert requests == []
        assert llm_calls == {"invalid": 1}
        assert (
            await client.post(endpoint, json={"reply": '{"path":7}'})
        ).status_code == 202
        await wait_for_view(
            client, task_id, lambda view: view["error"] == "clarification_still_pending"
        )
        assert requests == []
        assert (
            await client.post(endpoint, json={"reply": '{"path":"README.md"}'})
        ).status_code == 202
        await wait_for_status(client, task_id, "finished")
    assert requests == ["/repos/octo/invalid/contents/README.md"]
    assert agents["invalid"].github_client.http_client.is_closed


@pytest.mark.asyncio
async def test_parallel_tasks_cannot_receive_each_others_replies():
    app, agents, requests, llm_calls = read_service()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post(
            "/tasks", json={"repository": "octo/first", "task": "Read first"}
        )
        second = await client.post(
            "/tasks", json={"repository": "octo/second", "task": "Read second"}
        )
        first_id, second_id = first.json()["task_id"], second.json()["task_id"]
        await asyncio.gather(
            wait_for_status(client, first_id, "clarification_pending"),
            wait_for_status(client, second_id, "clarification_pending"),
        )
        wrong = await client.post(
            f"/tasks/{first_id}/clarifications/second-1/reply",
            json={"reply": '{"path":"wrong.md"}'},
        )
        assert wrong.status_code == 409
        assert requests == []
        assert llm_calls == {"first": 1, "second": 1}
        assert (
            await client.post(
                f"/tasks/{first_id}/clarifications/first-1/reply",
                json={"reply": '{"path":"README.md"}'},
            )
        ).status_code == 202
        await wait_for_status(client, first_id, "finished")
        assert (await client.get(f"/tasks/{second_id}")).json()["status"] == (
            "clarification_pending"
        )
        assert requests == ["/repos/octo/first/contents/README.md"]
        assert (
            await client.post(
                f"/tasks/{second_id}/clarifications/second-1/reply",
                json={"reply": '{"path":"README.md"}'},
            )
        ).status_code == 202
        await wait_for_status(client, second_id, "finished")
    assert requests == [
        "/repos/octo/first/contents/README.md",
        "/repos/octo/second/contents/README.md",
    ]
    assert agents["first"] is not agents["second"]


@pytest.mark.asyncio
async def test_duplicate_and_late_replies_never_dispatch_twice():
    app, _, requests, _ = read_service()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        started = await client.post(
            "/tasks", json={"repository": "octo/repo", "task": "Read file"}
        )
        task_id = started.json()["task_id"]
        await wait_for_status(client, task_id, "clarification_pending")
        endpoint = f"/tasks/{task_id}/clarifications/repo-1/reply"
        assert (
            await client.post(endpoint, json={"reply": '{"path":"README.md"}'})
        ).status_code == 202
        assert (
            await client.post(endpoint, json={"reply": '{"path":"other.md"}'})
        ).status_code == 409
        await wait_for_status(client, task_id, "finished")
        assert (
            await client.post(endpoint, json={"reply": '{"path":"late.md"}'})
        ).status_code == 409
    assert requests == ["/repos/octo/repo/contents/README.md"]


@pytest.mark.asyncio
async def test_cancel_pending_task_and_reject_late_reply():
    app, agents, requests, _ = read_service()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        started = await client.post(
            "/tasks", json={"repository": "octo/repo", "task": "Read file"}
        )
        task_id = started.json()["task_id"]
        await wait_for_status(client, task_id, "clarification_pending")
        stopped = await client.delete(f"/tasks/{task_id}")
        assert stopped.json()["status"] == "cancelling"
        cancelled = await wait_for_status(client, task_id, "cancelled")
        assert cancelled["cleanup_status"] == "complete"
        assert (
            await client.post(
                f"/tasks/{task_id}/clarifications/repo-1/reply",
                json={"reply": '{"path":"late.md"}'},
            )
        ).status_code == 409
    assert requests == []
    assert agents["repo"].github_client.http_client.is_closed


@pytest.mark.asyncio
async def test_cancel_reply_ends_task_without_new_model_or_tool_call():
    app, agents, requests, llm_calls = read_service()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        started = await client.post(
            "/tasks", json={"repository": "octo/repo", "task": "Read file"}
        )
        task_id = started.json()["task_id"]
        await wait_for_status(client, task_id, "clarification_pending")
        endpoint = f"/tasks/{task_id}/clarifications/repo-1/reply"
        assert (
            await client.post(endpoint, json={"reply": "cancel"})
        ).status_code == 202
        cancelled = await wait_for_status(client, task_id, "cancelled")
        assert cancelled["execution_status"] == "cancelled"
        assert (
            await client.post(endpoint, json={"reply": '{"path":"late.md"}'})
        ).status_code == 409
    assert requests == []
    assert llm_calls == {"repo": 1}
    assert agents["repo"].github_client.http_client.is_closed


@pytest.mark.asyncio
async def test_clarification_timeout_is_terminal_and_never_dispatches():
    app, agents, requests, _ = read_service(timeout=0.02)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        started = await client.post(
            "/tasks", json={"repository": "octo/repo", "task": "Read file"}
        )
        task_id = started.json()["task_id"]
        timed_out = await wait_for_status(client, task_id, "clarification_timeout")
        assert timed_out["execution_status"] == "unknown"
        assert timed_out["cleanup_status"] == "complete"
        assert (
            await client.post(
                f"/tasks/{task_id}/clarifications/repo-1/reply",
                json={"reply": '{"path":"late.md"}'},
            )
        ).status_code == 409
    assert requests == []
    assert agents["repo"].github_client.http_client.is_closed


@pytest.mark.asyncio
async def test_parallel_tool_calls_close_all_messages_before_next_llm_request():
    requests = []
    llm_message_snapshots = []

    def make_agent(context):
        async def select_tool(**kwargs):
            llm_message_snapshots.append(
                [(item.role, item.tool_call_id) for item in kwargs["messages"]]
            )
            if len(llm_message_snapshots) == 1:
                return SimpleNamespace(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="needs-path",
                            function=Function(name="github_read_file", arguments="{}"),
                        ),
                        ToolCall(
                            id="has-context",
                            function=Function(
                                name="github_repository_info", arguments="{}"
                            ),
                        ),
                    ],
                )
            return SimpleNamespace(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="finish",
                        function=Function(
                            name="terminate", arguments='{"status":"success"}'
                        ),
                    )
                ],
            )

        def respond(request):
            requests.append(request.url.path)
            if request.url.path.endswith("/README.md"):
                return httpx.Response(
                    200,
                    json={
                        "path": "README.md",
                        "content": base64.b64encode(b"Repository evidence").decode(),
                    },
                )
            return httpx.Response(
                200, json={"full_name": "octo/repo", "language": "Python"}
            )

        llm = object.__new__(LLM)
        llm.ask_tool = AsyncMock(side_effect=select_tool)
        return RepositoryInvestigationAgent.create(
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

    app = create_app(agent_factory=make_agent, routing_top_k=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        started = await client.post(
            "/tasks", json={"repository": "octo/repo", "task": "Inspect"}
        )
        task_id = started.json()["task_id"]
        pending = await wait_for_status(client, task_id, "clarification_pending")
        assert pending["clarification"]["tool_call_id"] == "needs-path"
        assert requests == []
        assert len(llm_message_snapshots) == 1
        status = await asyncio.wait_for(client.get(f"/tasks/{task_id}"), 0.2)
        assert status.json()["status"] == "clarification_pending"
        assert (
            await client.post(
                f"/tasks/{task_id}/clarifications/needs-path/reply",
                json={"reply": '{"path":"README.md"}'},
            )
        ).status_code == 202
        await wait_for_status(client, task_id, "finished")

    assert requests == [
        "/repos/octo/repo/contents/README.md",
        "/repos/octo/repo",
    ]
    assert len(llm_message_snapshots) == 2
    assert ("tool", "needs-path") in llm_message_snapshots[1]
    assert ("tool", "has-context") in llm_message_snapshots[1]


@pytest.mark.asyncio
async def test_finished_task_releases_its_agent_after_cleanup():
    app, agents, _, _ = read_service()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        started = await client.post(
            "/tasks", json={"repository": "octo/repo", "task": "Read file"}
        )
        task_id = started.json()["task_id"]
        await wait_for_status(client, task_id, "clarification_pending")
        assert (
            await client.post(
                f"/tasks/{task_id}/clarifications/repo-1/reply",
                json={"reply": '{"path":"README.md"}'},
            )
        ).status_code == 202
        await wait_for_status(client, task_id, "finished")
    agent_ref = weakref.ref(agents.pop("repo"))
    gc.collect()
    assert agent_ref() is None
