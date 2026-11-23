"""v0.03.4.2: túneles con URL fija (Tailscale Funnel y ngrok) además de Cloudflare."""
import json
import os
import stat
import sys
import time

import pytest

unix_only = pytest.mark.skipif(sys.platform == "win32", reason="los ejecutables falsos son scripts de sh")


def _fake_exe(folder, name, script):
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text("#!/bin/sh\n" + script)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


# ---------- configuración ----------

def test_modes_and_ngrok_settings():
    from brain_mcp import remote

    with pytest.raises(remote.RemoteError) as e:
        remote.configure({"mode": "otro"})
    assert e.value.code == "bad_mode"
    cfg = remote.configure({"mode": "ngrok", "ngrok_token": "ngrok config add-authtoken 2abcDEF_ghij-KLMNopqrstuvwxyz0123",
                            "ngrok_domain": "https://Mi-Brain.ngrok-free.app/"})
    assert cfg["mode"] == "ngrok" and cfg["ngrok_token"] == "2abcDEF_ghij-KLMNopqrstuvwxyz0123"
    assert cfg["ngrok_domain"] == "mi-brain.ngrok-free.app"
    assert remote.configure({"ngrok_token": ""})["ngrok_token"]  # vacío = conservar
    for bad, code in (({"ngrok_token": "corto"}, "bad_ngrok_token"), ({"ngrok_domain": "no es un dominio"}, "bad_ngrok_domain")):
        with pytest.raises(remote.RemoteError) as e:
            remote.configure(bad)
        assert e.value.code == code
    pub = remote.public(None)
    assert "ngrok_token" not in pub and pub["has_ngrok_token"] and pub["ngrok_domain"] == "mi-brain.ngrok-free.app"
    assert pub["stable"] is True and set(pub["tools"]) == {"tailscale", "ngrok", "cloudflared"}
    assert remote.configure({"mode": "quick"}) and remote.public(None)["stable"] is False


def test_commands_keep_secrets_out_of_argv(monkeypatch):
    from brain_mcp import remote

    monkeypatch.setattr(remote, "tailscale_path", lambda: "/bin/tailscale")
    monkeypatch.setattr(remote, "ngrok_path", lambda: "/bin/ngrok")
    remote.configure({"mode": "tailscale"})
    cmd, env = remote.tunnel_command()
    assert cmd == ["/bin/tailscale", "funnel", f"http://127.0.0.1:{remote.PORT}"] and env == {}
    remote.configure({"mode": "ngrok", "ngrok_token": "t" * 30, "ngrok_domain": "x.ngrok-free.app"})
    cmd, env = remote.tunnel_command()
    assert cmd[:3] == ["/bin/ngrok", "http", f"http://127.0.0.1:{remote.PORT}"] and "--log" in cmd
    assert cmd[-2:] == ["--url", "https://x.ngrok-free.app"]
    assert env == {"NGROK_AUTHTOKEN": "t" * 30} and not any("t" * 30 in c for c in cmd)
    monkeypatch.setattr(remote, "ngrok_path", lambda: None)
    with pytest.raises(remote.RemoteError) as e:
        remote.tunnel_command()
    assert e.value.code == "no_ngrok" and remote.missing_code() == "no_ngrok"


def test_url_parsing_per_service():
    from brain_mcp import remote

    assert remote.url_in("https://mi-compu.tail1a2b3.ts.net/", "tailscale") == "https://mi-compu.tail1a2b3.ts.net"
    assert remote.url_in("visit https://login.tailscale.com/f/funnel?node=abc", "tailscale") is None
    line = 't=2026-11-23T10:00:00-0300 lvl=info msg="started tunnel" obj=tunnels name=command_line addr=http://127.0.0.1:8770 url=https://mi-brain.ngrok-free.app'
    assert remote.url_in(line, "ngrok") == "https://mi-brain.ngrok-free.app"
    assert remote.url_in('lvl=info msg="client session established" url=https://connect.ngrok-agent.com', "ngrok") is None
    assert remote.url_in("INF |  https://a-b.trycloudflare.com  |", "quick") == "https://a-b.trycloudflare.com"
    assert remote.is_fatal('lvl=eror msg="failed to reconnect session" err="authentication failed: ERR_NGROK_105"', "ngrok")
    assert remote.is_fatal("Access denied: serve config denied", "tailscale")
    assert not remote.is_fatal("Available on the internet:", "tailscale")


