"""MCP read fixtures with only a per-test dispatch audit file."""

import asyncio
import json
import os
import time

from mcp.server.fastmcp import FastMCP


server = FastMCP("taskpilot-stdio-fixture")
audit_path = os.environ["TASKPILOT_MCP_AUDIT_PATH"]
variant = os.environ.get("TASKPILOT_MCP_VARIANT", "v1")


def record(name: str, arguments: dict) -> None:
    with open(audit_path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"tool": name, "arguments": arguments}) + "\n")


if variant == "v1":

    @server.tool(name="echo_read")
    def echo_read_v1(text: str) -> str:
        """Echo text without changing external state."""
        record("echo_read", {"text": text})
        return f"echo:{text}"

    @server.tool(name="legacy_read")
    def legacy_read() -> str:
        """Return the original fixture version."""
        record("legacy_read", {})
        return "v1"

else:

    @server.tool(name="echo_read")
    def echo_read_v2(text: str, suffix: str) -> str:
        """Echo text and suffix without changing external state."""
        record("echo_read", {"text": text, "suffix": suffix})
        return f"echo:{text}:{suffix}"

    @server.tool(name="new_read")
    def new_read() -> str:
        """Return the updated fixture version."""
        record("new_read", {})
        return "v2"


@server.tool(name="fail_read")
def fail_read() -> str:
    """Return a controlled MCP failure."""
    record("fail_read", {})
    raise ValueError("controlled fixture failure")


@server.tool(name="server_pid")
def server_pid() -> str:
    """Expose this disposable fixture process ID for cleanup testing."""
    return str(os.getpid())


@server.tool(name="slow_read")
async def slow_read(wait_ms: int) -> str:
    """Delay a read response for deadline validation."""
    record("slow_read", {"wait_ms": wait_ms})
    await asyncio.sleep(wait_ms / 1000)
    return "slow read completed"


if __name__ == "__main__":
    time.sleep(int(os.environ.get("TASKPILOT_MCP_STARTUP_DELAY_MS", "0")) / 1000)
    server.run(transport="stdio")
