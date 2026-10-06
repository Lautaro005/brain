"""Acceso remoto: el server MCP de brain por URL, publicado con un túnel de Cloudflare.

Así brain deja de ser solo local: agentes que corren en la nube (Manus, Claude.ai, ChatGPT…) o en otra
máquina se conectan con una URL. Dos procesos, los dos manejados por el dashboard (services.py):

- `server.py --http`: el mismo server MCP, por streamable HTTP, escuchando SOLO en 127.0.0.1:8770.
- `cloudflared`: publica ese puerto en internet. Modo "quick" (sin cuenta): URL *.trycloudflare.com
  que cambia cada vez que se prende. Modo "named": un túnel de la cuenta de Cloudflare del usuario
  (token del túnel + hostname propio), con URL fija.

Seguridad: la URL pública lleva un token secreto (`https://…/<token>/mcp`); sin él, todo da 404. También
se acepta `Authorization: Bearer <token>` en `/mcp`. El token se puede regenerar (la URL vieja deja de
andar en el acto, sin reiniciar nada) y hay un modo de solo lectura. Config en data/remote.json (600).
"""
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import shutil
import stat
import tarfile
import tempfile
from pathlib import Path

from . import locks

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG = DATA / "remote.json"
BIN = DATA / "bin"
PORT = int(os.environ.get("BRAIN_REMOTE_PORT", "8770"))
MCP_PATH = "/mcp"
DEFAULTS = {"token": "", "enabled": False, "read_only": False, "mode": "quick", "hostname": "", "tunnel_token": "",
            "keep_awake": True}
CF_RELEASES = "https://github.com/cloudflare/cloudflared/releases"
CF_API = "https://api.github.com/repos/cloudflare/cloudflared/releases/latest"
QUICK_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class RemoteError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code, self.detail = code, detail


# ---------- config ----------

_CACHE: dict = {"key": None, "value": None}


def load() -> dict:
    """Se lee en cada pedido del server --http (token y solo lectura al día sin reiniciar): se cachea por
    mtime y tamaño del archivo, así no se parsea el JSON varias veces por cada mensaje MCP."""
    try:
        st = CONFIG.stat()
        key = (str(CONFIG), st.st_ino, st.st_mtime_ns, st.st_size)  # _save usa os.replace: cada escritura es otro inodo
    except OSError:
        key = None
    if key is not None and _CACHE["key"] == key:
        return dict(_CACHE["value"])
    try:
        data = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        data = {}
    cfg = {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}
    _CACHE.update(key=key, value=cfg)
    return dict(cfg)


def _save(cfg: dict) -> dict:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG)
    return cfg


def _update(**changes) -> dict:
    with locks.locked(DATA / ".remote.lock"):
        cfg = load()
        cfg.update(changes)
        if not cfg["token"]:
            cfg["token"] = secrets.token_urlsafe(24)
        return _save(cfg)


def token() -> str:
    cfg = load()
    return cfg["token"] or _update()["token"]


def regenerate() -> str:
    return _update(token=secrets.token_urlsafe(24))["token"]


def set_enabled(on: bool) -> dict:
    return _update(enabled=bool(on))


def configure(body: dict) -> dict:
    """Ajustes del acceso remoto: read_only, mode (quick|named), hostname y tunnel_token (vacío = conservar)."""
    changes = {}
    if "read_only" in body:
        changes["read_only"] = bool(body["read_only"])
    if "keep_awake" in body:
        changes["keep_awake"] = bool(body["keep_awake"])
    if "mode" in body:
        if body["mode"] not in ("quick", "named"):
            raise RemoteError("bad_mode")
        changes["mode"] = body["mode"]
    if "hostname" in body:
        host = re.sub(r"^https?://", "", str(body["hostname"] or "").strip().lower()).strip("/")
        if host and not HOSTNAME.match(host):
            raise RemoteError("bad_hostname", host)
        changes["hostname"] = host
    if str(body.get("tunnel_token") or "").strip():
        t = str(body["tunnel_token"]).strip()
        # el comando que muestra Cloudflare es "cloudflared service install <token>": aceptamos el comando entero
        t = t.split()[-1]
        if not re.fullmatch(r"[A-Za-z0-9_\-=+/.]{20,}", t):
            raise RemoteError("bad_tunnel_token")
        changes["tunnel_token"] = t
    cfg = {**load(), **changes}
    if cfg["mode"] == "named" and not (cfg["hostname"] and cfg["tunnel_token"]):
        raise RemoteError("named_incomplete")
    return _update(**changes)


