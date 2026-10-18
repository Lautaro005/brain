"""Ajustes de LM Studio (URL del server OpenAI-compatible local). Config en data/lmstudio.json (600)."""
import json
import os
import re
from urllib.parse import urlparse

import requests

from . import history, locks

DATA = history.DATA
CONFIG = DATA / "lmstudio.json"
DEFAULT_URL = "http://127.0.0.1:1234"
DEFAULT_CTX = 32768

_CACHE: dict = {"key": None, "value": None}


class LmStudioError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code, self.detail = code, detail


def load() -> dict:
    try:
        st = CONFIG.stat()
        key = (str(CONFIG), st.st_ino, st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if key is not None and _CACHE["key"] == key:
        return dict(_CACHE["value"])
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    url = str(data.get("url") or DEFAULT_URL).strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    cfg = {"url": url, "enabled": bool(data.get("enabled", True))}
    _CACHE.update(key=key, value=cfg)
    return dict(cfg)


def _save(cfg: dict) -> dict:
    DATA.mkdir(parents=True, exist_ok=True)
    with locks.locked(DATA / ".lmstudio.lock"):
        tmp = CONFIG.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, CONFIG)
    _CACHE["key"] = None
    return cfg


def _valid_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if u.endswith("/v1"):
        u = u[:-3]
    if not u:
        raise LmStudioError("bad_url")
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise LmStudioError("bad_url")
    if p.username or p.password:
        raise LmStudioError("bad_url")
    host = (p.hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1") and not host.endswith(".local"):
        raise LmStudioError("bad_url_host")
    return u


def save(body: dict) -> dict:
    url = _valid_url(str(body.get("url") or DEFAULT_URL))
    enabled = bool(body.get("enabled", True))
    return _save({"url": url, "enabled": enabled})


def public() -> dict:
    cfg = load()
    return {"url": cfg["url"], "enabled": cfg["enabled"]}


def base_url() -> str | None:
    cfg = load()
    if not cfg["enabled"]:
        return None
    return cfg["url"]


def healthy() -> bool:
    url = base_url()
    if not url:
        return False
    try:
        r = requests.get(f"{url}/v1/models", timeout=1.5)
        return r.status_code == 200
    except requests.RequestException:
        return False


def list_models() -> list[dict]:
    url = base_url()
    if not url:
        return []
    try:
        r = requests.get(f"{url}/v1/models", timeout=5)
        r.raise_for_status()
    except requests.RequestException as e:
        raise LmStudioError("lmstudio_down", str(e)) from e
    out = []
    for m in r.json().get("data") or []:
        mid = str(m.get("id") or "").strip()
        if not mid or re.search(r"embed", mid, re.I):
            continue
        out.append({"id": mid, "ctx_max": DEFAULT_CTX})
    return out
