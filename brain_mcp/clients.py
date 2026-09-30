"""Clientes MCP que usan brain, más allá de los que "Conectar agente" configura solo.

Las apps que agregan brain con un formulario ("Add MCP server → Run a command") guardan la config
donde quieren, así que agents.py no las puede leer. Para que igual aparezcan en "Mis conexiones":

- **Detectados**: cada server.py anota en data/clients.json el nombre y la versión que el cliente
  manda en el handshake MCP (clientInfo), con la fecha del último uso.
- **Manuales**: el usuario puede anotar a mano una app donde agregó brain (por si todavía no la usó).

Varios server.py escriben a la vez (uno por cliente): todo pasa por un lock sobre data/.clients.lock
(brain_mcp/locks.py: flock en macOS/Linux, msvcrt en Windows).
"""
import json
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import locks

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PATH = DATA / "clients.json"

# clientInfo.name (en minúsculas) → clave de agents.CLIENTS. El primero que coincida gana.
KNOWN = [
    ("claude-code", "claude_code"),
    ("claude-ai", "claude_desktop"),
    ("claude desktop", "claude_desktop"),
    ("cursor", "cursor"),
    ("visual studio code", "vscode"),
    ("vscode", "vscode"),
    ("windsurf", "windsurf"),
    ("gemini", "gemini_cli"),
    ("codex", "codex"),
    ("chatgpt", "chatgpt"),
]
# no son agentes: el Inspector que prende el dashboard y los clientes de prueba
IGNORED = ("mcp-inspector", "inspector")
_last_touch: dict[str, float] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "client"


@contextmanager
def _locked():
    with locks.locked(DATA / ".clients.lock"):
        data = {"seen": {}, "manual": {}}
        if PATH.exists():
            try:
                loaded = json.loads(PATH.read_text() or "{}")
                if isinstance(loaded, dict):
                    data["seen"] = loaded.get("seen") or {}
                    data["manual"] = loaded.get("manual") or {}
            except json.JSONDecodeError:
                pass
        before = json.dumps(data, sort_keys=True)
        yield data
        if json.dumps(data, sort_keys=True) != before:
            tmp = PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            tmp.replace(PATH)


def known_key(name: str) -> str | None:
    n = (name or "").lower()
    return next((key for pat, key in KNOWN if pat in n), None)


def record(name: str, version: str = "") -> None:
    """Anota que un cliente MCP usó brain. Como mucho una escritura por minuto por cliente."""
    name = (name or "").strip()[:80]
    if not name or any(i in name.lower() for i in IGNORED):
        return
    cid = _id(name)
    if time.time() - _last_touch.get(cid, 0) < 60:
        return
    _last_touch[cid] = time.time()
    with _locked() as d:
        prev = d["seen"].get(cid) or {}
        d["seen"][cid] = {"name": name, "version": str(version or "")[:40], "first_seen": prev.get("first_seen") or _now(),
                          "last_seen": _now(), "key": known_key(name)}


def add_manual(name: str) -> dict:
    name = name.strip()[:80]
    if not name:
        raise ValueError("missing_name")
    cid = _id(name)
    with _locked() as d:
        d["manual"][cid] = {"name": name, "added_at": _now(), "key": known_key(name)}
    return {"id": cid, "name": name}


def remove(cid: str) -> None:
    """Saca un cliente de la lista (detectado o manual). Si se vuelve a usar, reaparece."""
    with _locked() as d:
        d["seen"].pop(cid, None)
        d["manual"].pop(cid, None)
    _last_touch.pop(cid, None)


def load() -> dict:
    with _locked() as d:
        return {"seen": dict(d["seen"]), "manual": dict(d["manual"])}


def listing(agents_status: list[dict]) -> list[dict]:
    """Filas de "Mis conexiones": agentes configurados + detectados por uso + anotados a mano.

    Un cliente detectado que corresponde a un agente conocido se suma a la fila de ese agente
    (con su último uso) en lugar de repetirse."""
    d = load()
    names = {a["key"]: a["name"] for a in agents_status}
    rows: dict[str, dict] = {}
    for a in agents_status:
        if a["connected"]:
            rows[a["key"]] = {"id": a["key"], "name": a["name"], "source": "config", "agent": a, "last_seen": None}
    for cid, c in d["manual"].items():
        key = c.get("key")
        if key and key in rows:
            continue
        rows[f"m:{cid}"] = {"id": cid, "name": c["name"], "source": "manual", "added_at": c.get("added_at"), "last_seen": None}
    for cid, c in d["seen"].items():
        key = c.get("key")
        target = rows.get(key) if key else None
        target = target or rows.get(f"m:{cid}")
        if target:
            if not target["last_seen"] or c["last_seen"] > target["last_seen"]:
                target["last_seen"], target["client"] = c["last_seen"], c["name"]
            continue
        rows[f"s:{cid}"] = {"id": cid, "name": names.get(key) or c["name"], "source": "seen", "version": c.get("version"),
                            "last_seen": c["last_seen"], "client": c["name"]}
    return list(rows.values())
