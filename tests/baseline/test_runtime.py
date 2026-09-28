"""Offline characterization: scripted LLM, real baseline Agent and local tools.

These tests do not prove provider connectivity or model tool selection.
No private config is created, overwritten, or sent to a provider.
"""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# Config is eagerly constructed on import. Supply test data without editing the
# user's config file; the existing config.example.toml provides the openable path.
with patch(
    "tomllib.load",
    return_value={
        "llm": {"model": "test", "base_url": "http://127.0.0.1:9/v1", "api_key": "test"},
        "daytona": {"daytona_api_key": "unused-test"},
    },
):
    from app.agent.manus import Manus
    from app.config import config
    from app.llm import LLM
    from app.schema import AgentState, Function, ToolCall
    from app.tool import StrReplaceEditor, Terminate, ToolCollection


def call(name, arguments, call_id):
    return ToolCall(
        id=call_id,
        function=Function(name=name, arguments=json.dumps(arguments)),
    )


def reply(content=None, *calls):
    return SimpleNamespace(content=content, tool_calls=list(calls))


@pytest.fixture
def make_agent(monkeypatch):
    monkeypatch.setenv("OPENMANUS_DISABLE_BROWSER_USE", "1")
    monkeypatch.setattr(config.mcp_config, "servers", {})
    monkeypatch.setattr(config.sandbox, "use_sandbox", False)
    connect = AsyncMock(side_effect=AssertionError("Offline test attempted MCP connection"))
    monkeypatch.setattr(Manus, "connect_mcp_server", connect)

    async def build(responses, max_steps=5):
        pending = iter(responses)
        requests = []

        async def ask_tool(**kwargs):
            requests.append(copy.deepcopy(kwargs))
            response = next(pending)
            if isinstance(response, Exception):
                raise response
            return response

        # Bypass only provider/tokenizer initialization; BaseAgent still receives
        # an LLM instance, so its normal validation and loop remain in use.
        llm = object.__new__(LLM)
        llm.ask_tool = AsyncMock(side_effect=ask_tool)
        agent = await Manus.create(
            llm=llm,
            available_tools=ToolCollection(StrReplaceEditor(), Terminate()),
            next_step_prompt="",
            max_steps=max_steps,
        )
        return agent, requests

    return build


@pytest.mark.asyncio
async def test_plain_text_keeps_running_until_step_limit(make_agent):
    agent, requests = await make_agent([reply("first"), reply("second")], max_steps=2)

    result = await agent.run("plain task")

    assert result == "Step 1: first\nStep 2: second\nTerminated: Reached max steps (2)"
    assert len(requests) == 2
    assert [m.content for m in agent.messages] == ["plain task", "first", "second"]
    assert agent.current_step == 0
    assert agent.state == AgentState.IDLE
    assert agent._initialized is False


@pytest.mark.asyncio
async def test_real_file_read_is_dispatched_and_observed_by_next_step(make_agent, tmp_path):
    target = tmp_path / "evidence.txt"
    target.write_text("baseline-evidence-7319\n")
    agent, requests = await make_agent([
        reply(None, call("str_replace_editor", {"command": "view", "path": str(target)}, "read")),
        reply(None, call("terminate", {"status": "success"}, "done")),
    ])

    result = await agent.run("read the evidence file")

    observation = next(m for m in requests[1]["messages"] if m.tool_call_id == "read")
    assert observation.role == "tool"
    assert "baseline-evidence-7319" in observation.content
    assert "Observed output of cmd `str_replace_editor`" in result
    assert {t["function"]["name"] for t in requests[0]["tools"]} == {
        "str_replace_editor", "terminate"
    }
    assert agent.state == AgentState.IDLE
    assert target.read_text() == "baseline-evidence-7319\n"


@pytest.mark.asyncio
async def test_tool_error_becomes_observation_and_agent_continues(make_agent, tmp_path):
    missing = tmp_path / "missing.txt"
    agent, requests = await make_agent([
        reply(None, call("str_replace_editor", {"command": "view", "path": str(missing)}, "bad-read")),
        reply(None, call("terminate", {"status": "failure"}, "done")),
    ])

    result = await agent.run("read a missing file")

    observation = next(m for m in requests[1]["messages"] if m.tool_call_id == "bad-read")
    assert f"Error: The path {missing} does not exist" in observation.content
    assert "status: failure" in result
    assert len(requests) == 2
    assert agent.state == AgentState.IDLE
    assert not missing.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["success", "failure"])
async def test_terminate_stops_run_regardless_of_status(make_agent, status):
    agent, requests = await make_agent([
        reply(None, call("terminate", {"status": status}, "done")),
    ])

    result = await agent.run("finish")

    assert result == (
        "Step 1: Observed output of cmd `terminate` executed:\n"
        f"The interaction has been completed with status: {status}"
    )
    assert len(requests) == 1
    assert agent.current_step == 1
    assert agent.state == AgentState.IDLE
    assert agent._initialized is False


@pytest.mark.asyncio
async def test_terminate_does_not_skip_remaining_calls_in_same_batch(make_agent, tmp_path):
    target = tmp_path / "after-terminate.txt"
    target.write_text("still executed")
    agent, requests = await make_agent([
        reply(
            None,
            call("terminate", {"status": "failure"}, "stop"),
            call("str_replace_editor", {"command": "view", "path": str(target)}, "after"),
        ),
    ])

    result = await agent.run("batch completion")

    assert len(requests) == 1
    assert "still executed" in result
    assert [m.tool_call_id for m in agent.messages if m.role == "tool"] == ["stop", "after"]


@pytest.mark.asyncio
async def test_consecutive_runs_preserve_memory_and_step_counter(make_agent):
    agent, requests = await make_agent([
        reply(None, call("terminate", {"status": "success"}, "first")),
        reply(None, call("terminate", {"status": "success"}, "second")),
    ])

    first = await agent.run("task one")
    first_messages = copy.deepcopy(agent.messages)
    assert agent.current_step == 1
    assert agent._initialized is False
    second = await agent.run("task two")

    assert first.startswith("Step 1:")
    assert second.startswith("Step 2:")
    assert agent.current_step == 2
    assert agent.messages[:len(first_messages)] == first_messages
    assert [m.content for m in requests[1]["messages"] if m.role == "user"] == [
        "task one", "task two"
    ]
    assert agent.state == AgentState.IDLE
    assert agent._initialized is False
    assert agent.connected_servers == {}


@pytest.mark.asyncio
async def test_llm_exception_propagates_but_manus_cleanup_runs(make_agent):
    agent, _ = await make_agent([RuntimeError("scripted provider failure")])

    with pytest.raises(RuntimeError, match="scripted provider failure"):
        await agent.run("fail")

    assert agent.state == AgentState.IDLE
    assert agent.current_step == 1
    assert agent._initialized is False


@pytest.mark.asyncio
async def test_cli_logs_completion_but_does_not_print_run_return(make_agent, monkeypatch, capsys):
    import main as cli

    agent, _ = await make_agent([
        reply(None, call("terminate", {"status": "success"}, "done")),
    ])
    monkeypatch.setattr(cli.Manus, "create", AsyncMock(return_value=agent))
    monkeypatch.setattr("sys.argv", ["main.py", "--prompt", "finish"])
    logs = []
    monkeypatch.setattr(cli.logger, "info", logs.append)

    returned = await cli.main()

    assert returned is None
    assert "Request processing completed." in logs
    assert "Step 1:" not in capsys.readouterr().out
    assert agent.messages[-1].tool_call_id == "done"
    assert agent._initialized is False
