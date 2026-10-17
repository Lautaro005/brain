"""Abrir el vault de brain en Obsidian (sidebar del dashboard → «Abrir en Obsidian»).

El vault de brain ya es Markdown con frontmatter y links `[texto](ruta.md)` / `[[nombre]]`: Obsidian lo abre tal
cual, con las carpetas (projects/, skills/, memory/, knowledge/), los archivos y el grafo. Lo que hace brain:

- Detecta Obsidian (la app o su carpeta de config) en macOS, Windows y Linux (también Flatpak y Snap).
- Registra la carpeta vault/ en la lista de vaults de Obsidian (`obsidian.json`), así `obsidian://open?path=…`
  la encuentra. Solo agrega su entrada, con backup previo (`obsidian.json.bak-brain`), y solo con Obsidian cerrado:
  con la app abierta, Obsidian reescribe ese archivo y la entrada se perdería.
- La primera vez deja una config mínima en vault/.obsidian/ (links en Markdown estándar, que es lo que usa brain, y
  colores del grafo por carpeta). Si ya existe .obsidian/, no la toca.
"""
import json
import os
import secrets
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import quote

from . import agents, vault

IS_WIN, IS_MAC = os.name == "nt", sys.platform == "darwin"
DOWNLOAD = "https://obsidian.md/download"


class ObsidianError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code, self.detail = code, detail


def _config_dirs() -> list[Path]:
    """Dónde guarda Obsidian su config (obsidian.json), según el sistema y cómo se instaló."""
    home = agents.HOME
    dirs = [agents._app_data() / "obsidian"]
    if not IS_MAC and not IS_WIN:
        dirs += [home / ".var/app/md.obsidian.Obsidian/config/obsidian", home / "snap/obsidian/current/.config/obsidian"]
    return dirs


def _config() -> Path | None:
    for d in _config_dirs():
        if (d / "obsidian.json").is_file():
            return d / "obsidian.json"
    for d in _config_dirs():
        if d.is_dir():
            return d / "obsidian.json"
    return None


def _app_installed() -> bool:
    if IS_MAC:
        if (agents.APPS / "Obsidian.app").exists() or (agents.HOME / "Applications/Obsidian.app").exists():
            return True
    elif IS_WIN:
        local = Path(os.environ.get("LOCALAPPDATA") or agents.HOME / "AppData/Local")
        if any(p.exists() for p in (local / "Programs/Obsidian/Obsidian.exe", local / "Obsidian/Obsidian.exe")):
            return True
    else:
        if (agents.HOME / ".var/app/md.obsidian.Obsidian").exists() or Path("/var/lib/flatpak/app/md.obsidian.Obsidian").exists():
            return True
    return bool(shutil.which("obsidian"))


def _same(a: str, b: Path) -> bool:
    try:
        x, y = str(Path(a).expanduser().resolve()), str(b.resolve())
    except OSError:
        return False
    return x.lower() == y.lower() if IS_WIN else x == y


def _read(cfg: Path) -> dict:
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as e:
        raise ObsidianError("bad_config", str(cfg)) from e
    if not isinstance(data, dict):
        raise ObsidianError("bad_config", str(cfg))
    return data


def _registered(cfg: Path | None) -> bool:
    if not cfg or not cfg.is_file():
        return False
    try:
        vaults = _read(cfg).get("vaults") or {}
    except ObsidianError:
        return False
    return any(_same(str(v.get("path", "")), vault.VAULT) for v in vaults.values() if isinstance(v, dict))


def _running() -> bool:
    return any(agents._app_running({"process": p}) for p in (("Obsidian",) if IS_MAC or IS_WIN else ("obsidian", "Obsidian")))


def uri() -> str:
    """Abre el vault en BRAIN.md (la raíz de todo)."""
    return "obsidian://open?path=" + quote(str(vault.VAULT / "BRAIN.md"), safe="")


def status() -> dict:
    cfg = _config()
    return {"installed": bool(cfg) or _app_installed(), "registered": _registered(cfg), "running": _running(),
            "vault_path": str(vault.VAULT), "uri": uri(), "download": DOWNLOAD,
            "configured": (vault.VAULT / ".obsidian").is_dir()}


# config mínima del vault para Obsidian: links como los escribe brain y el grafo con colores por carpeta
APP_JSON = {"useMarkdownLinks": True, "newLinkFormat": "relative", "alwaysUpdateLinks": True,
            "newFileLocation": "current", "showUnsupportedFiles": False}
GRAPH_JSON = {"colorGroups": [
    {"query": "file:BRAIN.md OR file:FORMAT.md OR file:FACT-CHECK.md", "color": {"a": 1, "rgb": 14753096}},
    {"query": "path:projects", "color": {"a": 1, "rgb": 2450411}},
    {"query": "path:memory OR file:profile.md", "color": {"a": 1, "rgb": 9133302}},
    {"query": "path:skills", "color": {"a": 1, "rgb": 366185}},
    {"query": "path:knowledge", "color": {"a": 1, "rgb": 563842}},
], "showTags": True, "showAttachments": False, "hideUnresolved": True}


def ensure_vault_config() -> bool:
    """vault/.obsidian/ con la config de brain, solo si todavía no existe (no pisa la del usuario)."""
    d = vault.VAULT / ".obsidian"
    if d.exists():
        return False
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps(APP_JSON, indent=2), encoding="utf-8")
    (d / "graph.json").write_text(json.dumps(GRAPH_JSON, indent=2), encoding="utf-8")
    return True


def register() -> bool:
    """Agrega vault/ a la lista de vaults de Obsidian. Devuelve True si escribió algo."""
    cfg = _config() or (_config_dirs()[0] / "obsidian.json" if _app_installed() else None)
    if not cfg:  # instalada pero nunca abierta: se crea la lista de vaults que Obsidian lee al arrancar
        raise ObsidianError("not_installed")
    if _registered(cfg):
        return False
    if _running():
        raise ObsidianError("running", str(vault.VAULT))
    data = _read(cfg)
    vaults = data.get("vaults") if isinstance(data.get("vaults"), dict) else {}
    vid = secrets.token_hex(8)
    while vid in vaults:
        vid = secrets.token_hex(8)
    vaults[vid] = {"path": str(vault.VAULT), "ts": int(time.time() * 1000)}
    data["vaults"] = vaults
    cfg.parent.mkdir(parents=True, exist_ok=True)
    agents._backup(cfg)
    tmp = cfg.with_name(cfg.name + ".tmp-brain")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, cfg)
    return True


def open_vault() -> dict:
    """Deja todo listo para abrir el vault y devuelve el link obsidian:// (lo abre el navegador)."""
    if not status()["installed"]:
        raise ObsidianError("not_installed")
    vault.ensure_vault()
    configured = ensure_vault_config()
    added = register()
    return {**status(), "added": added, "configured_now": configured}
