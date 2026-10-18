"""v0.03.3.1: modo app de escritorio (ajustes del shell, log propio) y backup de esos ajustes. Sin red."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from brain_mcp import appmode, backup


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


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"X-Brain": "1", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def test_defaults_and_save_validation():
    cfg = appmode.load()
    assert cfg == {"notify": {"on": True, "create": True, "update": True, "delete": True}, "notch": True, "close_to_tray": True}
    out = appmode.save({"notify": {"delete": False, "create": "no", "bogus": False}, "notch": False, "close_to_tray": "x", "evil": 1})
    assert out["notify"] == {"on": True, "create": True, "update": True, "delete": False}  # "no" no es booleano: se ignora
    assert out["notch"] is False and out["close_to_tray"] is True
    assert appmode.load() == out and "evil" not in json.loads(appmode.SETTINGS.read_text())


def test_corrupt_settings_fall_back_to_defaults():
    appmode.SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    appmode.SETTINGS.write_text("{not json", encoding="utf-8")
    assert appmode.load()["notch"] is True
    appmode.SETTINGS.write_text('["lista"]', encoding="utf-8")
    assert appmode.load()["notify"]["on"] is True


def test_log_tail_and_service_entry(monkeypatch):
    assert appmode.log_lines() == [] and appmode.service_info() is None  # terminal: sin app, sin pestaña
    appmode.LOG.parent.mkdir(parents=True, exist_ok=True)
    appmode.LOG.write_text("\n".join(f"línea {i}" for i in range(700)) + "\n", encoding="utf-8")
    lines = appmode.log_lines()
    assert len(lines) == 500 and lines[-1] == "línea 699"
    info = appmode.service_info()  # hubo una app antes: la pestaña queda, apagada
    assert info["key"] == "app" and info["group"] == "app" and info["status"] == "stopped"
    monkeypatch.setenv("BRAIN_APP", "1")
    assert appmode.service_info()["status"] == "running"


def test_api(dash, monkeypatch):
    assert _get(dash + "/api/version")["app"] is False
    st = _get(dash + "/api/status")
    assert "app" not in {s["key"] for s in st["services"]}
    monkeypatch.setenv("BRAIN_APP", "1")
    assert _get(dash + "/api/version")["app"] is True
    assert "app" in {s["key"] for s in _get(dash + "/api/status")["services"]}
    assert _get(dash + "/api/app")["active"] is True
    out = _post(dash + "/api/app", {"notify": {"on": False}, "notch": False})
    assert out["notify"]["on"] is False and out["notch"] is False
    assert _get(dash + "/api/app")["notify"]["on"] is False
    appmode.LOG.parent.mkdir(parents=True, exist_ok=True)
    appmode.LOG.write_text("2026-10-18 [app] hola\n", encoding="utf-8")
    assert _get(dash + "/api/logs/app")["lines"] == ["2026-10-18 [app] hola"]


def test_post_requires_header(dash):
    req = urllib.request.Request(dash + "/api/app", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)
    assert e.value.code == 403


def test_settings_travel_in_backups():
    assert "app_settings.json" in backup.CONFIG_FILES


def test_handoff_api(dash, monkeypatch, tmp_path):
    import dashboard

    assert _get(dash + "/api/app")["installed"] is False
    assert _post(dash + "/api/app/handoff", {})["code"] == "not_installed"
    fake = tmp_path / "brain.app"
    fake.mkdir()
    monkeypatch.setattr(appmode, "installed_path", lambda: fake)
    called = []
    monkeypatch.setattr(appmode, "handoff", lambda port, root: called.append((port, root)))
    monkeypatch.setattr(dashboard, "_quit_app", lambda: {"ok": True})
    assert _get(dash + "/api/app")["installed"] is True
    assert _post(dash + "/api/app/handoff", {}) == {"ok": True}
    assert called and (called[0][1] / "dashboard.py").exists()  # la raíz de brain, donde está brain.sh
    monkeypatch.setenv("BRAIN_APP", "1")  # dentro de la app no hay a dónde pasar
    assert _post(dash + "/api/app/handoff", {})["code"] == "already_app"
