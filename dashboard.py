"""Dashboard de brain-mcp: servicios, estadísticas, grafo, conocimiento y logs en una sola página.

Uso: ./brain.sh [--port 8765] [--no-autostart] [--no-browser]
Al cerrarlo (Ctrl+C) apaga los servicios que haya prendido él.
"""
import argparse
import atexit
import json
import logging
import os
import shutil
import signal
import sys
import threading
import time
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # si no, una línea por cada request a Chroma


import server as mcp_tools  # noqa: E402  (las mismas funciones que exponen las tools MCP)
import asyncio  # noqa: E402

from brain_mcp import agents, backup, chat, clients, connectors, history, memory, oauth, reflect, remote, stats, updates, vault  # noqa: E402
from brain_mcp.chroma_store import ChromaUnavailable  # noqa: E402
from brain_mcp.embeddings import OllamaUnavailable  # noqa: E402
from brain_mcp.scrape import ScrapeError  # noqa: E402
from brain_mcp.graph import build_graph  # noqa: E402
from brain_mcp.services import SERVICES, stop_all  # noqa: E402

log = logging.getLogger("dashboard")
HERE = Path(__file__).resolve().parent / "brain_mcp"
PAGES = {"/": "dashboard.html", "/index.html": "dashboard.html", "/graph": "graph.html", "/chat": "chat.html"}
ALLOWED_HOSTS: set[str] = set()
PORT = 8765  # se pisa en main(); lo usa OAuth para la redirect URI
MAX_BODY = 5 * 1024 * 1024  # 5 MB alcanza para cualquier export de memoria en texto
MAX_BACKUP = 8 * 1024 ** 3  # subida de un backup para restaurar
# "Actualizar y reiniciar": el dashboard sale con este código y el lanzador (brain.sh / brain.ps1, que
# exporta BRAIN_LAUNCHER=1) actualiza con git + uv y lo vuelve a abrir en la misma terminal.
RESTART_CODE = 75
_SERVER: dict = {"srv": None, "restart": False}


def _error_code(e: Exception) -> str:
    # el dashboard traduce por código; el detalle queda en el idioma original
    if isinstance(e, OllamaUnavailable):
        return "ollama"
    if isinstance(e, ChromaUnavailable):
        return "chroma"
    if isinstance(e, ScrapeError):
        return "scrape"
    return "other"


_chunks_cache: dict = {"at": 0.0, "value": None}


def _chunks(chroma_up: bool) -> int | None:
    # el panel consulta cada 2,5 s; el conteo de Chroma alcanza con refrescarlo cada 10 s
    if not chroma_up:
        _chunks_cache.update(at=0.0, value=None)
    elif time.time() - _chunks_cache["at"] > 10 or _chunks_cache["value"] is None:
        _chunks_cache.update(at=time.time(), value=stats.chroma_count())
    return _chunks_cache["value"]


def _can_self_update() -> bool:
    return os.environ.get("BRAIN_LAUNCHER") == "1" and (Path(__file__).resolve().parent / ".git").exists()


def _update_apply() -> dict:
    """Cierra el dashboard para que el lanzador actualice y lo vuelva a abrir (la página se recarga sola)."""
    if not _can_self_update():
        return {"ok": False, "code": "no_launcher"}
    _SERVER["restart"] = True
    threading.Timer(0.4, lambda: _SERVER["srv"] and _SERVER["srv"].shutdown()).start()
    return {"ok": True, "current": updates.current()}


def _remote_info() -> dict:
    t = SERVICES["tunnel"].info()
    m = SERVICES["remote_mcp"].info()
    err = None
    if "error" in (t["status"], m["status"]):  # la última línea con error de cloudflared o del server, para la tarjeta
        lines = list(SERVICES["tunnel" if t["status"] == "error" else "remote_mcp"].logs)
        err = next((ln for ln in reversed(lines) if any(w in ln for w in ("ERR", "failed", "Error", "error"))), None)
    return {"ok": True, **remote.public(t["link"] if t["status"] == "running" else None), "tunnel": t, "mcp": m,
            "error": err[-300:] if err else None}


def _remote_start() -> None:
    if not remote.cloudflared_path():
        raise remote.RemoteError("no_cloudflared")
    remote.tunnel_command()  # valida el modo (named sin token/hostname) antes de prender nada
    m, t = SERVICES["remote_mcp"], SERVICES["tunnel"]
    if m.status() in ("stopped", "error"):
        m.start()
    if not t.ours():
        t.start()
    remote.set_enabled(True)


