"""v0.03.1.2: backup automático, acceso remoto que sobrevive a que la compu se duerma, /compact como el de
Claude (y auto-compact) y la vista previa de HTML del chat. Sin red, Ollama ni Chroma."""
import json
import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.test_dashboard import _get, dash  # noqa: F401  (fixture)
from tests.test_v031 import _post

ROOT = Path(__file__).resolve().parent.parent


# ---------- backup automático ----------

def test_autobackup_validation_and_schedule(tmp_path):
    from brain_mcp import autobackup, vault

    with pytest.raises(autobackup.AutoBackupError) as e:
        autobackup.save({"folder": "relativa/backups"})
    assert e.value.code == "folder_relative"
    with pytest.raises(autobackup.AutoBackupError) as e:
        autobackup.save({"folder": str(vault.VAULT / "bk")})
    assert e.value.code == "folder_in_vault"
    for bad in ("../x", "a b", "{foo}", "x{date", ""):
        with pytest.raises(autobackup.AutoBackupError):
            autobackup.save({"name": bad})
    with pytest.raises(autobackup.AutoBackupError):
        autobackup.save({"frequency": "monthly"})
    with pytest.raises(autobackup.AutoBackupError):
        autobackup.save({"at": "25:00"})

    st = autobackup.save({"enabled": True, "folder": str(tmp_path / "Backups"), "frequency": "daily", "at": "03:30",
                          "name": "mi-brain-{version}-{date}.zip", "keep": 2})
    assert st["name"] == "mi-brain-{version}-{date}" and (tmp_path / "Backups").is_dir()
    assert st["preview"].startswith("mi-brain-v") and st["preview"].endswith(".zip")

    cfg = autobackup.load()
    base = datetime(2026, 10, 6, 10, 0)  # lunes
    cfg["state"] = {"since": base.isoformat()}
    assert autobackup.next_run(cfg) == datetime(2026, 10, 7, 3, 30)
    cfg.update(frequency="weekly", weekday=4, at="09:00")
    assert autobackup.next_run(cfg) == datetime(2026, 10, 9, 9, 0)
    cfg.update(frequency="hours", hours=6)
    cfg["state"] = {"last_run": base.isoformat()}
    assert autobackup.next_run(cfg) == base + timedelta(hours=6)
    # después de un error, se reintenta a los 15 minutos (no en cada vuelta)
    cfg["state"] = {"last_run": (base - timedelta(hours=7)).isoformat(), "last_error": "x", "last_attempt": base.isoformat()}
    assert autobackup.next_run(cfg) == base + timedelta(minutes=15)


def test_autobackup_runs_prunes_and_lists(tmp_path):
    from brain_mcp import autobackup, backup, vault

    folder = tmp_path / "ext"
    autobackup.save({"enabled": True, "folder": str(folder), "frequency": "hours", "hours": 1,
                     "name": "auto-{n}", "keep": 2, "only_changes": True})
    foreign = folder / "otro-backup.zip"
    foreign.write_bytes(b"no es mio")

    # recién prendido: todavía no toca
    assert autobackup.tick() is None
    later = datetime.now() + timedelta(hours=1, minutes=1)
    r = autobackup.tick(later)
    assert r["name"] == "auto-1.zip" and (folder / "auto-1.zip").is_file()
    with zipfile.ZipFile(folder / "auto-1.zip") as z:
        man = json.loads(z.read("manifest.json"))
    assert man["auto"] is True and not man["includes_secrets"]

    # sin cambios: se saltea (no crea un archivo igual)
    r = autobackup.tick(later + timedelta(hours=1, minutes=1))
    assert r == {"skipped": True} and not (folder / "auto-2.zip").exists()

    for i in range(3):  # con cambios: crea y deja solo los 2 últimos que hizo él
        vault.write_file(f"nota{i}.md", f"---\nname: nota{i}\ndescription: x\n---\nhola {i}\n")
        os.utime(folder, None)
        autobackup.run_now()
    names = sorted(p.name for p in folder.glob("*.zip"))
    assert "otro-backup.zip" in names  # nunca toca lo que no hizo
    assert len([n for n in names if n.startswith("auto-")]) == 2
    st = autobackup.status()
    assert st["count"] == 2 and st["last_error"] == "" and st["next_run"]

    # la lista de Ajustes y la descarga/restauración los encuentran en la otra carpeta
    listed = {b["name"]: b for b in backup.listing()}
    assert st["last_name"] in listed and listed[st["last_name"]]["auto"] is True
    assert backup.path_of(st["last_name"]).parent == folder.resolve()

    # una carpeta que desapareció (disco desconectado): error claro, no se crea en otro lado
    for p in folder.glob("*"):
        p.unlink()
    folder.rmdir()
    with pytest.raises(autobackup.AutoBackupError) as e:
        autobackup.run_now()
    assert e.value.code == "folder_missing" and autobackup.status()["last_error"].startswith("folder_missing")


