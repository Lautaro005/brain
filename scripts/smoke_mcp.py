"""Chequeo de humo para el CI: arranca server.py por stdio (como lo haría Claude) y lista las tools. Después
lo arranca por HTTP (server.py --http, el del acceso remoto) y prueba que sin el token responda 404 y con
el token funcione igual."""
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTTP_PORT = 8779  # no el 8770 de uso normal, por si hay un brain corriendo


async def stdio() -> list[str]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
        await s.initialize()
        return [t.name for t in (await s.list_tools()).tools]


async def http(url: str) -> list[str]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read, write, *_), ClientSession(read, write) as s:
        await s.initialize()
        return [t.name for t in (await s.list_tools()).tools]


def smoke_http() -> int:
    env = {**os.environ, "BRAIN_REMOTE": "1", "BRAIN_REMOTE_PORT": str(HTTP_PORT)}
    proc = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--http"], cwd=ROOT, env=env)
    try:
        for _ in range(120):  # hasta 60 s: el primer arranque en Windows es lento
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{HTTP_PORT}/", timeout=1)
            except urllib.error.HTTPError:
                break  # respondió (404 sin token): está arriba
            except OSError:
                time.sleep(0.5)
        else:
            print("server.py --http no arrancó")
            return 1
        token = json.loads((ROOT / "data/remote.json").read_text())["token"]
        req = urllib.request.Request(f"http://127.0.0.1:{HTTP_PORT}/mcp", data=b"{}", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            print("server.py --http respondió sin token")
            return 1
        except urllib.error.HTTPError as e:
            if e.code != 404:
                print(f"sin token esperaba 404 y dio {e.code}")
                return 1
        tools = asyncio.run(http(f"http://127.0.0.1:{HTTP_PORT}/{token}/mcp"))
        print(f"server.py --http OK: {len(tools)} tools con el token, 404 sin él")
        return 0 if "read_file" in tools else 1
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def main() -> int:
    tools = asyncio.run(stdio())
    print(f"server.py OK: {len(tools)} tools")
    missing = {"read_file", "search_knowledge", "propose_connection", "refresh_sources"} - set(tools)
    if missing:
        print(f"faltan tools: {sorted(missing)}")
        return 1
    return smoke_http()


if __name__ == "__main__":
    sys.exit(main())
