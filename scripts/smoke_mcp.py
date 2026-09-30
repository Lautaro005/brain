"""Chequeo de humo para el CI: arranca server.py por stdio (como lo haría Claude) y lista las tools."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
        await s.initialize()
        tools = [t.name for t in (await s.list_tools()).tools]
    print(f"server.py OK: {len(tools)} tools")
    missing = {"read_file", "search_knowledge", "propose_connection", "refresh_sources"} - set(tools)
    if missing:
        print(f"faltan tools: {sorted(missing)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
