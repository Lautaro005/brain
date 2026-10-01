"""v0.03: acceso remoto por URL, agentes nuevos, skills en el chat, contexto fijo, actividad y actualizar."""
import asyncio
import json
import threading
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

import pytest


# ---------- acceso remoto: token, URL y solo lectura ----------

async def _echo(scope, receive, send):
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": scope["path"].encode()})


def _call(app, path, headers=None):
    import httpx

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://x") as c:
            r = await c.post(path, headers=headers or {})
            return r.status_code, r.text
    return asyncio.run(go())


def test_remote_token_protects_and_rewrites():
    from brain_mcp import remote

    app = remote.protect(_echo)
    tok = remote.token()
    assert len(tok) >= 30
    assert _call(app, "/mcp")[0] == 404
    assert _call(app, "/nope/mcp")[0] == 404
    assert _call(app, f"/{tok}/mcp") == (200, "/mcp")  # el SDK recibe /mcp, sin el token
    assert _call(app, "/mcp", {"Authorization": f"Bearer {tok}"}) == (200, "/mcp")
    assert _call(app, "/mcp", {"Authorization": "Bearer otro"})[0] == 404
    assert _call(app, f"/{tok}/otra-cosa")[0] == 404
    new = remote.regenerate()
    assert new != tok
    assert _call(app, f"/{tok}/mcp")[0] == 404  # la URL vieja deja de andar sin reiniciar
    assert _call(app, f"/{new}/mcp")[0] == 200


def test_remote_config_validation():
    from brain_mcp import remote

    with pytest.raises(remote.RemoteError) as e:
        remote.configure({"mode": "named"})
    assert e.value.code == "named_incomplete"
    with pytest.raises(remote.RemoteError) as e:
        remote.configure({"hostname": "no es un host"})
    assert e.value.code == "bad_hostname"
    cfg = remote.configure({"mode": "named", "hostname": "https://Brain.Ejemplo.com/",
                            "tunnel_token": "cloudflared service install eyJhIjoiMTIzNDU2Nzg5MCJ9abcdef"})
    assert cfg["hostname"] == "brain.ejemplo.com" and cfg["tunnel_token"] == "eyJhIjoiMTIzNDU2Nzg5MCJ9abcdef"
    pub = remote.public("https://brain.ejemplo.com")
    assert "tunnel_token" not in pub and pub["has_tunnel_token"]
    assert pub["url"] == f"https://brain.ejemplo.com/{pub['token']}/mcp"
    assert remote.configure({"tunnel_token": ""})["tunnel_token"]  # vacío = conservar
    if __import__("os").name != "nt":
        assert oct(remote.CONFIG.stat().st_mode & 0o777) == "0o600"


def test_tunnel_url_and_command(monkeypatch):
    from brain_mcp import remote

    lines = ["INF Requesting new quick Tunnel on trycloudflare.com...",
             "INF |  https://alpha-beta-gamma.trycloudflare.com                                 |"]
    assert remote.tunnel_url(lines) == "https://alpha-beta-gamma.trycloudflare.com"
    assert remote.tunnel_url(lines[:1]) is None
    monkeypatch.setattr(remote, "cloudflared_path", lambda: "/bin/cloudflared")
    cmd, env = remote.tunnel_command()
    assert cmd[-2:] == ["--url", f"http://127.0.0.1:{remote.PORT}"] and env == {}
    remote.configure({"mode": "named", "hostname": "b.example.com", "tunnel_token": "x" * 40})
    cmd, env = remote.tunnel_command()
    assert cmd[-1] == "run" and env == {"TUNNEL_TOKEN": "x" * 40} and "x" * 40 not in cmd  # el token no va en la línea de comando
    assert remote.tunnel_url(["INF Registered tunnel connection connIndex=0"]) == "https://b.example.com"


def test_cloudflared_checksum_from_release_notes(monkeypatch):
    import requests
    from brain_mcp import remote

    body = ("SHA256 Checksums:\ncloudflared-linux-amd64: " + "a" * 64 + "\ncloudflared-darwin-arm64.tgz: " + "b" * 64 + "\n")

    class R:
        status_code = 200
        text = body

        def json(self):
            return {"body": body}
    monkeypatch.setattr(requests, "get", lambda *a, **k: R())
    assert remote._expected_sha256("cloudflared-linux-amd64") == "a" * 64
    assert remote._expected_sha256("cloudflared-darwin-arm64.tgz") == "b" * 64
    assert remote._expected_sha256("cloudflared-windows-amd64.exe") is None


def test_read_only_remote(monkeypatch):
    import server
    from brain_mcp import remote

    remote.configure({"read_only": True})
    monkeypatch.setenv("BRAIN_REMOTE", "1")
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert names == server.READ_ONLY_TOOLS
    r = asyncio.run(server.mcp.call_tool("write_file", {"path": "x.md", "content": "hola"}))
    assert r.is_error and "solo lectura" in r.content[0].text
    monkeypatch.delenv("BRAIN_REMOTE")  # el dashboard y los agentes locales nunca quedan en solo lectura
    assert "write_file" in {t.name for t in asyncio.run(server.mcp.list_tools())}


