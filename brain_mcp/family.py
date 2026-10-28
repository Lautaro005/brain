"""La familia de apps de Lautaro005: OpenCue (agentes), Lumina (reuniones y archivos) y AI WorkStudio (el hub).

brain no incluye su código: las usa como programas instalados, igual que WorkStudio, por su comando, su puerto
local y su base de datos:

- **Lumina** (`127.0.0.1:47770`): `GET /api/search` busca en sus archivos, reuniones y notas; `GET /api/status`
  dice si está corriendo. Solo se habla con ella por loopback, y solo cuando el usuario lo pide (botón del chat
  o la tool `lumina_search`).
- **OpenCue**: sus agentes están en `opencue.db` (se lee en solo lectura). Para hablar con uno se arranca
  `opencue mcp-serve --agent <otro agente>` (el mismo camino que usa WorkStudio) y se llama a `send_to_agent`:
  la app de OpenCue hace el trabajo (con sus tools, su memoria y sus permisos) y devuelve la respuesta.
- **WorkStudio**: solo se detecta (y se abre); su servidor MCP único (`workstudio mcp`) ya incluye a brain.

Nada de esto escribe en el vault ni en las bases de las otras apps."""
import asyncio
import json
import logging
import os
import platform
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import history

log = logging.getLogger(__name__)

DATA = history.DATA
CONFIG = DATA / "family.json"
LUMINA_URL = "http://127.0.0.1:47770"
APPS = ("opencue", "lumina", "workstudio")
OPENCUE_PREFIX = "opencue:"
MAX_AGENT_CONTEXT = 24000  # caracteres de contexto de brain que se le mandan a un agente de OpenCue
START_WAIT = 25            # segundos que se espera a que abra la app de OpenCue
REPLY_TIMEOUT = 1800


class FamilyError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code, self.detail = code, detail


# ---------- ajustes ----------

def load() -> dict:
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"lumina_url": _valid_url(data.get("lumina_url")) or LUMINA_URL,
            "opencue_agents": bool(data.get("opencue_agents", True))}


def _valid_url(url) -> str:
    """Lumina solo puede estar en esta compu: loopback (http), con o sin puerto."""
    u = str(url or "").strip().rstrip("/")
    if not u:
        return ""
    p = urlparse(u)
    if p.scheme != "http" or p.hostname not in ("127.0.0.1", "localhost", "::1") or p.path not in ("", "/"):
        raise FamilyError("bad_url", u)
    return f"http://{p.netloc}"


def save(data: dict) -> dict:
    cfg = load()
    if "lumina_url" in data:
        cfg["lumina_url"] = _valid_url(data["lumina_url"]) or LUMINA_URL
    if "opencue_agents" in data:
        cfg["opencue_agents"] = bool(data["opencue_agents"])
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG)
    return cfg


def lumina_url() -> str:
    return os.environ.get("BRAIN_LUMINA_URL", "").rstrip("/") or load()["lumina_url"]


# ---------- Lumina ----------

def lumina_status(timeout: float = 1.5) -> dict:
    out = {"installed": bool(shutil.which("lumina") or (Path.home() / ".lumina").exists()), "running": False,
           "version": "", "url": lumina_url()}
    try:
        r = requests.get(lumina_url() + "/api/status", timeout=timeout)
        if r.ok:
            j = r.json()
            out.update(running=True, installed=True, version=str(j.get("version") or ""),
                       embeddings_ready=bool(j.get("embeddings_ready")))
    except (requests.RequestException, ValueError):
        pass
    return out


def lumina_search(query: str, k: int = 6, scope: str = "all") -> list[dict]:
    """Busca en la base de Lumina (archivos, reuniones, notas). Devuelve [{text, title, score, kind, date, path,
    meeting_id}]. Levanta FamilyError: lumina_down (no corre), lumina_not_ready (todavía no tiene el modelo de
    embeddings) o search_failed."""
    q = (query or "").strip()
    if not q:
        raise FamilyError("empty_query")
    if scope not in ("all", "documents", "meetings", "notes"):
        raise FamilyError("bad_scope", scope)
    try:
        r = requests.get(lumina_url() + "/api/search", params={"q": q[:500], "k": max(1, min(int(k), 20)), "scope": scope},
                         timeout=20)
    except requests.RequestException as e:
        raise FamilyError("lumina_down", str(e)) from e
    if r.status_code == 409:
        raise FamilyError("lumina_not_ready", r.text[:200])
    if not r.ok:
        raise FamilyError("search_failed", f"HTTP {r.status_code}")
    try:
        hits = r.json()
    except ValueError as e:
        raise FamilyError("search_failed", "respuesta inválida") from e
    return [{"text": str(h.get("text") or "")[:1500], "title": h.get("title") or "", "score": h.get("score"),
             "kind": h.get("collection") or "", "date": h.get("date") or "", "path": h.get("path") or "",
             "meeting_id": h.get("meeting_id") or ""} for h in hits if isinstance(h, dict)]


