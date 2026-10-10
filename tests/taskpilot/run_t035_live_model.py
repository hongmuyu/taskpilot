"""Opt-in real-model clarification against the disposable stdio MCP fixture.

Run with ``PYTHONPATH=. .venv/bin/python tests/taskpilot/run_t035_live_model.py``.
Uses the project's existing model configuration and writes sanitized evidence.
"""

import asyncio
import importlib.metadata
import json
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.agent.toolcall import ToolCallAgent
from app.config import config
from app.llm import LLM
from app.logger import logger
from app.schema import Function, Message, ToolCall, ToolChoice
from app.tool.mcp import MCPClients


SERVER = Path(__file__).parent / "fixtures" / "stdio_mcp_server.py"
EVIDENCE = Path(__file__).parents[2] / "docs" / "evidence" / "t035_real_mcp_e2e.json"


async def main() -> int:
    settings = config.llm["default"]
    # The model SDK and MCP tools may log raw responses; keep evidence allowlisted.
    logger.remove()
    llm = LLM(
        config_name="t035_live",
        llm_config={"default": settings, "t035_live": settings},
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
            alternative = clients.tool_map["mcp_fixture_legacy_read"]
            response = await llm.ask_tool.__wrapped__(
                llm,
                messages=[
                    Message.user_message(
                        "Use the MCP tool that echoes user supplied text. The user "
                        "has not supplied the text yet. Do not invent it; call the "
                        "appropriate tool now so the execution layer can ask the user."
                    )
                ],
                tools=[tool.to_param(), alternative.to_param()],
                tool_choice=ToolChoice.AUTO,
                timeout=30,
            )
            calls = response.tool_calls if response else None
            if not calls or len(calls) != 1:
                print("NOT VERIFIED: model did not return exactly one tool call")
                return 1
            model_command = ToolCall.model_validate(calls[0].model_dump())
            try:
                arguments = json.loads(model_command.function.arguments)
            except ValueError:
                print("NOT VERIFIED: model tool arguments were invalid JSON")
                return 1
            if model_command.function.name != tool.name or not isinstance(
                arguments, dict
            ):
                print("NOT VERIFIED: model did not select the expected MCP tool")
                return 1
            argument_keys = sorted(arguments)
            omission = "natural"
            if "text" in arguments:
                arguments.pop("text")
                omission = "controlled_injection_removed_text"
            command = ToolCall(
                id=model_command.id,
                function=Function(
                    name=model_command.function.name, arguments=json.dumps(arguments)
                ),
            )

            clarification = {"asked": False, "dispatch_before": None}

            async def answer(self, *, inquire):
                clarification["asked"] = True
                assert "text" in inquire
                clarification["dispatch_before"] = (
                    len(audit.read_text().splitlines()) if audit.exists() else 0
                )
                assert clarification["dispatch_before"] == 0
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
                and clarification["dispatch_before"] == 0
                and observation.startswith("Status: success\n")
                and len(entries) == 1
                and entries[0]["tool"] == "echo_read"
                and entries[0]["arguments"] == {"text": "confirmed by user"}
                and agent.memory.messages[-1].tool_call_id == command.id
                and agent.tool_call_sources[command.id]["text"] == "user_clarification"
            )
            evidence = {
                "result": "PASS" if passed else "FAIL",
                "run_date": date.today().isoformat(),
                "model": llm.model,
                "model_revision": "NOT AVAILABLE",
                "mcp_sdk_version": importlib.metadata.version("mcp"),
                "server": "FastMCP disposable read-only fixture",
                "transport": "stdio",
                "model_selected_tool": model_command.function.name,
                "model_argument_keys": argument_keys,
                "missing_parameter_mode": omission,
                "user_reply": "scripted AskHuman response",
                "mcp_tool": tool.name,
                "original_tool_name": tool.original_name,
                "tool_call_id_preserved": (
                    agent.memory.messages[-1].tool_call_id == command.id
                ),
                "clarification_triggered": clarification["asked"],
                "clarification_required_field": "text",
                "dispatch_before_clarification": clarification["dispatch_before"],
                "server_dispatch_count": len(entries),
                "parameter_source": agent.tool_call_sources[command.id].get("text"),
                "revalidation_and_dispatch_succeeded": passed,
                "final_status": "success" if passed else "failure",
            }
            if passed:
                EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
                EVIDENCE.write_text(
                    json.dumps(evidence, indent=2, sort_keys=True) + "\n"
                )
            print(json.dumps(evidence, sort_keys=True))
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
