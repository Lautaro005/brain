"""Conexiones: brain como cliente MCP de otros servers, que re-expone sus tools como propias.

- Tipos de conexión: "stdio" (un comando local, ej. un server MCP oficial de Gmail/Notion/GitHub),
  "http" (un server MCP remoto por URL) y "composio" (un server MCP creado en Composio).
- La config vive en data/connections.json (fuera del vault y de git). Los secretos (valores de
  env y de headers, API key de Composio) van a ROOT/.env con permisos 600 y la config solo guarda
  una referencia "$env:NOMBRE". Nunca se devuelven al dashboard.
- Las tools de cada conexión se descubren (list_tools) y se cachean en data/connections_tools.json,
  así server.py las lista sin tener que conectarse a nada al arrancar.
- Cada llamada abre una sesión con el server de origen, ejecuta y cierra (simple y robusto; un
  server stdio se relanza en cada llamada).
- Auto-captura: el resultado de una tool proxeada se guarda en vault/knowledge/<...>.md y se
  indexa en Chroma, así queda buscable con search_knowledge y aparece en el grafo.
"""
import hashlib
import json
import logging
import os
import re
import time
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import frontmatter
import httpx

from . import chroma_store, vault
from .chunking import chunk_text

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG = DATA / "connections.json"
CACHE = DATA / "connections_tools.json"
ENV_PATH = ROOT / ".env"

COMPOSIO_API = "https://backend.composio.dev"
# Composio tiene dos tipos de key:
# - "ck_…" (consumer): se usa directo contra su MCP, con el header x-consumer-api-key. No puede
#   llamar a la API de desarrollador; las apps conectadas en Composio ya vienen incluidas.
# - "ak_…" (proyecto/desarrollador): API completa con x-api-key; hay que elegir auth configs y
#   crear un server MCP (POST /api/v3.1/mcp/servers).
COMPOSIO_CONSUMER_MCP = "https://connect.composio.dev/mcp"
KINDS = ("stdio", "http", "composio")
SEP = "__"  # nombre proxeado: <prefijo>__<tool original>
CALL_TIMEOUT = 120
MAX_CAPTURE_CHARS = 200_000


class ConnectorError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code, self.detail = code, detail


# ---------- .env (secretos) ----------

def _read_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            out[k.strip()] = v
    return out


def _write_env(values: dict[str, str | None]) -> None:
    """Actualiza claves del .env (None borra). Mantiene el resto del archivo."""
    env = _read_env()
    for k, v in values.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    lines = ["# Secretos locales de brain (conexiones, Composio). Nunca se commitea."]
    lines += [f"{k}={json.dumps(v)}" for k, v in sorted(env.items())]
    ENV_PATH.write_text("\n".join(lines) + "\n")
    os.chmod(ENV_PATH, 0o600)


def _env_key(conn_id: str, name: str) -> str:
    return "BRAIN_" + re.sub(r"[^A-Z0-9]+", "_", f"{conn_id}_{name}".upper()).strip("_")


def _resolve(value: str, env: dict[str, str]) -> str:
    return env.get(value[5:], "") if isinstance(value, str) and value.startswith("$env:") else value


# ---------- config ----------

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:24] or "conn"


def load() -> list[dict]:
    if not CONFIG.exists():
        return []
    try:
        return json.loads(CONFIG.read_text()).get("connections", [])
    except json.JSONDecodeError:
        log.warning("data/connections.json está roto; se ignora")
        return []


def _save(conns: list[dict]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps({"connections": conns}, indent=2, ensure_ascii=False))
    tmp.replace(CONFIG)


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text()) if CACHE.exists() else {}
    except json.JSONDecodeError:
        return {}