def test_autobackup_api(dash, tmp_path):  # noqa: F811
    st = json.loads(_get(dash + "/api/backup/auto")[1])
    assert st["ok"] and st["enabled"] is False and st["folder"] == ""
    r = json.loads(_post(dash + "/api/backup/auto", {"enabled": True, "folder": str(tmp_path / "b"), "frequency": "weekly",
                                                    "weekday": 6, "at": "22:15", "keep": 5}))
    assert r["ok"] and r["frequency"] == "weekly" and r["next_run"]
    bad = json.loads(_post(dash + "/api/backup/auto", {"folder": "x/y"}))
    assert not bad["ok"] and bad["code"] == "folder_relative"
    run = json.loads(_post(dash + "/api/backup/auto_run", {}))
    assert run["ok"] and Path(run["path"]).parent == (tmp_path / "b").resolve() and run["status"]["count"] == 1
    d = json.loads(_get(dash + "/api/backup/dirs?path=" + str(tmp_path))[1])
    assert d["ok"] and "b" in d["dirs"] and d["path"] == str(tmp_path.resolve())


# ---------- acceso remoto: dormir y despertar ----------

def _tunnel():
    from brain_mcp import remote, services

    t = services.Tunnel("tunnel", "T", "", remote.PORT, ["true"])
    t.alive = False
    t.ours = lambda: t.alive
    t.starts = 0

    def start():
        t.logs.clear()
        t._reset()
        t.alive, t.starts, t.started_at = True, t.starts + 1, 1000.0
    t.start = start
    t.stop = lambda: setattr(t, "alive", False)
    return t


class _Svc:
    def __init__(self, up=True):
        self.up, self.starts = up, 0

    def status(self):
        return "running" if self.up else "stopped"

    def ours(self):
        return self.up

    def start(self):
        self.up, self.starts = True, self.starts + 1


class _Awake:
    def __init__(self):
        self.on = False

    def start(self):
        self.on = True
        return True

    def stop(self):
        self.on = False


def test_tunnel_keeps_url_after_log_flood():
    from brain_mcp import services

    t = _tunnel()
    t.start()
    for line in ["INF Requesting new quick Tunnel on trycloudflare.com...",
                 "INF |  https://brave-otter-lake.trycloudflare.com  |",
                 "INF Registered tunnel connection connIndex=0 connection=a event=0 ip=1.2.3.4 location=eze01 protocol=quic",
                 "INF Registered tunnel connection connIndex=1 connection=b event=0 ip=1.2.3.4 location=eze01 protocol=quic"]:
        t.logs.append(line)
        t.on_line(line)
    assert t.link() == "https://brave-otter-lake.trycloudflare.com" and t.connected() is True
    # la compu se durmió: cloudflared llena los logs de reintentos (antes la URL se perdía y quedaba "Abriendo el túnel…")
    for i in range(800):
        line = f"ERR Serve tunnel error error=\"timeout\" connIndex={i % 2}" if i % 3 else f"INF Retrying connection in up to 4s connIndex={i % 2}"
        t.logs.append(line)
        t.on_line(line)
    assert len(t.logs) == 500 and not any("trycloudflare" in x for x in t.logs)
    assert t.link() == "https://brave-otter-lake.trycloudflare.com" and t.healthy()
    assert t.connected() is False and t.info()["reconnecting"] is True
    # se reconectó solo: misma URL, todo bien
    t.on_line("INF Registered tunnel connection connIndex=0 connection=c event=0 ip=1.2.3.4 location=eze01 protocol=quic")
    assert t.connected() is True and t.info()["reconnecting"] is False
    assert services.CONN_INDEX.search("connIndex=3").group(1) == "3"