def test_remote_blocks_private_urls(monkeypatch):
    from brain_mcp import scrape

    monkeypatch.setenv("BRAIN_REMOTE", "1")
    for url in ("http://127.0.0.1:8765/api/status", "http://localhost/", "http://10.0.0.1/", "file:///etc/passwd"):
        with pytest.raises(scrape.ScrapeError):
            scrape.scrape(url)


def test_backup_keeps_remote_token_out_by_default():
    from brain_mcp import backup, remote

    remote.token()
    with zipfile.ZipFile(backup.path_of(backup.create()["name"])) as z:
        assert not any("remote" in n for n in z.namelist())
    with zipfile.ZipFile(backup.path_of(backup.create(include_secrets=True)["name"])) as z:
        assert "secrets/remote.json" in z.namelist()


# ---------- agentes ----------

def test_openmausbot_and_manus(tmp_path, monkeypatch):
    from brain_mcp import agents

    cfg = tmp_path / ".openmausbot/config.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"apiKey": "k", "mcpServers": {"otro": {"command": "x"}}}))
    monkeypatch.setitem(agents.CLIENTS["openmausbot"], "path", cfg)
    monkeypatch.setattr(agents, "_app_running", lambda c: False)
    st = agents.set_connected("openmausbot", True)
    data = json.loads(cfg.read_text())
    assert st["connected"] and data["apiKey"] == "k" and "otro" in data["mcpServers"]
    assert data["mcpServers"]["brain"]["args"][-1] == "server.py"
    assert cfg.with_name("config.json.bak-brain").exists()
    m = agents.status("manus")
    assert m["kind"] == "url" and m["installed"] and not m["connected"] and not m["auto"]
    with pytest.raises(agents.AgentError) as e:
        agents.set_connected("manus", True)
    assert e.value.code == "url_only"


# ---------- chat: skills con "/" y contexto fijo ----------

def test_chat_skills_and_app_guide():
    from brain_mcp import chat, vault

    vault.write_file("skills/escribir-mails.md", "---\nname: escribir-mails\ndescription: Mails cortos\n---\nPaso 1: saludo corto.")
    vault.write_file("skills/organize.md", "---\nname: organize\ndescription: tapado por el comando\n---\nx")
    names = [s["name"] for s in chat.skills()]
    assert names == ["escribir-mails"]
    assert chat.parse_command("/escribir-mails a Juan") == ("skill:escribir-mails", "a Juan")
    assert chat.parse_command("/organize") == ("organize", "")
    assert chat.parse_command("/no-existe hola") == ("", "/no-existe hola")
    assert "escribir-mails" in chat._command_request("skill:escribir-mails", "a Juan")
    ctx = chat._context("hola", "c1", "es", "", "skill:escribir-mails")
    assert "Paso 1: saludo corto." in ctx and chat.APP_GUIDE["es"] in ctx
    # el contexto fijo va siempre, también sin comando, y antes de las instrucciones del usuario
    ctx = chat._context("hola", "c1", "es", "RESPONDÉ COMO PIRATA")
    assert ctx.index("Cómo funciona brain") < ctx.index("PIRATA")


# ---------- actividad para las notificaciones ----------

def test_history_since():
    from brain_mcp import history, vault

    start = history.since(-1)
    assert start["changes"] == []
    vault.write_file("a.md", "---\nname: a\ndescription: a\n---\nuno")
    vault.write_file("a.md", "---\nname: a\ndescription: a\n---\ndos")
    vault.delete_file("a.md")
    r = history.since(start["last_id"])
    assert [(c["op"], c["was_new"]) for c in r["changes"]] == [("create", 1), ("update", 0), ("delete", 0)]
    assert history.since(r["last_id"])["changes"] == []


# ---------- dashboard ----------

@pytest.fixture
def dash():
    import dashboard

    srv = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    port = srv.server_address[1]
    dashboard.ALLOWED_HOSTS.update({f"127.0.0.1:{port}", f"localhost:{port}"})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def _post(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"X-Brain": "1", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def test_dashboard_v03_api(dash, monkeypatch):
    import dashboard

    r = _get(dash + "/api/remote")
    assert r["ok"] and r["url"] is None and r["tunnel"]["status"] == "stopped" and r["token"]
    assert _post(dash + "/api/remote/settings", {"hostname": "mal host"})["code"] == "bad_hostname"
    assert _post(dash + "/api/remote/settings", {"read_only": True})["read_only"] is True
    old = r["token"]
    assert _post(dash + "/api/remote/regenerate")["token"] != old
    monkeypatch.setattr(dashboard.remote, "cloudflared_path", lambda: None)
    assert _post(dash + "/api/remote/start")["code"] == "no_cloudflared"

    assert _get(dash + "/api/activity?after=-1")["changes"] == []
    assert "Cómo funciona brain" in _get(dash + "/api/chat/app_context")["text"]["es"]
    assert _get(dash + "/api/chat/commands") == {"ok": True, "skills": []}

    monkeypatch.delenv("BRAIN_LAUNCHER", raising=False)
    assert _get(dash + "/api/version")["can_update"] is False
    assert _post(dash + "/api/update/apply")["code"] == "no_launcher"
    monkeypatch.setenv("BRAIN_LAUNCHER", "1")
    monkeypatch.setitem(dashboard._SERVER, "srv", None)
    r = _post(dash + "/api/update/apply")
    assert r["ok"] and dashboard._SERVER["restart"] is True
    dashboard._SERVER["restart"] = False
