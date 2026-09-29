"""Opt-in real-provider smoke runs; not collected by pytest.

Run from repository root: PYTHONPATH=. .venv/bin/python tests/baseline/run_live.py tool
Only baseline PlanningTool (in-memory state) and Terminate are exposed.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


os.environ["OPENMANUS_DISABLE_BROWSER_USE"] = "1"

from app.agent.manus import Manus
from app.config import config
from app.logger import logger
from app.schema import ToolChoice
from app.tool import PlanningTool, Terminate, ToolCollection


TASKS = {
    "plain": ["用一句话说明什么是 MCP。"],
    "tool": [
        "请实际调用 planning 工具，command=create，plan_id=baseline-pr01，"
        "title=Baseline check，steps=[Read evidence, Report result]。"
        "收到工具结果后简要报告，再调用 terminate(status=success)。"
    ],
    "failure": [
        "这是安全的错误行为测试。请实际调用 planning(command=get, "
        "plan_id=baseline-missing-pr01)，该计划不存在。不要创建或修复计划。"
        "收到错误后说明观察到的错误，然后调用 terminate(status=failure)。"
    ],
    "terminate": ["立即调用 terminate(status=failure) 结束本次验证。"],
    "consecutive": [
        "这是第一轮，请记住标记 baseline-round-one，然后调用 terminate(status=success)。",
        "这是同一实例第二轮，请说出上一轮标记，再调用 terminate(status=success)。",
    ],
}


def snapshot(agent):
    return {
        "current_step": agent.current_step,
        "state": agent.state.value,
        "initialized": agent._initialized,
        "connected_servers": list(agent.connected_servers),
        "messages": [m.model_dump(exclude_none=True) for m in agent.messages],
        "input_tokens_cumulative": agent.llm.total_input_tokens,
        "output_tokens_cumulative": agent.llm.total_completion_tokens,
    }


async def run(case, disable_thinking=False):
    if config.mcp_config.servers or config.sandbox.use_sandbox:
        raise RuntimeError(
            "Baseline smoke requires no configured MCP and use_sandbox=false"
        )
    llm_config = config.llm["default"]
    if not llm_config.api_key or llm_config.api_key == "YOUR_API_KEY":
        raise RuntimeError(
            "Fill in the ignored config/config.toml locally before live runs"
        )

    logger.remove()
    logger.add(sys.stderr, level="INFO", diagnose=False, backtrace=False)
    agent = await Manus.create(
        available_tools=ToolCollection(PlanningTool(), Terminate()),
        max_steps=1 if case == "plain" else 6,
        tool_choices=ToolChoice.NONE if case == "plain" else ToolChoice.AUTO,
    )
    if disable_thinking:
        ask_tool = agent.llm.ask_tool

        async def ask_tool_without_reasoning(**kwargs):
            return await ask_tool(
                **kwargs, extra_body={"thinking": {"type": "disabled"}}
            )

        agent.llm.ask_tool = ask_tool_without_reasoning
    evidence = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "baseline": "3309bf4e416fb1c74b008f3e86494439a31bad53",
        "case": case,
        "model": llm_config.model,
        "base_url": llm_config.base_url,
        "tool_pool": list(agent.available_tools.tool_map),
        "tool_choice": getattr(agent.tool_choices, "value", agent.tool_choices),
        "max_steps": agent.max_steps,
        "provider_request_override": (
            {"thinking": "disabled"} if disable_thinking else None
        ),
        "mcp": "disabled; lifecycle of real connection NOT VERIFIED",
        "runs": [],
    }
    failed = False
    try:
        for task in TASKS[case]:
            record = {"task": task, "before": snapshot(agent)}
            evidence["runs"].append(record)
            try:
                # Limit only this smoke command's duration, not production runtime.
                record["run_return"] = await asyncio.wait_for(
                    agent.run(task), timeout=240
                )
            except Exception as exc:
                failed = True
                # Never serialize exception frames, provider configuration, or keys.
                detail = str(exc).replace(llm_config.api_key, "[REDACTED]")
                record["error"] = {"type": type(exc).__name__, "message": detail}
                break
            finally:
                record["after"] = snapshot(agent)
    finally:
        await agent.cleanup()
        await agent.llm.client.close()
        output = Path("logs/baseline") / f"live-{case}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(evidence, ensure_ascii=False, indent=2, default=str)
        output.write_text(encoded.replace(llm_config.api_key, "[REDACTED]") + "\n")
        print(f"Evidence: {output}; execution_error={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=TASKS)
    parser.add_argument(
        "--disable-thinking",
        action="store_true",
        help="Use DeepSeek's supported non-thinking Chat Completions mode",
    )
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(run(args.case, disable_thinking=args.disable_thinking))
    )
