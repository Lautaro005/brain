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
    m = agents.status("manus")  # local, con su formulario: no hay archivo que escribir
    assert m["kind"] == "form" and m["group"] == "desktop" and not m["connected"] and not m["auto"] and not m["detected"]
    with pytest.raises(agents.AgentError) as e:
        agents.set_connected("manus", True)
    assert e.value.code == "form_only"
    monkeypatch.setattr(agents, "_seen", lambda key: key == "manus")  # Manus ya usó brain (handshake MCP)
    assert agents.status("manus")["detected"]


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


# ---------- arreglos de la revisión ----------

def test_remote_redirect_to_private_is_blocked(monkeypatch):
    """Una página "pública" que redirige a 127.0.0.1 no lleva a brain a leer servicios locales."""
    from http.server import BaseHTTPRequestHandler

    from brain_mcp import scrape

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/go":
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{self.server.server_address[1]}/secret")
                self.end_headers()
            else:
                body = ("<html><head><title>Hola</title></head><body><article><p>" + "palabra " * 80 + "</p></article></body></html>").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)
                self.server.hosts.append(self.headers.get("Host"))

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.hosts = []
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    real = scrape.check_public
    # el server de prueba hace de sitio "público" solo con el nombre 127.0.0.1; localhost sigue bloqueado
    monkeypatch.setattr(scrape, "check_public", lambda url: "127.0.0.1" if "//127.0.0.1:" in url else real(url))
    monkeypatch.setenv("BRAIN_REMOTE", "1")
    try:
        with pytest.raises(scrape.ScrapeError, match="públicas"):
            scrape.scrape(f"http://127.0.0.1:{port}/go")
        r = scrape.scrape(f"http://127.0.0.1:{port}/ok")
        assert "palabra" in r["text"] and r["rendered_js"] is False
        assert srv.hosts == [f"127.0.0.1:{port}"]  # se conecta a la IP validada, con el Host original
    finally:
        srv.shutdown()


def test_remote_refuses_busy_port(monkeypatch):
    import dashboard

    monkeypatch.setattr(dashboard.remote, "cloudflared_path", lambda: "/bin/cloudflared")
    monkeypatch.setattr(dashboard.SERVICES["remote_mcp"], "status", lambda: "external")
    started = []
    monkeypatch.setattr(dashboard.SERVICES["tunnel"], "start", lambda: started.append(1))
    r = dashboard._remote_post("start", {})
    assert r["code"] == "busy" and not started  # nunca se publica un puerto que no es de brain


def test_history_since_paginates():
    from brain_mcp import history

    start = history.since(-1)["last_id"]
    for i in range(7):
        history.record("create", f"n{i}.md", None, "x")
    r = history.since(start, limit=3)
    assert len(r["changes"]) == 3 and r["total"] == 7 and r["last_id"] == r["changes"][-1]["id"]
    assert r["max_id"] == start + 7
    assert len(history.since(r["last_id"], limit=10)["changes"]) == 4  # nada se pierde


def test_command_punctuation_and_skill_names():
    from brain_mcp import chat, vault

    assert chat.parse_command("/organize.") == ("organize", "")
    assert chat.parse_command("/compact, ahora") == ("compact", "ahora")
    vault.write_file("skills/con espacio.md", "---\nname: x\ndescription: x\n---\nx")
    vault.write_file("skills/reseñas.md", "---\nname: x\ndescription: x\n---\nx")
    vault.write_file("skills/equipo/onboarding.md", "---\nname: x\ndescription: x\n---\nx")
    assert [s["name"] for s in chat.skills()] == ["equipo/onboarding"]
    assert chat.parse_command("/equipo/onboarding. Ana") == ("skill:equipo/onboarding", "Ana")


def test_cloudflared_failed_download_leaves_nothing(monkeypatch):
    import requests
    from brain_mcp import remote

    monkeypatch.setattr(remote, "_expected_sha256", lambda asset: "0" * 64)

    class R:
        status_code = 403

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(requests, "get", lambda *a, **k: R())
    with pytest.raises(remote.RemoteError):
        remote.install_cloudflared()
    assert list(remote.BIN.iterdir()) == []


def test_remote_config_cache_sees_new_token():
    from brain_mcp import remote

    tok = remote.token()
    assert remote.load()["token"] == tok
    new = remote.regenerate()
    assert remote.load()["token"] == new != tok  # mismo tamaño de archivo: el cache no puede quedarse con el viejo



# ---------- v0.03.0.1 ----------

def test_deepseek_harness_patch_layer(tmp_path, monkeypatch):
    """brain se agrega a ~/.dsh/cordis.patch.yml como un bloque propio, sin tocar el resto (comentarios y !!js)."""
    import yaml
    from brain_mcp import agents

    path = tmp_path / ".dsh/cordis.patch.yml"
    path.parent.mkdir()
    original = "# mis parches\n- id: tools\n  config:\n    token: !!js process.env.X\n"
    path.write_text(original)
    monkeypatch.setitem(agents.CLIENTS["deepseek_harness"], "path", path)
    st = agents.set_connected("deepseek_harness", True)
    text = path.read_text()
    assert st["connected"] and not st["elsewhere"] and text.startswith(original)
    assert path.with_name("cordis.patch.yml.bak-brain").read_text() == original
    patches = yaml.load(text, Loader=agents._DshLoader)
    row = patches[-1]["insert"][0]
    assert row["name"] == "@deepseek-ai/dsh-mcp-client" and row["config"]["transport"] == "stdio"
    assert row["config"]["serverName"] == "brain" and row["config"]["args"][-1] == "server.py"
    agents.set_connected("deepseek_harness", True)  # reconectar no duplica
    assert path.read_text().count("dsh-mcp-client") == 1
    agents.set_connected("deepseek_harness", False)
    assert path.read_text() == original
    for bad in ("{a: 1}\n", "- [unclosed\n"):  # dsh no lo podría leer: no se toca
        path.write_text(bad)
        with pytest.raises(agents.AgentError) as e:
            agents.set_connected("deepseek_harness", True)
        assert e.value.code == "bad_config" and path.read_text() == bad
    path.write_text("[]\n")
    agents.set_connected("deepseek_harness", True)
    assert agents.status("deepseek_harness")["connected"]


def test_cloudflared_macos_checksum_is_of_the_inner_binary(monkeypatch, tmp_path):
    """En macOS Cloudflare publica el SHA256 del binario dentro del .tgz, con el nombre del .tgz."""
    import hashlib
    import io
    import tarfile

    import requests
    from brain_mcp import remote

    binary = b"#!/bin/sh\necho cloudflared\n" * 100
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("cloudflared")
        info.size = len(binary)
        tf.addfile(info, io.BytesIO(binary))
    tgz = buf.getvalue()

    class R:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_content(self, n):
            yield tgz
    monkeypatch.setattr(requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(remote, "_asset", lambda: "cloudflared-darwin-arm64.tgz")
    for published in (hashlib.sha256(binary).hexdigest(), hashlib.sha256(tgz).hexdigest()):
        monkeypatch.setattr(remote, "_expected_sha256", lambda a, h=published: h)
        r = remote.install_cloudflared()
        assert remote._local_bin().read_bytes() == binary and r["sha256"] == published
    remote._local_bin().unlink()
    monkeypatch.setattr(remote, "_expected_sha256", lambda a: "0" * 64)
    with pytest.raises(remote.RemoteError) as e:
        remote.install_cloudflared()
    assert e.value.code == "checksum" and list(remote.BIN.iterdir()) == []
