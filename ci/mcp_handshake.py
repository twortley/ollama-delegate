#!/usr/bin/env python3
"""
Start the server over stdio with the MCP SDK's own client, and check what a real
client sees: the nine default tools, and a server_info that carries the version.

    python ci/mcp_handshake.py

The selftest registers tools against a stand-in, so it never touches the SDK.
This does, which is what makes "both SDK generations are tolerated" an observed
fact rather than a reading of the source: CI runs it once per generation.

No Ollama is needed. OLLAMA_HOST points at a closed port; nothing here makes a
model call. Exits 1 and prints what differed.
"""
import asyncio
import os
import pathlib
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_TOOLS = {"server_info", "list_models", "show_model", "list_running",
                 "generate", "chat", "embed", "pull_model", "delete_model"}


async def run() -> int:
    env = {k: v for k, v in os.environ.items() if not k.startswith("OLLAMA_")}
    env["OLLAMA_HOST"] = "http://127.0.0.1:9"
    params = StdioServerParameters(command=sys.executable,
                                   args=[str(ROOT / "ollama_server.py")], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            info = await session.call_tool("server_info", {})
    text = " ".join(getattr(c, "text", "") for c in info.content)
    problems = []
    if tools != DEFAULT_TOOLS:
        problems.append(f"tools differ -- missing {sorted(DEFAULT_TOOLS - tools)}, "
                        f"unexpected {sorted(tools - DEFAULT_TOOLS)}")
    if '"version"' not in text:
        problems.append("server_info does not report a version")
    for p in problems:
        print(f"  FAIL  {p}")
    print(f"{len(tools)} tools enumerated over stdio; "
          f"{'OK' if not problems else f'{len(problems)} problem(s)'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
