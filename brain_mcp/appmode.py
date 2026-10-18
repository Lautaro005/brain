"""Modo app de escritorio (apps/desktop, Tauri): ajustes que lee el shell y el log propio de la app.

El shell (bandeja, notificaciones del sistema, panel del notch) es otro proceso: arranca el dashboard con
BRAIN_APP=1, consulta GET /api/app y GET /api/activity cada 5 s y escribe su propio log en data/logs/app.log,
que el dashboard muestra en Logs. Los ajustes viven en data/app_settings.json (no en localStorage: los
necesita el shell, que no ve el almacenamiento de la página).
"""
import json
import os
from collections import deque

from . import history, locks

DATA = history.DATA
SETTINGS = DATA / "app_settings.json"
LOG = DATA / "logs" / "app.log"
LOG_LINES = 500

DEFAULTS = {
    "notify": {"on": True, "create": True, "update": True, "delete": True},
    "notch": True,
    "close_to_tray": True,
}


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
        "notch": bool(raw.get("notch", DEFAULTS["notch"])),
        "close_to_tray": bool(raw.get("close_to_tray", DEFAULTS["close_to_tray"])),
    }


def save(body: dict) -> dict:
    """Mezcla lo que llega con lo guardado; ignora claves desconocidas y valores que no son booleanos."""
    cur = load()
    if isinstance(body.get("notify"), dict):
        for k in cur["notify"]:
            if isinstance(body["notify"].get(k), bool):
                cur["notify"][k] = body["notify"][k]
    for k in ("notch", "close_to_tray"):
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