def public(base_url: str | None) -> dict:
    """Lo que ve el dashboard (local): ajustes sin el token del túnel, y la URL completa para copiar."""
    cfg = load()
    tok = cfg["token"] or token()
    return {
        "enabled": cfg["enabled"], "read_only": cfg["read_only"], "mode": cfg["mode"], "hostname": cfg["hostname"],
        "keep_awake": cfg["keep_awake"],
        "has_tunnel_token": bool(cfg["tunnel_token"]), "port": PORT,
        "base_url": base_url, "url": f"{base_url.rstrip('/')}/{tok}{MCP_PATH}" if base_url else None,
        "token": tok, "local_url": f"http://127.0.0.1:{PORT}/{tok}{MCP_PATH}",
        "cloudflared": cloudflared_path(),
    }


# ---------- server MCP por HTTP (lo corre server.py --http) ----------

def is_remote_process() -> bool:
    return os.environ.get("BRAIN_REMOTE") == "1"


def read_only() -> bool:
    return is_remote_process() and load()["read_only"]


def protect(app):
    """Envuelve la app ASGI del SDK: sin el token, 404. /<token>/mcp se reescribe a /mcp."""
    from starlette.responses import PlainTextResponse

    async def wrapped(scope, receive, send):
        if scope["type"] == "lifespan":
            return await app(scope, receive, send)
        if scope["type"] != "http":
            return
        tok = load()["token"].encode()
        path = scope.get("path", "")
        seg, _, rest = path.lstrip("/").partition("/")
        if tok and hmac.compare_digest(seg.encode(), tok) and ("/" + rest).startswith(MCP_PATH):
            new = "/" + rest
            scope = {**scope, "path": new, "raw_path": new.encode()}
            return await app(scope, receive, send)
        auth = dict(scope.get("headers") or []).get(b"authorization", b"")
        if path.startswith(MCP_PATH) and tok and auth[:7].lower() == b"bearer " and hmac.compare_digest(auth[7:].strip(), tok):
            return await app(scope, receive, send)
        await PlainTextResponse("not found", status_code=404)(scope, receive, send)

    return wrapped


