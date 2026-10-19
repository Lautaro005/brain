"""Modo app de escritorio (apps/desktop, Tauri): ajustes que lee el shell y el log propio de la app.

El shell (bandeja, notificaciones del sistema, panel del notch) es otro proceso: arranca el dashboard con
BRAIN_APP=1, consulta GET /api/app y GET /api/activity cada 5 s y escribe su propio log en data/logs/app.log,
que el dashboard muestra en Logs. Los ajustes viven en data/app_settings.json (no en localStorage: los
necesita el shell, que no ve el almacenamiento de la página).
"""
import json
import os
import subprocess
import sys
from collections import deque
from pathlib import Path

from . import history, locks

DATA = history.DATA
SETTINGS = DATA / "app_settings.json"
LOG = DATA / "logs" / "app.log"
LOG_LINES = 500

DEFAULTS = {
    "notify": {"on": True, "create": True, "update": True, "delete": True},
    "channel": "notch",  # "notch" | "system" — un solo lugar para el aviso
    "close_to_tray": True,
}


def _channel(raw: dict) -> str:
    """`channel` manda. Un `notch` booleano viejo (v0.03.3.1) se lee una vez. Sin nada: notch en macOS."""
    if raw.get("channel") in ("notch", "system"):
        return raw["channel"]
    if "notch" in raw:
        return "notch" if raw.get("notch") else "system"
    return "notch" if sys.platform == "darwin" else "system"


def active() -> bool:
    """El dashboard lo arrancó la app de escritorio (la pone en el entorno al lanzar brain.sh/brain.ps1)."""
    return os.environ.get("BRAIN_APP") == "1"


def load() -> dict:
    try:
        raw = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    notify = raw.get("notify") if isinstance(raw.get("notify"), dict) else {}
    return {
        "notify": {k: bool(notify.get(k, v)) for k, v in DEFAULTS["notify"].items()},
        "channel": _channel(raw),
        "close_to_tray": bool(raw.get("close_to_tray", DEFAULTS["close_to_tray"])),
    }


def save(body: dict) -> dict:
    """Mezcla lo que llega con lo guardado; ignora claves desconocidas y valores que no son booleanos."""
    cur = load()
    if isinstance(body.get("notify"), dict):
        for k in cur["notify"]:
            if isinstance(body["notify"].get(k), bool):
                cur["notify"][k] = body["notify"][k]
    if body.get("channel") in ("notch", "system"):
        cur["channel"] = body["channel"]
    elif isinstance(body.get("notch"), bool):  # clientes de la v0.03.3.1
        cur["channel"] = "notch" if body["notch"] else "system"
    for k in ("close_to_tray",):
        if isinstance(body.get(k), bool):
            cur[k] = body[k]
    DATA.mkdir(parents=True, exist_ok=True)
    with locks.locked(DATA / ".app.lock"):
        tmp = SETTINGS.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS)
    return cur


def log_lines(limit: int = LOG_LINES) -> list[str]:
    """Las últimas líneas de data/logs/app.log (lo que escribe el shell: arranque, tray, avisos, errores)."""
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            return list(deque((ln.rstrip("\n") for ln in f), maxlen=limit))
    except OSError:
        return []


def service_info() -> dict | None:
    """Entrada de «App» para la pestaña Logs; None si brain no corre dentro de la app y nunca hubo log."""
    if not active() and not LOG.exists():
        return None
    return {"key": "app", "label": "App", "description": "App de escritorio (bandeja, avisos y notch)",
            "group": "app", "port": 0, "status": "running" if active() else "stopped", "managed": active(),
            "uptime": None, "exit_code": None, "link": None}


# ---------- pasar de la versión de terminal a la app ----------
def _home() -> Path:
    return Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or "~").expanduser()


def installed_path() -> Path | None:
    """Dónde está la app de escritorio instalada (la deja install.sh / install.ps1), o None."""
    h = _home()
    if sys.platform == "darwin":
        cands = [h / "Applications" / "brain.app", Path("/Applications/brain.app")]
    elif sys.platform.startswith("win"):
        local = Path(os.environ.get("LOCALAPPDATA") or h / "AppData" / "Local")
        cands = [local / "brain" / "brain-app.exe", local / "Programs" / "brain" / "brain-app.exe"]
    else:
        cands = [h / ".local" / "opt" / "brain" / "brain.AppImage"]
    return next((c for c in cands if c.exists()), None)


def handoff(port: int, root: Path) -> None:
    """Deja que el dashboard de terminal se cierre y abre la app cuando el puerto quede libre (la app
    arranca su propio dashboard: si abriera con el de la terminal todavía vivo, lo usaría y se apagaría
    con él). Proceso aparte, desligado de este."""
    code = (
        "import socket,subprocess,sys,time\n"
        "port,root=int(sys.argv[1]),sys.argv[2]\n"
        "def up():\n"
        "    s=socket.socket(); s.settimeout(0.5)\n"
        "    try: return s.connect_ex(('127.0.0.1',port))==0\n"
        "    finally: s.close()\n"
        "end=time.time()+40\n"
        "while up() and time.time()<end: time.sleep(0.5)\n"
        "time.sleep(1)\n"
        "if sys.platform.startswith('win'):\n"
        "    cmd=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',root+'\\\\brain.ps1','app']\n"
        "else:\n"
        "    cmd=['/bin/bash',root+'/brain.sh','app']\n"
        "subprocess.Popen(cmd,cwd=root,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
    )
    kw: dict = {}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    subprocess.Popen([sys.executable, "-c", code, str(port), str(root)], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