def _remote_post(action: str, body: dict) -> dict:
    """/api/remote/start · stop · settings · regenerate · install"""
    try:
        if action == "start":
            _remote_start()
        elif action == "stop":
            SERVICES["tunnel"].stop()
            SERVICES["remote_mcp"].stop()
            remote.set_enabled(False)
        elif action == "settings":
            before = remote.load()
            after = remote.configure(body)
            t = SERVICES["tunnel"]
            if t.ours() and any(before[k] != after[k] for k in ("mode", "hostname", "tunnel_token")):
                t.stop()
                t.start()
        elif action == "regenerate":
            remote.regenerate()  # la URL vieja deja de andar en el acto: el server lee el token en cada pedido
        elif action == "install":
            remote.install_cloudflared()
        else:
            return {"ok": False, "code": "unknown_action"}
    except remote.RemoteError as e:
        return {"ok": False, "code": e.code, "detail": e.detail}
    except RuntimeError as e:  # Service.start: el puerto está ocupado por otro programa
        return {"ok": False, "code": "busy", "detail": str(e)}
    return _remote_info()


def _status() -> dict:
    services = [s.info() for s in SERVICES.values()]
    st = {s["key"]: s["status"] for s in services}
    return {
        "services": services,
        "health": stats.health(st),
        "chunks": _chunks(st.get("chroma") in ("running", "external")),
    }


def _chat_get(path: str, q: dict) -> dict:
    """/api/chat/models · /api/chat/list · /api/chat/get?id="""
    try:
        if path == "/api/chat/models":
            return {"ok": True, "models": chat.models(), "suggested": chat.SUGGESTED_MODEL}
        if path == "/api/chat/list":
            return {"ok": True, "chats": chat.list_chats()}
        if path == "/api/chat/get":
            return {"ok": True, "chat": chat.get_chat(q.get("id", [""])[0])}
        if path == "/api/chat/context":
            return _chat_context()
        if path == "/api/chat/commands":  # los skills del vault, para el menú de "/"
            return {"ok": True, "skills": chat.skills()}
        if path == "/api/chat/app_context":  # el contexto fijo sobre cómo funciona brain (Ajustes → Chat)
            return {"ok": True, "text": chat.app_guide()}
    except chat.ChatError as e:
        return {"ok": False, "code": e.code, "detail": e.detail}
    except Exception as e:
        return {"ok": False, "code": _error_code(e), "detail": str(e)}
    return {"ok": False, "code": "unknown_action"}


def _chat_context() -> dict:
    """Ajustes → Contexto: la regla guardada y cada modelo con su máximo y lo que usa."""
    settings = chat.ctx_settings()
    try:
        models, ollama = chat.models(), True
    except chat.ChatError:
        models, ollama = [], False
    return {"ok": True, "settings": settings, "models": models, "ollama": ollama, "default_cap": chat.CHAT_CTX,
            "min": chat.MIN_CTX, "max": chat.MAX_CTX_SETTING}


def _backup_post(action: str, body: dict) -> dict:
    """/api/backup/create · restore · delete · rebuild"""
    try:
        if action == "create":
            return {"ok": True, **backup.create(include_secrets=bool(body.get("include_secrets")))}
        if action == "restore":
            r = backup.restore(backup.path_of(str(body.get("name", ""))), include_secrets=bool(body.get("include_secrets", True)))
            _chunks_cache["at"] = 0.0
            return {"ok": True, **r}
        if action == "delete":
            backup.delete(str(body.get("name", "")))
            return {"ok": True}
        if action == "rebuild":
            _chunks_cache["at"] = 0.0
            return {"ok": True, **backup.rebuild_indexes()}
    except backup.BackupError as e:
        return {"ok": False, "code": e.code, "detail": e.detail}
    return {"ok": False, "code": "unknown_action"}


# "Actualizar todas" las fuentes: corre en un thread (puede tardar minutos) y el dashboard consulta
# el avance con GET /api/sources/refresh_status.
_REFRESH = {"running": False, "done": 0, "total": 0, "updated": 0, "unchanged": 0, "errors": []}
_REFRESH_LOCK = threading.Lock()