def test_watchdog_recovers_after_sleep():
    from brain_mcp import remote, services

    remote._update(enabled=True)
    mcp, t, awake = _Svc(), _tunnel(), _Awake()
    w = services.RemoteWatchdog(mcp, t, awake)
    t.start()
    t.on_line("INF |  https://a-b-c.trycloudflare.com  |")
    t.on_line("INF Registered tunnel connection connIndex=0")
    now = 2000.0
    w.last_tick = now
    assert w.tick(now + 5) is None and awake.on  # todo bien: no hace nada y mantiene la compu despierta

    # se durmió 2 horas: al despertar no hay conexiones; se le da un rato para reconectarse solo
    t.on_line("ERR Connection terminated error=x connIndex=0")
    t.down_since = now + 7200
    assert w.tick(now + 7200) is None and w.woke_at == now + 7200 and t.starts == 1
    assert w.tick(now + 7230) is None and t.starts == 1
    # no pudo: se reabre el túnel
    assert w.tick(now + 7260) == "tunnel_restarted" and t.starts == 2
    t.on_line("INF |  https://x-y-z.trycloudflare.com  |")
    t._old_url = "https://a-b-c.trycloudflare.com"
    w.tick(now + 7265)
    assert t.prev_url == "https://a-b-c.trycloudflare.com" and t.url_changed_at

    # el quick tunnel ya no existe del lado de Cloudflare: se reabre sin esperar
    t.on_line("INF Registered tunnel connection connIndex=0")
    t.on_line('ERR Register tunnel error from server side error="Unauthorized: Tunnel not found" connIndex=0')
    assert w.tick(now + 7400) == "tunnel_restarted" and t.starts == 3

    # cloudflared se cerró, o el server MCP: los vuelve a prender
    t.alive, mcp.up = False, False
    assert w.tick(now + 7500) == "tunnel_started" and mcp.starts == 1 and t.starts == 4

    # apagado por el usuario: no toca nada y deja dormir a la compu
    remote.set_enabled(False)
    t.alive = False
    assert w.tick(now + 7600) is None and t.starts == 4 and not awake.on


def test_remote_keep_awake_setting():
    from brain_mcp import remote

    assert remote.load()["keep_awake"] is True
    assert remote.configure({"keep_awake": False})["keep_awake"] is False
    assert remote.public("https://x.trycloudflare.com")["keep_awake"] is False


# ---------- /compact como el de Claude ----------

def _hist():
    return [{"role": "user", "content": "Armá la nota de mi viaje a Mendoza", "tools": []},
            {"role": "assistant", "content": "Listo, la guardé.", "tools": [
                {"name": "write_file", "args": {"path": "projects/viaje.md"}, "ok": True, "result": "Guardado projects/viaje.md"}]},
            {"role": "user", "content": "Sumale que vuelvo el 12", "tools": []},
            {"role": "assistant", "content": "Hecho.", "tools": []}]


def test_compact_transcript_and_chunks():
    from brain_mcp import chat

    t = chat._transcript("RESUMEN VIEJO", _hist())
    assert t.startswith("(Resumen anterior)\nRESUMEN VIEJO") and 'write_file({"path": "projects/viaje.md"})' in t
    parts = chat._transcript_parts(_hist() * 50)
    chunks = chat._chunks_by_budget(parts, 2000)
    assert len(chunks) > 1 and all(len(c) <= 2000 for c in chunks)
    assert sum(c.count("Usuario:") for c in chunks) == 100  # ningún mensaje se pierde
    huge = chat._chunks_by_budget(["x" * 10000], 2000)
    assert len(huge) == 1 and len(huge[0]) < 2000


def test_compact_command(monkeypatch):
    from brain_mcp import chat

    seen, warmed = [], []

    def fake_stream(model, messages, tools, num_ctx=0, options=None):
        seen.append(messages)
        yield {"message": {"content": "1. Pedido: nota del viaje"}}
        yield {"done": True}

    monkeypatch.setattr(chat, "_stream", fake_stream)
    monkeypatch.setattr(chat, "_warm", lambda *a: warmed.append(a))
    monkeypatch.setattr(chat, "_mcp_tools", lambda: [])
    monkeypatch.setattr(chat, "get_chat", lambda cid: {"messages": _hist(), "title": "t", "created_at": "x", "command": ""})
    saved = []
    monkeypatch.setattr(chat, "_save", lambda *a, **k: saved.append(a))
    evs = list(chat.run("/compact enfocate en las fechas", "fake", "c1"))
    sys_, user = seen[0][0]["content"], seen[0][1]["content"]
    assert "Pendientes" in sys_ and "enfocate en las fechas" in sys_  # secciones fijas + instrucciones del usuario
    assert "projects/viaje.md" in user and "vuelvo el 12" in user
    usage = [e for e in evs if e["type"] == "usage"]
    assert usage and 0 < usage[0]["tokens"]
    assert warmed and "1. Pedido: nota del viaje" in warmed[0][1][0]["content"]  # precarga el contexto que sigue
    assert saved and saved[0][4][1]["compact"] is True
    assert evs[-1]["type"] == "done"