def serve(mcp, port: int = PORT) -> None:
    import anyio
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    token()  # que exista antes del primer pedido
    # el Host llega con el dominio público del túnel: la protección contra DNS rebinding del SDK lo
    # rechazaría. Acá la protección es el token (una página web no lo conoce) y escuchar solo en 127.0.0.1.
    app = mcp.streamable_http_app(
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    # access_log apagado: las líneas de acceso llevarían el token de la URL a los logs
    config = uvicorn.Config(protect(app), host="127.0.0.1", port=port, log_level="warning", access_log=False)
    anyio.run(uvicorn.Server(config).serve)


# ---------- cloudflared ----------

def _asset() -> str:
    sysname, mach = platform.system(), platform.machine().lower()
    arm = mach in ("arm64", "aarch64")
    if sysname == "Darwin":
        return f"cloudflared-darwin-{'arm64' if arm else 'amd64'}.tgz"
    if sysname == "Windows":
        return "cloudflared-windows-amd64.exe"  # en Windows ARM corre emulado
    if sysname == "Linux":
        return f"cloudflared-linux-{'arm64' if arm else ('arm' if mach.startswith('arm') else 'amd64')}"
    raise RemoteError("unsupported_os", sysname)


def _local_bin() -> Path:
    return BIN / ("cloudflared.exe" if os.name == "nt" else "cloudflared")


def cloudflared_path() -> str | None:
    """El cloudflared del sistema (brew, winget, apt…) o el que bajó brain a data/bin/."""
    found = shutil.which("cloudflared")
    if found:
        return found
    p = _local_bin()
    return str(p) if p.exists() else None


def _expected_sha256(asset: str) -> str | None:
    """Cloudflare publica el SHA256 de cada archivo en la descripción del release."""
    import requests

    pat = re.compile(re.escape(asset) + r":\s*([0-9a-f]{64})")
    for url, get in ((CF_API, lambda r: r.json().get("body") or ""), (f"{CF_RELEASES}/latest", lambda r: r.text)):
        try:
            r = requests.get(url, timeout=15, headers={"Accept": "application/vnd.github+json"} if "api." in url else {})
            if r.status_code == 200:
                m = pat.search(get(r))
                if m:
                    return m.group(1)
        except (requests.RequestException, ValueError):
            continue
    return None


def install_cloudflared() -> dict:
    """Baja cloudflared del release oficial de GitHub a data/bin/ y verifica el SHA256 publicado."""
    import requests

    asset = _asset()
    expected = _expected_sha256(asset)
    if not expected:
        raise RemoteError("no_checksum", f"{CF_RELEASES}/latest")
    BIN.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    fd, name = tempfile.mkstemp(dir=BIN, prefix=".cloudflared-")
    tmp_path = Path(name)
    try:  # el archivo a medio bajar se borra siempre (falle la descarga, el checksum o lo que sea)
        with os.fdopen(fd, "wb") as tmp:
            try:
                with requests.get(f"{CF_RELEASES}/latest/download/{asset}", stream=True, timeout=60) as r:
                    if r.status_code != 200:
                        raise RemoteError("download", f"HTTP {r.status_code}")
                    for chunk in r.iter_content(1 << 20):
                        h.update(chunk)
                        tmp.write(chunk)
            except requests.RequestException as e:
                raise RemoteError("download", str(e)) from e
        dest = _local_bin()
        if asset.endswith(".tgz"):
            # macOS: Cloudflare publica, con el nombre del .tgz, el SHA256 del binario que trae adentro (no el
            # del .tgz). Se acepta cualquiera de los dos, y el binario se verifica antes de instalarlo.
            try:
                tf = tarfile.open(tmp_path)
            except tarfile.TarError as e:
                raise RemoteError("checksum", asset) from e
            with tf:
                member = next((m for m in tf.getmembers() if m.isfile() and Path(m.name).name == "cloudflared"), None)
                if member is None:
                    raise RemoteError("download", "el .tgz no trae cloudflared")
                inner = hashlib.sha256()
                part = dest.with_name(dest.name + ".part")
                try:
                    with tf.extractfile(member) as src, open(part, "wb") as out:
                        for chunk in iter(lambda: src.read(1 << 20), b""):
                            inner.update(chunk)
                            out.write(chunk)
                    if expected not in (h.hexdigest(), inner.hexdigest()):
                        raise RemoteError("checksum", asset)
                    os.replace(part, dest)
                finally:
                    part.unlink(missing_ok=True)
        else:
            if h.hexdigest() != expected:
                raise RemoteError("checksum", asset)
            os.replace(tmp_path, dest)
        if os.name != "nt":
            dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"path": str(dest), "sha256": expected}


def tunnel_command() -> tuple[list[str], dict]:
    """(comando, env) para el proceso del túnel. El token del túnel va por env, no en la línea de comando."""
    exe = cloudflared_path()
    if not exe:
        raise RemoteError("no_cloudflared")
    cfg = load()
    base = [exe, "tunnel", "--no-autoupdate"]
    if cfg["mode"] == "named":
        if not (cfg["tunnel_token"] and cfg["hostname"]):
            raise RemoteError("named_incomplete")
        return base + ["run"], {"TUNNEL_TOKEN": cfg["tunnel_token"]}
    return base + ["--url", f"http://127.0.0.1:{PORT}"], {}


def tunnel_url(log_lines) -> str | None:
    """URL pública del túnel: la fija (named) o la *.trycloudflare.com que imprime cloudflared."""
    cfg = load()
    if cfg["mode"] == "named":
        ready = any("Registered tunnel connection" in line for line in log_lines)
        return f"https://{cfg['hostname']}" if ready and cfg["hostname"] else None
    for line in reversed(list(log_lines)):
        m = QUICK_URL.search(line)
        if m and "api.trycloudflare.com" not in m.group(0):
            return m.group(0)
    return None
