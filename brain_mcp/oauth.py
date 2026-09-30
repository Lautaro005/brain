"""OAuth para conexiones MCP por URL (la autorización que define la spec de MCP).

Muchos servers remotos (Notion, Linear, Sentry…) responden 401 hasta que el usuario inicia sesión.
El SDK de MCP ya hace el flujo completo (OAuthClientProvider): descubre el servidor de autorización
(RFC 9728 + RFC 8414), registra a brain como cliente (RFC 7591), usa PKCE y renueva el token solo.
Acá se le da lo que necesita:

- **Dónde guardar**: data/oauth.json (permisos 600, fuera del vault y de git, excluido de los
  backups por defecto), con un lock entre procesos porque cualquier server.py puede renovar un token.
- **Cómo pedir el login**: solo el dashboard lo hace (`start`). Abre la URL de autorización del
  servicio en el navegador y espera el código en http://127.0.0.1:<puerto>/oauth/callback.
- **Sin login interactivo** (agentes, llamadas proxeadas): si hace falta autorizar, se corta con
  NeedsAuth y el dashboard muestra "Iniciar sesión" en la conexión.
"""
import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import locks

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STORE_NAME = "oauth.json"
LOGIN_TIMEOUT = 300  # segundos para iniciar sesión en el servicio
CALLBACK_PATH = "/oauth/callback"


class NeedsAuth(RuntimeError):
    """La conexión necesita que el usuario inicie sesión desde el dashboard."""


def _store() -> Path:
    return DATA / STORE_NAME


def _read() -> dict:
    try:
        return json.loads(_store().read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return {}


def _update(conn_id: str, field: str, value) -> None:
    with locks.locked(DATA / ".oauth.lock"):
        data = _read()
        entry = data.setdefault(conn_id, {})
        if value is None:
            entry.pop(field, None)
        else:
            entry[field] = value
        p = _store()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        tmp.replace(p)


def has_tokens(conn_id: str) -> bool:
    return bool(_read().get(conn_id, {}).get("tokens"))


def forget(conn_id: str) -> None:
    """Borra tokens y registro de cliente (al eliminar la conexión o al cerrar sesión)."""
    with locks.locked(DATA / ".oauth.lock"):
        data = _read()
        if data.pop(conn_id, None) is not None:
            _store().write_text(json.dumps(data, indent=2), encoding="utf-8")


class _Storage:
    """TokenStorage del SDK sobre data/oauth.json."""

    def __init__(self, conn_id: str):
        self.conn_id = conn_id

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken

        t = _read().get(self.conn_id, {}).get("tokens")
        return OAuthToken.model_validate(t) if t else None

    async def set_tokens(self, tokens) -> None:
        _update(self.conn_id, "tokens", tokens.model_dump(mode="json", exclude_none=True))

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull

        c = _read().get(self.conn_id, {}).get("client")
        return OAuthClientInformationFull.model_validate(c) if c else None

    async def set_client_info(self, client_info) -> None:
        _update(self.conn_id, "client", client_info.model_dump(mode="json", exclude_none=True))


# ---------- login interactivo (solo desde el dashboard) ----------
# conn_id → {"status": starting|waiting|done|error, "url", "state", "code", "event", "error"}
_PENDING: dict[str, dict] = {}
_LOCK = threading.Lock()


def redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}{CALLBACK_PATH}"


def _redirect_for(conn_id: str, port: int | None) -> str:
    """La redirect URI con la que se registró el cliente (si ya se registró) o la del dashboard actual."""
    client = _read().get(conn_id, {}).get("client") or {}
    uris = client.get("redirect_uris") or []
    if port:
        return redirect_uri(port)
    return uris[0] if uris else redirect_uri(8765)


def provider(conn_id: str, server_url: str, port: int | None = None):
    """httpx2.Auth para la conexión. Con `port` (el del dashboard) puede pedir login; sin él, si
    hace falta autorizar levanta NeedsAuth."""
    from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    uri = _redirect_for(conn_id, port)
    if port:  # si el dashboard cambió de puerto, el cliente registrado ya no sirve: se registra de nuevo
        client = _read().get(conn_id, {}).get("client") or {}
        if client and uri not in (client.get("redirect_uris") or []):
            _update(conn_id, "client", None)
    meta = OAuthClientMetadata(
        client_name="brain", redirect_uris=[uri], grant_types=["authorization_code", "refresh_token"],
        response_types=["code"], token_endpoint_auth_method="none",
    )

    async def redirect_handler(url: str) -> None:
        if not port:
            raise NeedsAuth(conn_id)
        state = (parse_qs(urlparse(url).query).get("state") or [""])[0]
        with _LOCK:
            p = _PENDING.setdefault(conn_id, {})
            p.update(status="waiting", url=url, state=state, code=None, error=None)
            p.setdefault("event", threading.Event()).clear()

    async def callback_handler() -> AuthorizationCodeResult:
        import anyio

        p = _PENDING[conn_id]
        ok = await anyio.to_thread.run_sync(p["event"].wait, LOGIN_TIMEOUT)
        if not ok or not p.get("code"):
            raise NeedsAuth(p.get("error") or "login_timeout")
        return AuthorizationCodeResult(code=p["code"], state=p.get("state"), iss=p.get("iss"))

    return OAuthClientProvider(server_url=server_url, client_metadata=meta, storage=_Storage(conn_id),
                               redirect_handler=redirect_handler, callback_handler=callback_handler)


def start(conn_id: str, port: int) -> dict:
    """Arranca el login en un thread y espera hasta tener la URL de autorización (o que termine,
    si el servicio no pidió login). Devuelve {"status", "url"?, "error"?}."""
    from . import connectors

    with _LOCK:
        cur = _PENDING.get(conn_id)
        if cur and cur.get("status") == "waiting" and cur.get("url"):
            return {"status": "waiting", "url": cur["url"]}
        _PENDING[conn_id] = {"status": "starting", "event": threading.Event()}

    def run():
        p = _PENDING[conn_id]
        try:
            asyncio.run(connectors.discover(conn_id, port=port))
            err = connectors.cached_error(conn_id)
            p.update(status="error" if err else "done", error=err)
        except Exception as e:  # el detalle queda en el caché de la conexión
            p.update(status="error", error=str(e)[:300])

    threading.Thread(target=run, daemon=True, name=f"oauth-{conn_id}").start()
    deadline = time.time() + 25
    while time.time() < deadline:
        p = _PENDING[conn_id]
        if p.get("status") in ("waiting", "done", "error"):
            return {k: p.get(k) for k in ("status", "url", "error") if p.get(k)}
        time.sleep(0.1)
    return {"status": "starting"}


def status(conn_id: str) -> dict:
    p = _PENDING.get(conn_id) or {}
    return {"status": p.get("status") or ("done" if has_tokens(conn_id) else "none"), "error": p.get("error")}


def callback(query: str) -> tuple[bool, str]:
    """GET /oauth/callback?code=…&state=… → (ok, conn_id o motivo)."""
    q = {k: v[0] for k, v in parse_qs(query).items()}
    state = q.get("state", "")
    with _LOCK:
        conn_id = next((cid for cid, p in _PENDING.items() if state and p.get("state") == state), None)
        if not conn_id:
            return False, "unknown_state"
        p = _PENDING[conn_id]
        if q.get("error"):
            p.update(error=f"{q['error']}: {q.get('error_description', '')}".strip(": "), code=None)
        else:
            p.update(code=q.get("code"), iss=q.get("iss"))
        p["event"].set()
    return not q.get("error"), conn_id
