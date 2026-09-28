"""Versión instalada y chequeo de actualizaciones contra los releases de GitHub.

El chequeo solo corre cuando el usuario toca "Buscar actualizaciones" en Ajustes: brain no sale a
internet solo. Compara el archivo VERSION de la instalación con el último release publicado.
"""
import re
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
LATEST_URL = "https://api.github.com/repos/Lautaro005/brain/releases/latest"
RELEASES_URL = "https://github.com/Lautaro005/brain/releases"


class UpdateError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code, self.detail = code, detail


def current() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "v0.00.0"
    except OSError:
        return "v0.00.0"


def parse(v: str) -> tuple[int, ...]:
    """'v0.02.5' → (0, 2, 5). Lo que no sea número se ignora (sufijos tipo -beta)."""
    return tuple(int(x) for x in re.findall(r"\d+", (v or "").split("-")[0])) or (0,)


def check(timeout: float = 8) -> dict:
    """{current, latest, update_available, url, published_at, notes}. Levanta UpdateError."""
    try:
        r = requests.get(LATEST_URL, timeout=timeout, headers={"Accept": "application/vnd.github+json"})
    except requests.RequestException as e:
        raise UpdateError("offline", str(e)) from e
    if r.status_code == 404:  # todavía no hay releases publicados
        return {"current": current(), "latest": None, "update_available": False, "url": RELEASES_URL,
                "published_at": None, "notes": ""}
    if r.status_code != 200:
        raise UpdateError("github", f"HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    latest = str(data.get("tag_name") or "")
    cur = current()
    return {
        "current": cur, "latest": latest, "update_available": bool(latest) and parse(latest) > parse(cur),
        "url": data.get("html_url") or RELEASES_URL, "published_at": data.get("published_at"),
        "notes": (data.get("body") or "")[:4000],
    }