def _save_cache(cache: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def get(conn_id: str) -> dict:
    for c in load():
        if c["id"] == conn_id:
            return c
    raise ConnectorError("unknown", conn_id)


def public(c: dict) -> dict:
    """Vista para el dashboard: sin secretos (solo qué claves están cargadas)."""
    env = _read_env()
    cache = _load_cache().get(c["id"], {})
    return {
        "id": c["id"], "name": c["name"], "kind": c["kind"], "enabled": c.get("enabled", True),
        "capture": c.get("capture", True), "prefix": c["prefix"],
        "command": c.get("command", ""), "args": c.get("args", []), "url": _mask_url(c.get("url", "")),
        "env_keys": {k: bool(_resolve(v, env)) for k, v in (c.get("env") or {}).items()},
        "header_keys": {k: bool(_resolve(v, env)) for k, v in (c.get("headers") or {}).items()},
        "tools": [t["name"] for t in cache.get("tools", [])],
        "refreshed_at": cache.get("refreshed_at"), "error": cache.get("error"),
        "local": c["kind"] == "stdio",
    }


def _mask_url(url: str) -> str:
    # user_id y parámetros pueden ser sensibles: se muestran enmascarados
    if not url:
        return ""
    u = urlparse(url)
    q = "&".join(f"{k}=…" for k, _ in parse_qsl(u.query))
    return urlunparse(u._replace(query=q))


def upsert(data: dict, conn_id: str | None = None) -> dict:
    """Crea o edita una conexión. Secretos vacíos = conservar el valor guardado."""
    conns = load()
    kind = data.get("kind", "stdio")
    if kind not in KINDS:
        raise ConnectorError("bad_kind", kind)
    name = str(data.get("name", "")).strip()
    if not name:
        raise ConnectorError("missing_name")
    current = next((c for c in conns if c["id"] == conn_id), None) if conn_id else None
    cid = current["id"] if current else _unique_id(_slug(name), conns)
    conn = dict(current or {"id": cid, "created_at": _now(), "enabled": True, "capture": True})
    conn.update(name=name, kind=kind, prefix=_slug(str(data.get("prefix") or name)))
    secrets: dict[str, str | None] = {}

    def secret_map(field: str, incoming: dict) -> dict:
        old = (current or {}).get(field) or {}
        out = {}
        for k, v in (incoming or {}).items():
            k = str(k).strip()
            if not k:
                continue
            ref = f"$env:{_env_key(cid, (field[0] + '_') + k)}"
            if v:  # valor nuevo
                secrets[ref[5:]] = str(v)
                out[k] = ref
            elif k in old:  # vacío: se conserva
                out[k] = old[k]
        for k, v in old.items():  # claves sacadas: se borran del .env
            if k not in out and isinstance(v, str) and v.startswith("$env:"):
                secrets[v[5:]] = None
        return out

    if kind == "stdio":
        cmd = str(data.get("command", "")).strip()
        if not cmd:
            raise ConnectorError("missing_command")
        args = data.get("args") or []
        if isinstance(args, str):
            args = [a for a in (l.strip() for l in args.splitlines()) if a]
        conn.update(command=cmd, args=[str(a) for a in args], env=secret_map("env", data.get("env") or {}))
        conn.pop("url", None); conn.pop("headers", None)
    else:
        url = str(data.get("url", "")).strip() or (current or {}).get("url", "")
        if not url.startswith(("http://", "https://")):
            raise ConnectorError("bad_url")
        conn.update(url=url, headers=secret_map("headers", data.get("headers") or {}))
        conn.pop("command", None); conn.pop("args", None); conn.pop("env", None)
    if secrets:
        _write_env(secrets)
    conns = [c for c in conns if c["id"] != cid] + [conn]
    _save(conns)
    return public(conn)


def _unique_id(base: str, conns: list[dict]) -> str:
    ids, cid, n = {c["id"] for c in conns}, base, 2
    while cid in ids:
        cid, n = f"{base}_{n}", n + 1
    return cid


def set_flag(conn_id: str, field: str, value: bool) -> dict:
    if field not in ("enabled", "capture"):
        raise ConnectorError("bad_field", field)
    conns = load()
    for c in conns:
        if c["id"] == conn_id:
            c[field] = bool(value)
            _save(conns)
            return public(c)
    raise ConnectorError("unknown", conn_id)


def delete(conn_id: str) -> None:
    conns = load()
    c = next((x for x in conns if x["id"] == conn_id), None)
    if not c:
        raise ConnectorError("unknown", conn_id)
    refs = [v for f in ("env", "headers") for v in (c.get(f) or {}).values()]
    _write_env({v[5:]: None for v in refs if isinstance(v, str) and v.startswith("$env:")})
    _save([x for x in conns if x["id"] != conn_id])
    cache = _load_cache()
    cache.pop(conn_id, None)
    _save_cache(cache)


# ---------- cliente MCP ----------

async def _open(stack: AsyncExitStack, c: dict):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = _read_env()
    if c["kind"] == "stdio":
        extra = {k: _resolve(v, env) for k, v in (c.get("env") or {}).items()}
        params = StdioServerParameters(command=c["command"], args=c.get("args", []),
                                       env={**os.environ, **extra}, cwd=str(ROOT))
        read, write = await stack.enter_async_context(stdio_client(params))
    else:
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        headers = {k: _resolve(v, env) for k, v in (c.get("headers") or {}).items()}
        client = await stack.enter_async_context(httpx2.AsyncClient(headers=headers, timeout=CALL_TIMEOUT))
        read, write = await stack.enter_async_context(streamable_http_client(c["url"], http_client=client))
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return session


async def discover(conn_id: str) -> dict:
    """Se conecta, lista las tools y las guarda en el caché."""
    c = get(conn_id)
    cache = _load_cache()
    try:
        async with AsyncExitStack() as stack:
            session = await _open(stack, c)
            tools, cursor = [], None
            while True:
                from mcp.types import PaginatedRequestParams

                res = await session.list_tools(params=PaginatedRequestParams(cursor=cursor) if cursor else None)
                for t in res.tools:
                    schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {"type": "object"}
                    tools.append({"name": t.name, "description": t.description or "",
                                  "input_schema": schema if isinstance(schema, dict) else dict(schema)})
                cursor = getattr(res, "next_cursor", None) or getattr(res, "nextCursor", None)
                if not cursor:
                    break
        cache[conn_id] = {"tools": tools, "refreshed_at": _now(), "error": None}
    except Exception as e:  # el error queda visible en el dashboard
        msg = _short(e)
        if c["kind"] != "stdio":
            msg = _http_probe(c) or msg
        log.warning("No se pudo descubrir %s: %s", conn_id, msg)
        prev = cache.get(conn_id, {})
        cache[conn_id] = {"tools": prev.get("tools", []), "refreshed_at": prev.get("refreshed_at"), "error": msg}
    _save_cache(cache)
    return public(c)


def _http_probe(c: dict) -> str | None:
    """El cliente MCP no expone el status HTTP: un initialize a mano lo recupera (ej. 401 por key inválida)."""
    env = _read_env()
    headers = {k: _resolve(v, env) for k, v in (c.get("headers") or {}).items()}
    headers.update({"Accept": "application/json, text/event-stream", "Content-Type": "application/json"})
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "brain", "version": "0.1"}}}
    try:
        r = httpx.post(c["url"], headers=headers, json=body, timeout=15)
    except httpx.HTTPError as e:
        return f"No se pudo conectar a {urlparse(c['url']).netloc}: {e}"
    if r.status_code >= 400:
        return f"HTTP {r.status_code} de {urlparse(c['url']).netloc}: {r.text.strip()[:240]}"
    return None


