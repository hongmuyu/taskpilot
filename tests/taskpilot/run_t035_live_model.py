"""Opt-in real-model clarification against the disposable stdio MCP fixture.

Run with ``PYTHONPATH=. .venv/bin/python tests/taskpilot/run_t035_live_model.py``.
Supply DEEPSEEK_API_KEY in the environment, never as a command argument.
"""

import asyncio
import hmac
import importlib.metadata
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.agent.toolcall import ToolCallAgent
from app.config import config
from app.llm import LLM
from app.logger import logger
from app.schema import Message, ToolCall, ToolChoice
from app.tool.mcp import MCPClients


SERVER = Path(__file__).parent / "fixtures" / "stdio_mcp_server.py"


async def main() -> int:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        print("BLOCKED: DEEPSEEK_API_KEY environment variable is absent")
        return 2
    settings = config.llm["default"]
    if hmac.compare_digest(key, settings.api_key):
        print("BLOCKED: environment credential must differ from local config")
        return 2

    # Use only the environment credential and suppress raw model/tool logs.
    logger.remove()
    live_settings = settings.model_copy(update={"api_key": key, "temperature": 0.0})
    llm = LLM(
        config_name="t035_live",
        llm_config={"default": live_settings, "t035_live": live_settings},
    )
    llm.client = llm.client.with_options(max_retries=0)

    with tempfile.TemporaryDirectory(prefix="taskpilot-t035-") as directory:
        audit = Path(directory) / "calls.jsonl"
        clients = MCPClients(connect_timeout_seconds=5, cleanup_timeout_seconds=2)
        try:
            await clients.connect_stdio(
                sys.executable,
                [str(SERVER)],
                server_id="fixture",
                env={
                    "TASKPILOT_MCP_AUDIT_PATH": str(audit),
                    "TASKPILOT_MCP_VARIANT": "v1",
                },
            )
            tool = clients.tool_map["mcp_fixture_echo_read"]
            response = await llm.ask_tool.__wrapped__(
                llm,
                messages=[
                    Message.user_message(
                        "Integration check: select echo_read now, but the text to "
                        "echo has not been provided. Call the tool with an empty "
                        "JSON object. Do not invent a text value; the execution "
                        "layer will ask the user."
                    )
                ],
                tools=[tool.to_param()],
                tool_choice=ToolChoice.REQUIRED,
                temperature=0.0,
                timeout=30,
            )
            calls = response.tool_calls if response else None
            if not calls or len(calls) != 1:
                print("NOT VERIFIED: model did not return exactly one tool call")
                return 1
            command = ToolCall.model_validate(calls[0].model_dump())
            try:
                arguments = json.loads(command.function.arguments)
            except ValueError:
                print("NOT VERIFIED: model tool arguments were invalid JSON")
                return 1
            if command.function.name != tool.name or arguments != {}:
                print(
                    "NOT VERIFIED: model did not select the expected MCP tool "
                    "with missing parameters"
                )
                return 1

            clarification = {"asked": False}

            async def answer(self, *, inquire):
                clarification["asked"] = True
                assert "text" in inquire
                assert not audit.exists()
                return json.dumps({"text": "confirmed by user"})

            agent = ToolCallAgent(available_tools=clients, llm=llm)
            agent.tool_calls = [command]
            with patch("app.agent.toolcall.AskHuman.execute", new=answer):
                observation = await agent.act()
            entries = (
                [json.loads(line) for line in audit.read_text().splitlines()]
                if audit.exists()
                else []
            )
            passed = (
                clarification["asked"]
                and observation.startswith("Status: success\n")
                and len(entries) == 1
                and entries[0]["tool"] == "echo_read"
                and entries[0]["arguments"] == {"text": "confirmed by user"}
                and agent.memory.messages[-1].tool_call_id == command.id
            )
            print(
                json.dumps(
                    {
                        "result": "PASS" if passed else "FAIL",
                        "model": llm.model,
                        "model_revision": "NOT AVAILABLE",
                        "mcp_sdk_version": importlib.metadata.version("mcp"),
                        "transport": "stdio",
                        "model_omission": "prompted",
                        "user_reply": "scripted",
                        "mcp_tool": tool.name,
                        "original_tool_name": tool.original_name,
                        "tool_call_id_preserved": (
                            agent.memory.messages[-1].tool_call_id == command.id
                        ),
                        "clarification_triggered": clarification["asked"],
                        "server_dispatch_count": len(entries),
                        "final_status": "success" if passed else "failure",
                    },
                    sort_keys=True,
                )
            )
            return 0 if passed else 1
        finally:
            await clients.disconnect()
            await llm.client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        print(f"NOT VERIFIED: live model run failed ({type(exc).__name__})")
        raise SystemExit(1) from None