def lumina_block(hits: list[dict], lang: str = "es") -> str:
    """Los resultados de Lumina como bloque para el system prompt o el mensaje de un agente."""
    if not hits:
        return ""
    es = lang != "en"
    head = ("# Lumina: lo que encontró en las reuniones, archivos y notas del usuario\nSon fragmentos de su base "
            "local de Lumina. Usalos solo si responden a lo que pregunta y nombrá de dónde sale cada dato "
            "(título y fecha). Si no alcanzan, decilo.") if es else (
            "# Lumina: what it found in the user's meetings, files and notes\nThese are passages from their local "
            "Lumina knowledge base. Use them only if they answer the question and name where each fact comes from "
            "(title and date). If they are not enough, say so.")
    rows = []
    for i, h in enumerate(hits, 1):
        tag = " · ".join(x for x in (h["kind"], h["title"], h["date"]) if x)
        rows.append(f"[{i}] {tag}\n{h['text']}")
    return head + "\n\n" + "\n\n".join(rows)


# ---------- OpenCue ----------

def _home() -> Path:
    return Path(os.environ.get("BRAIN_FAMILY_HOME") or Path.home())


def opencue_db() -> Path:
    if os.environ.get("OPENCUE_HOME"):
        return Path(os.environ["OPENCUE_HOME"]) / "opencue.db"
    sysname = platform.system()
    h = _home()
    if sysname == "Darwin":
        return h / "Library/Application Support/opencue/opencue.db"
    if sysname == "Windows":
        return Path(os.environ.get("APPDATA") or h / "AppData/Roaming") / "opencue" / "data" / "opencue.db"
    return Path(os.environ.get("XDG_DATA_HOME") or h / ".local/share") / "opencue" / "opencue.db"


def opencue_agents() -> list[dict]:
    """Agentes de OpenCue [{id, name, emoji, model}], de su base en solo lectura. Vacío si no está instalada."""
    db = opencue_db()
    if not db.exists():
        return []
    try:
        with closing(sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=2)) as c:
            rows = c.execute("SELECT id, name, avatar_emoji, model FROM agents ORDER BY name COLLATE NOCASE").fetchall()
    except sqlite3.Error as e:
        log.info("no se pudieron leer los agentes de OpenCue: %s", e)
        return []
    return [{"id": r[0], "name": r[1], "emoji": r[2] or "", "model": r[3] or ""} for r in rows]


def opencue_cli() -> str | None:
    p = shutil.which("opencue")
    if p:
        return p
    for cand in (_home() / ".local/bin/opencue", _home() / ".local/bin/opencue.cmd"):
        if cand.exists():
            return str(cand)
    return None