def _refresh_all_start() -> dict:
    with _REFRESH_LOCK:
        if _REFRESH["running"]:
            return dict(_REFRESH)
        paths = [f["path"] for f in vault.list_files(mcp_tools.SOURCES_DIR)]
        _REFRESH.update(running=True, done=0, total=len(paths), updated=0, unchanged=0, errors=[])

    def run():
        for p in paths:
            r = mcp_tools.refresh_source_core(p)
            with _REFRESH_LOCK:
                _REFRESH["done"] += 1
                if r["status"] == "error":
                    _REFRESH["errors"].append({"path": p, "error": r["error"]})
                else:
                    _REFRESH[r["status"]] += 1
        _chunks_cache["at"] = 0.0
        with _REFRESH_LOCK:
            _REFRESH["running"] = False

    threading.Thread(target=run, daemon=True, name="refresh-sources").start()
    return dict(_REFRESH)


def _oauth_page(ok: bool, who: str) -> str:
    """Página que ve el usuario al volver del login del servicio (en la pestaña que se abrió)."""
    import html

    title = "Listo, brain quedó conectado" if ok else "No se pudo conectar"
    sub = ("Podés cerrar esta pestaña y volver al dashboard." if ok else
           f"El servicio no autorizó la conexión ({html.escape(who)}). Volvé al dashboard e intentá de nuevo.")
    return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>brain</title><body style='font:16px/1.5 system-ui,sans-serif;display:grid;place-items:center;"
            f"min-height:90vh;margin:0;background:#f5f5f3;color:#0a0a0a'><div style='max-width:420px;padding:24px'>"
            f"<h1 style='font-size:22px;margin:0 0 8px'>{title}</h1><p style='margin:0;color:#3d3d3d'>{sub}</p></div>"
            f"<script>{'setTimeout(()=>window.close(),1500)' if ok else ''}</script>")


