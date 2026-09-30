"""El dashboard arranca y respeta sus protecciones (Host y X-Brain) en los tres sistemas."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest


@pytest.fixture
def dash():
    import dashboard

    srv = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    port = srv.server_address[1]
    dashboard.ALLOWED_HOSTS.update({f"127.0.0.1:{port}", f"localhost:{port}"})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read()


def test_pages_and_api(dash):
    assert _get(dash + "/")[0] == 200
    assert b"BrainUI" in _get(dash + "/ui.js")[1]
    st = json.loads(_get(dash + "/api/status")[1])
    assert {s["key"] for s in st["services"]} == {"chroma", "ollama", "inspector"}
    assert json.loads(_get(dash + "/api/backup/list")[1])["ok"]


def test_rejects_foreign_host(dash):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(dash + "/api/status", {"Host": "evil.example:80"})
    assert e.value.code == 403


def test_post_requires_header(dash):
    req = urllib.request.Request(dash + "/api/backup/create", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)
    assert e.value.code == 403