def _process_running(name: str) -> bool:
    """¿Hay un proceso con ese nombre? (sin dependencias: pgrep o tasklist)."""
    try:
        if platform.system() == "Windows":
            out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}.exe", "/NH"], capture_output=True, text=True, timeout=5).stdout
            return name.lower() in out.lower()
        return subprocess.run(["pgrep", "-ix", name], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def opencue_status() -> dict:
    agents = opencue_agents()
    return {"installed": bool(opencue_cli() or opencue_db().exists()), "running": _process_running("opencue"),
            "agents": len(agents)}


def agent_ref(name: str) -> str:
    return OPENCUE_PREFIX + name


def is_agent_ref(ref: str) -> bool:
    return str(ref or "").startswith(OPENCUE_PREFIX)


def chat_models() -> list[dict]:
    """Los agentes de OpenCue como "modelos" del selector del chat de brain (name = opencue:<nombre>)."""
    if not load()["opencue_agents"]:
        return []
    out = []
    for a in opencue_agents():
        out.append({"provider": "opencue", "name": agent_ref(a["name"]), "label": f"OpenCue · {a['emoji'] + ' ' if a['emoji'] else ''}{a['name']}",
                    "size": 0, "params": a["model"], "ctx": 0, "ctx_max": 0, "ctx_source": "agent", "digest": "",
                    "agent": True})
    return out


def _start_opencue() -> None:
    """Abre la app de OpenCue si no está (los mensajes entre agentes los procesa la app) y espera a que aparezca."""
    if _process_running("opencue"):
        return
    exe = opencue_cli()
    if not exe:
        raise FamilyError("opencue_missing")
    try:
        subprocess.Popen([exe], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError as e:
        raise FamilyError("opencue_start", str(e)) from e
    end = time.time() + START_WAIT
    while time.time() < end:
        if _process_running("opencue"):
            time.sleep(3)  # que termine de arrancar su bucle de mensajes
            return
        time.sleep(0.5)
    raise FamilyError("opencue_start", "la app no abrió")


async def _send_to_agent(exe: str, sender: str, target: str, message: str, timeout: float, prefix: tuple = ()) -> str:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=exe, args=[*prefix, "mcp-serve", "--agent", sender], env={**os.environ})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), 30)
            res = await asyncio.wait_for(session.call_tool("send_to_agent", {"agent": target, "message": message}), timeout)
    text = "\n".join(getattr(c, "text", "") for c in res.content if getattr(c, "text", None)).strip()
    if res.is_error:
        raise FamilyError("agent_error", text[:500])
    prefix = f"[{target}]:"
    return text[len(prefix):].strip() if text.startswith(prefix) else text


def opencue_chat(agent: str, message: str, timeout: float = REPLY_TIMEOUT, start_app: bool = True) -> str:
    """Le manda `message` a un agente de OpenCue y devuelve su respuesta (bloquea hasta que contesta).

    Como WorkStudio: `opencue mcp-serve --agent <otro agente>` + `send_to_agent`. OpenCue entrega el mensaje
    como si viniera de ese otro agente; el agente destino conserva su propia conversación con sus tools."""
    agents = opencue_agents()
    if not agents:
        raise FamilyError("opencue_missing")
    target = next((a for a in agents if a["name"] == agent or a["id"] == agent), None)
    if target is None:
        raise FamilyError("agent_not_found", agent)
    sender = next((a for a in agents if a["id"] != target["id"]), target)
    exe = opencue_cli()
    if not exe:
        raise FamilyError("opencue_missing")
    if start_app:
        _start_opencue()
    try:
        return asyncio.run(_send_to_agent(exe, sender["id"], target["name"], message, timeout))
    except asyncio.TimeoutError as e:
        raise FamilyError("agent_timeout", target["name"]) from e
    except FamilyError:
        raise
    except Exception as e:
        raise FamilyError("agent_error", str(e)) from e


# ---------- AI WorkStudio ----------

def workstudio_status() -> dict:
    sysname = platform.system()
    app = {"Darwin": Path("/Applications/AI WorkStudio.app"), "Windows": Path(os.environ.get("LOCALAPPDATA", "")) / "AI WorkStudio",
           }.get(sysname)
    installed = bool(shutil.which("workstudio") or (app and app.exists()) or (_home() / ".workstudio").exists())
    return {"installed": installed, "running": _process_running("workstudio") or _process_running("AI WorkStudio")}


def open_app(name: str) -> dict:
    """Abre una app de la familia: Lumina en el navegador, las otras con su comando."""
    if name == "lumina":
        if not lumina_status()["running"]:
            exe = shutil.which("lumina")
            if not exe:
                raise FamilyError("not_installed", name)
            subprocess.Popen([exe], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return {"url": lumina_url()}
    cmd = opencue_cli() if name == "opencue" else shutil.which("workstudio")
    if not cmd and name == "workstudio" and platform.system() == "Darwin" and Path("/Applications/AI WorkStudio.app").exists():
        subprocess.Popen(["open", "-a", "AI WorkStudio"])
        return {}
    if not cmd:
        raise FamilyError("not_installed", name)
    subprocess.Popen([cmd], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {}


def status() -> dict:
    return {"settings": load(), "lumina": lumina_status(), "opencue": opencue_status(), "workstudio": workstudio_status()}