def _short(e: Exception) -> str:
    # anyio agrupa errores en ExceptionGroup: se muestra el primero, que es el útil
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return (f"{type(e).__name__}: {e}")[:300]


def proxied_tools() -> list[dict]:
    """Tools de las conexiones habilitadas, con nombre prefijado. Para list_tools del server."""
    cache = _load_cache()
    out = []
    for c in load():
        if not c.get("enabled", True):
            continue
        for t in cache.get(c["id"], {}).get("tools", []):
            out.append({
                "name": f"{c['prefix']}{SEP}{t['name']}"[:128],
                "description": f"[{c['name']}] {t['description']}".strip(),
                "input_schema": t.get("input_schema") or {"type": "object"},
                "conn_id": c["id"], "tool": t["name"],
            })
    return out


def resolve_tool(name: str) -> dict | None:
    return next((t for t in proxied_tools() if t["name"] == name), None)


async def call(conn_id: str, tool: str, arguments: dict) -> tuple[list, bool]:
    """Ejecuta la tool en el server de origen. Devuelve (content, is_error)."""
    c = get(conn_id)
    if not c.get("enabled", True):
        raise ConnectorError("disabled", c["name"])
    async with AsyncExitStack() as stack:
        session = await _open(stack, c)
        res = await session.call_tool(tool, arguments or {}, read_timeout_seconds=CALL_TIMEOUT)
    content = list(getattr(res, "content", []) or [])
    is_error = bool(getattr(res, "is_error", False) or getattr(res, "isError", False))
    if not is_error and c.get("capture", True):
        import anyio

        try:  # en un thread: embeber e indexar es bloqueante y no debe trabar el event loop
            await anyio.to_thread.run_sync(capture, c, tool, arguments, content)
        except Exception as e:  # capturar nunca rompe la llamada
            log.warning("No se pudo capturar %s/%s: %s", conn_id, tool, e)
    return content, is_error


