"""v0.03.4.1: el acceso remoto por túnel (Claude en la nube): sin SSE, sin redirecciones y con prueba de la URL."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from brain_mcp import remote

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
HDR = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


@pytest.fixture()
def app():
    """(cliente, token): la app ASGI de verdad (con su lifespan), envuelta con remote.protect."""
    import server
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.testclient import TestClient

    tok = remote.token()
    inner = server.mcp.streamable_http_app(
        json_response=True, stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    with TestClient(remote.protect(inner), base_url="http://x.trycloudflare.com", follow_redirects=False) as c:
        yield c, tok


def _call(client, method, path, **kw):
    return client.request(method, path, **kw)


def test_post_without_session_and_with_trailing_slash(app):
    a, tok = app
    for path in (f"/{tok}/mcp", f"/{tok}/mcp/"):  # con la barra final no hay 307 a una dirección local
        r = _call(a, "POST", path, json=INIT, headers=HDR)
        assert r.status_code == 200, (path, r.status_code, r.headers.get("location"))
        assert r.json()["result"]["serverInfo"]["name"] == "brain" and r.json()["result"]["serverInfo"]["version"]
        assert "mcp-session-id" not in r.headers  # sin sesiones
    r = _call(a, "POST", f"/{tok}/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=HDR)
    assert r.status_code == 200 and len(r.json()["result"]["tools"]) > 10  # tools/list sin initialize previo


def test_no_sse_get_and_delete_are_405(app):
    a, tok = app
    for m in ("GET", "DELETE"):
        r = _call(a, m, f"/{tok}/mcp", headers={"Accept": "text/event-stream"})
        assert r.status_code == 405 and r.headers["allow"] == "POST"


def test_token_rules_still_hold(app):
    a, tok = app
    assert _call(a, "POST", "/mcp", json=INIT, headers=HDR).status_code == 404
    assert _call(a, "POST", "/otro/mcp", json=INIT, headers=HDR).status_code == 404
    assert _call(a, "POST", f"/{tok}/otra", json=INIT, headers=HDR).status_code == 404
    assert _call(a, "POST", f"/{tok}/mcpx", json=INIT, headers=HDR).status_code == 404
    assert _call(a, "GET", "/mcp").status_code == 404  # sin token ni siquiera dice que existe
    ok = _call(a, "POST", "/mcp/", json=INIT, headers={**HDR, "Authorization": f"Bearer {tok}"})
    assert ok.status_code == 200
    assert _call(a, "POST", "/mcp", json=INIT, headers={**HDR, "Authorization": "Bearer mal"}).status_code == 404


# ---------- prueba de la URL pública ----------

def _fake(status=200, body=None, location=None):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            payload = body if body is not None else (
                {"jsonrpc": "2.0", "id": req.get("id"), "result": {"serverInfo": {"name": "brain"}}} if req.get("method") == "initialize"
                else {"jsonrpc": "2.0", "id": req.get("id"), "result": {"tools": [{"name": "a"}, {"name": "b"}]}})
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/tok/mcp"


@pytest.mark.parametrize("kw,code", [
    ({}, ""), ({"status": 404}, "bad_token"), ({"status": 530}, "tunnel_down"), ({"status": 418}, "http"),
    ({"status": 307, "location": "http://127.0.0.1:1/mcp"}, "redirect"), ({"body": b"<html>hola</html>"}, "bad_body"),
])
def test_selftest_reports_where_it_breaks(kw, code):
    srv, url = _fake(**kw)
    try:
        r = remote.selftest(url, timeout=5)
    finally:
        srv.shutdown()
    assert r["ok"] is (code == "") and r["code"] == code
    if not code:
        assert r["tools"] == 2 and r["server"] == "brain"


def test_selftest_unreachable():
    r = remote.selftest("http://127.0.0.1:9/tok/mcp", timeout=3)
    assert r["ok"] is False and r["code"] in ("connect", "timeout")


def test_remote_test_action_needs_a_url(monkeypatch):
    import dashboard

    monkeypatch.setattr(dashboard, "_remote_info", lambda: {"ok": True, "url": None})
    assert dashboard._remote_post("test", {}) == {"ok": False, "code": "no_url"}
    monkeypatch.setattr(dashboard, "_remote_info", lambda: {"ok": True, "url": "http://u/x/mcp"})
    monkeypatch.setattr(remote, "selftest", lambda url: {"ok": True, "tools": 3, "ms": 5, "code": ""})
    out = dashboard._remote_post("test", {})
    assert out["ok"] and out["test"]["tools"] == 3