def _connections_post(rest: list[str], body: dict) -> dict:
    """/api/connections (alta) · /<id> (editar) · /<id>/toggle · /<id>/refresh · /<id>/delete"""
    try:
        if rest[:1] == ["proposals"] and len(rest) == 3 and rest[2] in ("approve", "reject"):
            conn = connectors.resolve_proposal(rest[1], rest[2] == "approve")
            return {"ok": True, "connection": asyncio.run(connectors.discover(conn["id"])) if conn else None}
        if not rest:
            conn = connectors.upsert(body)
            return {"ok": True, "connection": asyncio.run(connectors.discover(conn["id"]))}
        cid, action = rest[0], (rest[1] if len(rest) > 1 else "update")
        if action == "update":
            connectors.upsert(body, cid)
            return {"ok": True, "connection": asyncio.run(connectors.discover(cid))}
        if action == "toggle":
            return {"ok": True, "connection": connectors.set_flag(cid, str(body.get("field")), bool(body.get("value")))}
        if action == "refresh":
            return {"ok": True, "connection": asyncio.run(connectors.discover(cid))}
        if action == "delete":
            connectors.delete(cid)
            return {"ok": True}
        if action == "authorize":  # login OAuth: devuelve la URL del servicio para abrir en otra pestaña
            connectors.get(cid)
            return {"ok": True, **oauth.start(cid, PORT)}
        if action == "auth_status":
            return {"ok": True, **oauth.status(cid), "connection": connectors.public(connectors.get(cid))}
        if action == "signout":
            oauth.forget(cid)
            return {"ok": True, "connection": asyncio.run(connectors.discover(cid))}
        return {"ok": False, "code": "unknown_action"}
    except connectors.ConnectorError as e:
        return {"ok": False, "code": e.code, "detail": e.detail}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        # evita DNS rebinding: solo se aceptan pedidos dirigidos a 127.0.0.1/localhost
        return self.headers.get("Host", "") in ALLOWED_HOSTS

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in PAGES:
                self._send(200, (HERE / PAGES[u.path]).read_bytes(), "text/html; charset=utf-8")
            elif u.path == oauth.CALLBACK_PATH:
                ok, who = oauth.callback(u.query)
                self._send(200, _oauth_page(ok, who).encode(), "text/html; charset=utf-8")
            elif u.path == "/ui.js":
                self._send(200, (HERE / "ui.js").read_bytes(), "text/javascript; charset=utf-8")
            elif u.path == "/api/status":
                self._json(_status())
            elif u.path == "/api/stats":
                self._json(stats.vault_stats())
            elif u.path == "/api/graph":
                self._json(build_graph())
            elif u.path == "/api/file":
                meta, content = vault.parse(vault.read_file(q.get("path", [""])[0]))
                self._json({"meta": meta, "content": content})
            elif u.path == "/api/profile":
                self._json({"profile": memory.get_profile(), "memory": memory.list_all()})
            elif u.path == "/api/connections":
                self._json({"connections": [connectors.public(c) for c in connectors.load()],
                            "proposals": connectors.proposals(),
                            "composio": connectors.composio_status()})
            elif u.path == "/api/composio/auth_configs":
                try:
                    self._json({"ok": True, "items": connectors.composio_auth_configs()})
                except connectors.ConnectorError as e:
                    self._json({"ok": False, "code": e.code, "detail": e.detail})
            elif u.path.startswith("/api/chat/"):
                self._json(_chat_get(u.path, q))
            elif u.path == "/api/agents":
                st = agents.all_status()
                self._json({"agents": st, "manual": agents.manual_snippets(), "mine": clients.listing(st)})
            elif u.path == "/api/backup/list":
                self._json({"ok": True, "backups": backup.listing()})
            elif u.path == "/api/backup/download":
                try:
                    p = backup.path_of(q.get("name", [""])[0])
                except backup.BackupError as e:
                    return self._json({"ok": False, "code": e.code}, 404)
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(p.stat().st_size))
                self.send_header("Content-Disposition", f'attachment; filename="{p.name}"')
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with open(p, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)
            elif u.path == "/api/sources/refresh_status":
                with _REFRESH_LOCK:
                    self._json(dict(_REFRESH))
            elif u.path == "/api/version":
                self._json({"current": updates.current(), "releases": updates.RELEASES_URL,
                            "can_update": _can_self_update(), "update_failed": os.environ.get("BRAIN_UPDATE_FAILED") == "1"})
            elif u.path == "/api/activity":  # para las notificaciones del sistema (Ajustes → Notificaciones)
                self._json(history.since(int(q.get("after", ["-1"])[0])))
            elif u.path == "/api/remote":
                self._json(_remote_info())
            elif u.path == "/api/history":
                self._json({"changes": history.list_changes(path=q.get("path", [None])[0], limit=int(q.get("limit", ["50"])[0]))})
            elif u.path.startswith("/api/logs/"):
                s = SERVICES.get(u.path.rsplit("/", 1)[-1])
                self._json({"lines": list(s.logs)} if s else {"error": "servicio desconocido"}, 200 if s else 404)
            else:
                self._send(404, b"not found", "text/plain")
        except vault.VaultError as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:
            log.exception("GET %s", u.path)
            self._json({"error": str(e)}, 500)

    def do_POST(self) -> None:
        # el header propio obliga a un preflight CORS que este server nunca aprueba,
        # así ninguna otra página abierta en el navegador puede disparar acciones acá
        if not self._host_ok() or self.headers.get("X-Brain") != "1":
            return self._send(403, b"forbidden", "text/plain")
        u = urlparse(self.path)
        if u.path == "/api/backup/upload":  # un .zip crudo, puede pesar GB: se escribe a disco en partes
            return self._backup_upload()
        try:
            size = int(self.headers.get("Content-Length") or 0)
            if size > MAX_BODY:
                return self._json({"error": "too_large"}, 413)
            body = json.loads(self.rfile.read(size) or b"{}")
            parts = u.path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "services"] and parts[3] in ("start", "stop"):
                s = SERVICES.get(parts[2])
                if not s:
                    return self._json({"error": "servicio desconocido"}, 404)
                s.start() if parts[3] == "start" else s.stop()
                self._json({"ok": True, "service": s.info()})
            elif u.path == "/api/save_url":
                url = str(body.get("url", "")).strip()
                if not url.startswith(("http://", "https://")):
                    return self._json({"ok": False, "code": "bad_url"})
                try:
                    r = mcp_tools.save_url_core(url, render_js=bool(body.get("render_js")))
                except Exception as e:
                    return self._json({"ok": False, "code": _error_code(e), "detail": str(e)})
                finally:
                    _chunks_cache["at"] = 0.0
                self._json({"ok": True, **r})
            elif u.path == "/api/sources/refresh":
                path = str(body.get("path", ""))
                if not path.startswith(mcp_tools.SOURCES_DIR + "/"):
                    return self._json({"ok": False, "code": "bad_path"})
                r = mcp_tools.refresh_source_core(path)
                _chunks_cache["at"] = 0.0
                self._json({"ok": r["status"] != "error", **r})
            elif parts[:2] == ["api", "backup"] and len(parts) == 3:
                self._json(_backup_post(parts[2], body))
            elif u.path == "/api/sources/refresh_all":
                self._json({"ok": True, **_refresh_all_start()})
            elif u.path == "/api/search":
                try:
                    hits, _, down = mcp_tools.search_core(str(body.get("query", "")), max(1, min(int(body.get("top_k", 5)), 20)))
                except Exception as e:
                    return self._json({"ok": False, "code": _error_code(e), "detail": str(e)})
                self._json({"ok": True, "down": down, "hits": [
                    {"text": h["text"], "url": h["url"], "path": h["source_md_path"],
                     "score": h["score"], "match": h["match"]} for h in hits]})
            elif u.path == "/api/search/reindex":
                self._json({"ok": True, **mcp_tools.reindex_keyword_core()})
            elif u.path == "/api/reflect":
                self._json({"ok": True, **reflect.run()})
            elif parts[:2] == ["api", "remote"] and len(parts) == 3:
                self._json(_remote_post(parts[2], body))
            elif u.path == "/api/update/apply":
                self._json(_update_apply())
            elif u.path == "/api/version/check":
                try:
                    self._json({"ok": True, **updates.check()})
                except updates.UpdateError as e:
                    self._json({"ok": False, "code": e.code, "detail": e.detail, "current": updates.current()})
            elif u.path == "/api/profile":
                path = memory.save_profile(str(body.get("name", "")), str(body.get("headline", "")), str(body.get("about", "")))
                self._json({"ok": True, "path": path})
            elif u.path == "/api/memory/parse":
                self._json({"ok": True, "categories": memory.parse(str(body.get("text", "")))})
            elif u.path == "/api/memory/import":
                source = str(body.get("source", "other"))
                if source not in memory.SOURCES:
                    return self._json({"error": "fuente desconocida"}, 400)
                self._json({"ok": True, **memory.import_categories(body.get("categories") or [], source)})
            elif len(parts) == 4 and parts[:2] == ["api", "agents"] and parts[3] in ("connect", "disconnect"):
                try:
                    st = agents.set_connected(parts[2], parts[3] == "connect", bool(body.get("restart_app")))
                except agents.AgentError as e:
                    return self._json({"ok": False, "code": e.code, "detail": e.detail})
                self._json({"ok": True, "agent": st})
            elif u.path == "/api/chat/send":
                self._stream_chat(body)
            elif u.path == "/api/chat/delete":
                try:
                    chat.delete_chat(str(body.get("id", "")))
                    self._json({"ok": True})
                except Exception as e:
                    self._json({"ok": False, "code": _error_code(e), "detail": str(e)})
            elif u.path == "/api/chat/context":
                try:
                    chat.save_ctx_settings(body)
                    self._json(_chat_context())
                except chat.ChatError as e:
                    self._json({"ok": False, "code": e.code, "detail": e.detail})
            elif u.path == "/api/chat/rename":
                try:
                    self._json({"ok": True, "title": chat.rename_chat(str(body.get("id", "")), str(body.get("title", "")))})
                except chat.ChatError as e:
                    self._json({"ok": False, "code": e.code, "detail": e.detail})
                except Exception as e:
                    self._json({"ok": False, "code": _error_code(e), "detail": str(e)})
            elif u.path == "/api/chat/pull":
                try:
                    chat.pull(str(body.get("model") or chat.SUGGESTED_MODEL))
                    self._json({"ok": True})
                except chat.ChatError as e:
                    self._json({"ok": False, "code": e.code, "detail": e.detail})
            elif u.path == "/api/clients/add":
                try:
                    self._json({"ok": True, "client": clients.add_manual(str(body.get("name", "")))})
                except ValueError as e:
                    self._json({"ok": False, "code": str(e)})
            elif u.path == "/api/clients/remove":
                clients.remove(str(body.get("id", "")))
                self._json({"ok": True})
            elif parts[:2] == ["api", "connections"]:
                self._json(_connections_post(parts[2:], body))
            elif u.path == "/api/composio/config":
                try:
                    st = connectors.composio_configure(body.get("api_key"), body.get("user_id"))
                    conn = asyncio.run(connectors.discover(st["connection_id"])) if st["connection_id"] and st["key_kind"] == "consumer" else None
                    self._json({"ok": True, "composio": st, "connection": conn})
                except connectors.ConnectorError as e:
                    self._json({"ok": False, "code": e.code, "detail": e.detail})
            elif u.path == "/api/composio/disconnect":
                self._json({"ok": True, "composio": connectors.composio_disconnect()})
            elif u.path == "/api/composio/provision":
                try:
                    conn = connectors.composio_provision([str(x) for x in body.get("auth_config_ids") or []])
                    self._json({"ok": True, "connection": asyncio.run(connectors.discover(conn["id"]))})
                except connectors.ConnectorError as e:
                    self._json({"ok": False, "code": e.code, "detail": e.detail})
            elif u.path == "/api/memory/delete":
                self._json({"ok": True, **memory.remove_item(str(body.get("category", "")), str(body.get("item", "")))})
            else:
                self._send(404, b"not found", "text/plain")
        except vault.VaultError as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:
            log.exception("POST %s", u.path)
            self._json({"error": str(e)}, 500)

    def _backup_upload(self) -> None:
        size = int(self.headers.get("Content-Length") or 0)
        if not 0 < size <= MAX_BACKUP:
            return self._json({"ok": False, "code": "too_large" if size else "empty"}, 413 if size else 400)
        dest = backup.backups_dir() / f"brain-backup-uploaded-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        left = size
        with open(dest, "wb") as f:
            while left:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        try:
            with zipfile.ZipFile(dest) as z:
                man = backup._check(z)
        except (zipfile.BadZipFile, backup.BackupError) as e:
            dest.unlink(missing_ok=True)
            code = e.code if isinstance(e, backup.BackupError) else "not_a_backup"
            return self._json({"ok": False, "code": code, "detail": str(e)})
        self._json({"ok": True, "name": dest.name, "manifest": man})

    def _stream_chat(self, body: dict) -> None:
        """Respuesta del chat como NDJSON: una línea por evento, a medida que el modelo escribe."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        gen = chat.run(str(body.get("text", "")), str(body.get("model", "")),
                       str(body.get("chat_id") or "") or None, "en" if body.get("lang") == "en" else "es",
                       str(body.get("system") or ""))
        try:
            for ev in gen:
                self.wfile.write((json.dumps(ev, ensure_ascii=False, default=str) + "\n").encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # el usuario cortó la respuesta: run() guarda lo que haya
        finally:
            gen.close()
        self.close_connection = True

    def log_message(self, *args) -> None:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Dashboard de brain-mcp")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-autostart", action="store_true", help="no prender Ollama/Chroma al arrancar")
    ap.add_argument("--no-browser", action="store_true", help="no abrir el navegador")
    args = ap.parse_args()

    ALLOWED_HOSTS.update({f"127.0.0.1:{args.port}", f"localhost:{args.port}"})
    global PORT
    PORT = args.port
    vault.ensure_vault()
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        sys.exit(f"El puerto {args.port} está ocupado (¿ya hay un dashboard abierto?). Otro: ./brain.sh --port 8766")

    atexit.register(stop_all)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    if not args.no_autostart:
        for key in ("ollama", "chroma"):
            s = SERVICES[key]
            if s.status() in ("stopped", "error"):
                try:
                    s.start()
                    log.info("%s prendido", s.label)
                except Exception as e:
                    log.warning("No se pudo prender %s: %s", s.label, e)
            else:
                log.info("%s ya estaba corriendo", s.label)
        if remote.load()["enabled"]:  # el acceso remoto quedó prendido la última vez
            try:
                _remote_start()
                log.info("Acceso remoto prendido (túnel de Cloudflare)")
            except Exception as e:
                log.warning("No se pudo prender el acceso remoto: %s", e)

    url = f"http://127.0.0.1:{args.port}"
    log.info("Dashboard en %s  (Ctrl+C apaga todo lo que prendió)", url)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    _SERVER["srv"] = srv
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Apagando servicios…")
        stop_all()
        srv.server_close()
    if _SERVER["restart"]:
        log.info("Cerrando para actualizar; el lanzador vuelve a abrir brain.")
        sys.exit(RESTART_CODE)


if __name__ == "__main__":
    main()