def test_compact_long_conversation_in_parts(monkeypatch):
    from brain_mcp import chat

    passes = []

    def fake_stream(model, messages, tools, num_ctx=0, options=None):
        passes.append(messages[1]["content"])
        yield {"message": {"content": f"RESUMEN {len(passes)}"}}

    monkeypatch.setattr(chat, "_stream", fake_stream)
    gen = chat._summarize("fake", "MUY VIEJO", _hist() * 200, 4096)
    evs = []
    try:
        while True:
            evs.append(next(gen))
    except StopIteration as stop:
        final = stop.value
    progress = [e for e in evs if e["type"] == "compact_progress"]
    assert len(passes) > 1 and len(progress) == len(passes) - 1
    assert passes[0].startswith("(Resumen anterior)\nMUY VIEJO")  # el resumen anterior no se pierde
    assert passes[1].startswith("(Resumen anterior)\nRESUMEN 1")   # cada tramo se suma a lo acumulado
    assert final == f"RESUMEN {len(passes)}" and [e for e in evs if e["type"] == "token"]


def test_auto_compact(monkeypatch):
    from brain_mcp import chat

    calls = []

    def fake_stream(model, messages, tools, num_ctx=0, options=None):
        calls.append(messages)
        if "Tu tarea es resumir" in messages[0]["content"]:
            yield {"message": {"content": "RESUMEN AUTO"}}
            return
        yield {"message": {"content": "respuesta"}}
        yield {"done": True, "prompt_eval_count": 10, "eval_count": 2}

    long = [{"role": "user" if i % 2 == 0 else "assistant", "content": "palabra " * 400, "tools": []} for i in range(10)]
    monkeypatch.setattr(chat, "_stream", fake_stream)
    monkeypatch.setattr(chat, "_mcp_tools", lambda: [])
    monkeypatch.setattr(chat, "context_window", lambda m: {"ctx": 4096, "max": 4096})
    monkeypatch.setattr(chat, "get_chat", lambda cid: {"messages": long, "title": "t", "created_at": "x", "command": ""})
    saved = []
    monkeypatch.setattr(chat, "_save", lambda *a, **k: saved.append(a))
    evs = list(chat.run("y ahora?", "fake", "c1"))
    assert any(e["type"] == "notice" and e["code"] == "auto_compact" for e in evs)
    assert any(e["type"] == "compacted" and e["summary"] == "RESUMEN AUTO" for e in evs)
    final = calls[-1]
    assert "RESUMEN AUTO" in final[0]["content"] and len(final) == 2  # system con el resumen + el mensaje nuevo
    # se guarda el par /compact + resumen y después la respuesta, a continuación
    assert saved[0][4][0]["content"] == "/compact" and saved[0][5] == 10 and saved[1][5] == 12


def test_context_keeps_volatile_part_last(monkeypatch):
    from brain_mcp import chat

    monkeypatch.setattr(chat, "_related_past", lambda text, cid, k=4: ["[2026-10-01 · usuario] algo de otro chat"])
    full = chat._context("hola", "c1", "es")
    stable = chat._context("hola", "c1", "es", past=False)
    assert full.startswith(stable) and full.index("De chats anteriores") > full.index("Fecha de hoy")
    assert "De chats anteriores" not in stable


# ---------- vista previa de HTML en el chat ----------

def test_chat_html_preview_is_sandboxed():
    html = (ROOT / "brain_mcp" / "chat.html").read_text(encoding="utf-8")
    assert "function htmlPreviews" in html and 'f.setAttribute("sandbox", st.js ? "allow-scripts" : "")' in html
    assert '"allow-scripts allow-same-origin' not in html and 'allow-same-origin"' not in html  # nunca: le daría al HTML acceso al chat y a la API de brain
    safe = html.split('const CSP_SAFE = "')[1].split('"')[0]
    assert "default-src 'none'" in safe and "script-src" not in safe and "https:" not in safe
    js = html.split('const CSP_JS = "')[1].split('"')[0]
    assert "connect-src 'none'" in js


def test_keep_awake_gives_up_when_not_allowed(monkeypatch):
    import time as _t

    from brain_mcp import services

    if os.name == "nt":
        pytest.skip("en Windows usa SetThreadExecutionState, sin proceso aparte")
    k = services.KeepAwake()
    monkeypatch.setattr(k, "_command", lambda: ["sh", "-c", "exit 1"])
    assert k.start() is True
    for _ in range(50):
        if k.proc.poll() is not None:
            break
        _t.sleep(0.05)
    assert k.start() is False and k.failed and k.proc is None
    assert k.start() is False  # no vuelve a lanzar el proceso en cada vuelta del watchdog
    k.stop()
    assert not k.failed  # apagar y prender de nuevo lo reintenta
    monkeypatch.setattr(k, "_command", lambda: ["sleep", "30"])
    assert k.start() and k.active()
    k.stop()
    assert not k.active()