# ---------- auto-captura ----------

def _text_of(content: list) -> str:
    parts = []
    for item in content:
        t = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
        if t == "text":
            parts.append(getattr(item, "text", None) or item.get("text", ""))
        elif t == "resource":
            res = getattr(item, "resource", None) or item.get("resource", {})
            txt = getattr(res, "text", None) or (res.get("text") if isinstance(res, dict) else None)
            if txt:
                parts.append(txt)
    text = "\n\n".join(p for p in parts if p).strip()
    # JSON crudo se re-formatea para que sea legible y chunkeable
    try:
        text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        pass
    return text[:MAX_CAPTURE_CHARS]


def _toolkit(c: dict, tool: str) -> str:
    if c["kind"] == "composio" and "_" in tool:
        return tool.split("_", 1)[0].lower()  # GMAIL_FETCH_EMAILS -> gmail
    return c["prefix"]


def capture(c: dict, tool: str, arguments: dict, content: list) -> str | None:
    text = _text_of(content)
    if len(text.split()) < 5:
        return None
    base = "composio" if c["kind"] == "composio" else "connections"
    ts = _now()
    digest = hashlib.sha1(f"{tool}{json.dumps(arguments, sort_keys=True)}{ts}".encode()).hexdigest()[:6]
    slug = f"{_slug(tool)}-{ts[:10]}-{digest}"
    md_path = f"knowledge/{base}/{_toolkit(c, tool)}/{slug}.md"
    chunks = chunk_text(text)
    ids = [f"{base}-{slug}-{i}" for i in range(len(chunks))]
    indexed = True
    try:
        chroma_store.upsert(ids, chunks, [{"url": f"mcp://{c['id']}/{tool}", "source_md_path": md_path, "chunk_index": i}
                                          for i in range(len(chunks))])
    except Exception as e:  # sin Chroma/Ollama el .md igual se guarda
        indexed, ids = False, []
        log.warning("Captura sin indexar (%s): %s", md_path, e)
    post = frontmatter.Post(
        text, name=slug, description=f"{c['name']} · {tool}", connection=c["id"], tool=tool,
        args=json.dumps(arguments or {}, ensure_ascii=False)[:2000], fetched_at=ts,
        chroma_ids=ids, indexed=indexed,
    )
    vault.write_file(md_path, frontmatter.dumps(post) + "\n")
    return md_path


# ---------- Composio (opcional) ----------
# Composio guarda los tokens OAuth de las cuentas conectadas en SU nube: en local quedan la API
# key (.env), la config y todo lo capturado.

def _key_kind(key: str) -> str:
    return "consumer" if key.startswith("ck_") else "project"


def composio_status() -> dict:
    env = _read_env()
    key = env.get("COMPOSIO_API_KEY", "")
    conn = next((c for c in load() if c["kind"] == "composio"), None)
    return {"configured": bool(key), "key_kind": _key_kind(key) if key else None,
            "user_id": env.get("COMPOSIO_USER_ID", ""), "connection_id": conn["id"] if conn else None}


