"""Dashboard de brain-mcp: servicios, estadísticas, grafo, conocimiento y logs en una sola página.

Uso: ./brain.sh [--port 8765] [--no-autostart] [--no-browser]
Al cerrarlo (Ctrl+C) apaga los servicios que haya prendido él.
"""
import argparse
import atexit
import json
import logging
import signal
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # si no, una línea por cada request a Chroma


import server as mcp_tools  # noqa: E402  (las mismas funciones que exponen las tools MCP)
import asyncio  # noqa: E402

from brain_mcp import agents, chat, clients, connectors, history, memory, reflect, stats, updates, vault  # noqa: E402
from brain_mcp.chroma_store import ChromaUnavailable  # noqa: E402
from brain_mcp.embeddings import OllamaUnavailable  # noqa: E402
from brain_mcp.scrape import ScrapeError  # noqa: E402
from brain_mcp.graph import build_graph  # noqa: E402
from brain_mcp.services import SERVICES, stop_all  # noqa: E402

log = logging.getLogger("dashboard")
HERE = Path(__file__).resolve().parent / "brain_mcp"
PAGES = {"/": "dashboard.html", "/index.html": "dashboard.html", "/graph": "graph.html", "/chat": "chat.html"}
ALLOWED_HOSTS: set[str] = set()
MAX_BODY = 5 * 1024 * 1024  # 5 MB alcanza para cualquier export de memoria en texto


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
    except chat.ChatError as e:
        return {"ok": False, "code": e.code, "detail": e.detail}
    except Exception as e:
        return {"ok": False, "code": _error_code(e), "detail": str(e)}
    return {"ok": False, "code": "unknown_action"}


def _connections_post(rest: list[str], body: dict) -> dict:
    """/api/connections (alta) · /<id> (editar) · /<id>/toggle · /<id>/refresh · /<id>/delete"""
    try:
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
            elif u.path == "/api/version":
                self._json({"current": updates.current(), "releases": updates.RELEASES_URL})
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

    def _stream_chat(self, body: dict) -> None:
        """Respuesta del chat como NDJSON: una línea por evento, a medida que el modelo escribe."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        gen = chat.run(str(body.get("text", "")), str(body.get("model", "")),
                       str(body.get("chat_id") or "") or None, "en" if body.get("lang") == "en" else "es")
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

    url = f"http://127.0.0.1:{args.port}"
    log.info("Dashboard en %s  (Ctrl+C apaga todo lo que prendió)", url)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Apagando servicios…")
        stop_all()


if __name__ == "__main__":
    main()