# ---------- el servicio del túnel ----------

def _tunnel():
    from test_v0312 import _tunnel as t  # mismo doble de prueba que el watchdog de Cloudflare
    return t()


def test_tailscale_waits_for_funnel_activation():
    from brain_mcp import remote, services

    remote.configure({"mode": "tailscale"})
    t = _tunnel()
    t.start()
    for line in ["Funnel is not enabled on your tailnet.", "To enable, visit:",
                 "         https://login.tailscale.com/f/funnel?node=nABC123"]:
        t.on_line(line)
    assert t.action_url == "https://login.tailscale.com/f/funnel?node=nABC123" and t.link() is None
    assert t.info()["action_url"] == t.action_url
    w = services.RemoteWatchdog(_Up(), t, _Awake())
    remote._update(enabled=True)
    w.last_tick = 1000
    assert w.tick(1000 + 500) is None and t.starts == 1  # esperando al usuario: no lo reabre aunque no haya URL
    for line in ["Available on the internet:", "", "https://mi-compu.tail1a2b3.ts.net/", "|-- proxy http://127.0.0.1:8770"]:
        t.on_line(line)
    assert t.action_url is None and t.link() == "https://mi-compu.tail1a2b3.ts.net" and t.healthy()


def test_fatal_error_is_not_retried():
    from brain_mcp import remote, services

    remote.configure({"mode": "ngrok"})
    remote._update(enabled=True)
    t = _tunnel()
    t.start()
    t.on_line('t=x lvl=eror msg="session closed" err="authentication failed: Your authtoken is invalid. ERR_NGROK_105"')
    assert t.fatal and "ERR_NGROK_105" in t.info()["fatal"]
    t.alive = False  # ngrok se cerró
    w = services.RemoteWatchdog(_Up(), t, _Awake())
    w.last_tick = 1000
    assert w.tick(1100) is None and t.starts == 1  # reintentar no arregla un token inválido
    t.start()  # Reintentar (o guardar otro token) vuelve a empezar de cero
    assert t.fatal is None and t.starts == 2


class _Up:
    def status(self):
        return "running"

    def ours(self):
        return True

    def start(self):
        pass


class _Awake:
    def start(self):
        return True

    def stop(self):
        pass


@unix_only
def test_real_process_with_fake_tailscale(tmp_path, monkeypatch):
    """El servicio de verdad (Popen + lectura de la salida) con un `tailscale` falso."""
    from brain_mcp import remote, services

    exe = _fake_exe(tmp_path / "bin", "tailscale", """
if [ "$1" = "status" ]; then echo '{"Self": {"DNSName": "mi-compu.tail1a2b3.ts.net."}}'; exit 0; fi
echo "Available on the internet:"
echo ""
echo "https://mi-compu.tail1a2b3.ts.net/"
echo "|-- proxy $2"
sleep 30
""")
    monkeypatch.setattr(remote, "tailscale_path", lambda: exe)
    remote._TS_CACHE["at"] = 0
    remote.configure({"mode": "tailscale"})
    assert remote.tailscale_url() == "https://mi-compu.tail1a2b3.ts.net"
    t = services.Tunnel("tunnel", "T", "", remote.PORT, services._tunnel_cmd, env=services._tunnel_env)
    try:
        t.start()
        for _ in range(50):
            if t.link():
                break
            time.sleep(0.1)
        assert t.link() == "https://mi-compu.tail1a2b3.ts.net" and t.status() == "running"
        assert any(f"proxy http://127.0.0.1:{remote.PORT}" in x for x in t.logs)
    finally:
        t.stop()


def test_dashboard_reports_missing_tool(monkeypatch):
    import dashboard
    from brain_mcp import remote

    monkeypatch.setattr(remote, "tailscale_path", lambda: None)
    remote.configure({"mode": "tailscale"})
    r = dashboard._remote_post("start", {})
    assert r == {"ok": False, "code": "no_tailscale", "detail": ""}
    info = dashboard._remote_info()
    assert info["mode"] == "tailscale" and info["tools"]["tailscale"] is None and info["enabled"] is False
    assert json.dumps(info)  # serializable para /api/remote
    assert os.environ.get("NGROK_AUTHTOKEN") is None