def composio_configure(api_key: str | None, user_id: str | None) -> dict:
    """Guarda la key. Con una consumer key (ck_) crea la conexión directo: no hay nada que elegir."""
    vals: dict[str, str | None] = {}
    if api_key:
        vals["COMPOSIO_API_KEY"] = api_key.strip()
    if user_id is not None and user_id.strip():
        vals["COMPOSIO_USER_ID"] = user_id.strip()
    if vals:
        _write_env(vals)
    key = _read_env().get("COMPOSIO_API_KEY", "")
    if key and _key_kind(key) == "consumer":
        existing = next((c for c in load() if c["kind"] == "composio"), None)
        upsert({"kind": "composio", "name": "Composio", "prefix": "composio", "url": COMPOSIO_CONSUMER_MCP,
                "headers": {"x-consumer-api-key": key}}, existing["id"] if existing else None)
    return composio_status()


def composio_disconnect() -> dict:
    """Borra la conexión de Composio y la key/user_id del .env."""
    for c in load():
        if c["kind"] == "composio":
            delete(c["id"])
    _write_env({"COMPOSIO_API_KEY": None, "COMPOSIO_USER_ID": None})
    return composio_status()


def _composio(method: str, path: str, **kw) -> dict:
    key = _read_env().get("COMPOSIO_API_KEY")
    if not key:
        raise ConnectorError("composio_not_configured")
    if _key_kind(key) == "consumer":
        # la API de desarrollador no acepta consumer keys (respondería 401)
        raise ConnectorError("composio_consumer_key")
    try:
        r = httpx.request(method, COMPOSIO_API + path, headers={"x-api-key": key}, timeout=30, **kw)
    except httpx.HTTPError as e:
        raise ConnectorError("composio_unreachable", str(e)) from e
    if r.status_code >= 400:
        raise ConnectorError("composio_http", f"{r.status_code}: {r.text[:300]}")
    return r.json()


def composio_auth_configs() -> list[dict]:
    out, cursor = [], None
    for _ in range(20):  # paginación defensiva
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        data = _composio("GET", "/api/v3.1/auth_configs", params=params)
        for it in data.get("items", data if isinstance(data, list) else []):
            tk = it.get("toolkit") or {}
            out.append({"id": it.get("id"), "name": it.get("name") or tk.get("slug") or it.get("id"),
                        "toolkit": tk.get("slug") or it.get("toolkit_slug") or "",
                        "status": it.get("status", ""), "logo": tk.get("logo")})
        cursor = data.get("next_cursor") if isinstance(data, dict) else None
        if not cursor:
            break
    return out


def composio_provision(auth_config_ids: list[str], name: str = "brain") -> dict:
    """Crea el server MCP en Composio con esas auth configs y lo guarda como conexión 'composio'."""
    if not auth_config_ids:
        raise ConnectorError("composio_nothing_selected")
    user_id = _read_env().get("COMPOSIO_USER_ID")
    if not user_id:
        raise ConnectorError("composio_no_user")
    srv_name = re.sub(r"[^A-Za-z0-9 -]", "", f"{name} {time.strftime('%m%d%H%M')}")[:30].strip()
    data = _composio("POST", "/api/v3.1/mcp/servers", json={"name": srv_name, "auth_config_ids": auth_config_ids})
    base = data.get("mcp_url") or f"{COMPOSIO_API}/v3/mcp/{data.get('id')}"
    u = urlparse(base)
    q = dict(parse_qsl(u.query))
    q["user_id"] = user_id
    url = urlunparse(u._replace(query=urlencode(q)))
    existing = next((c for c in load() if c["kind"] == "composio"), None)
    conn = upsert({"kind": "composio", "name": "Composio", "prefix": "composio", "url": url,
                   "headers": {"x-api-key": _read_env()["COMPOSIO_API_KEY"]}},
                  existing["id"] if existing else None)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
