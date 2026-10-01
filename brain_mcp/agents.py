"""Conectar brain a agentes e IAs (clientes MCP): detectar, conectar y desconectar.

Cada cliente guarda sus servers MCP en su propio archivo de config. Conectar = agregar la entrada
"brain" (command + args) sin tocar el resto del archivo, con backup previo (<archivo>.bak-brain).

Casos especiales:
- Claude Desktop reescribe su config al cerrarse: solo se escribe con la app cerrada (si está
  abierta, se puede pedir que el dashboard la cierre, escriba y la vuelva a abrir).
- ChatGPT desktop solo usa servers MCP locales en los modos Codex / ChatGPT Work, y los lee de
  ~/.codex/config.toml, que comparte con Codex CLI (conectar uno conecta el otro).
- Claude Code se maneja con su CLI (`claude mcp add/remove -s user`).
- OpenMausBot guarda sus servers en ~/.openmausbot/config.json (mcpServers, como Claude) y solo los lee
  al abrirse: se escribe con la app cerrada y se vuelve a abrir.
- Manus Studio (la app de escritorio) agrega servers locales con su formulario ("Run a command") y no tiene
  un archivo de config documentado: kind "form", la tarjeta muestra el comando y los argumentos para copiar
  y se marca "Detectado" (status.detected) cuando Manus usa brain (clientInfo del handshake, clients.py).
- DeepSeek Harness (dsh, app de escritorio y CLI) compone su config con parches YAML de Cordis; la capa del
  usuario es ~/.dsh/cordis.patch.yml ($DSH_HOME), que comparten el escritorio, el CLI y la web, y que dsh
  recarga solo al cambiar. brain agrega ahí un bloque propio entre marcas (`- insert:` de
  @deepseek-ai/dsh-mcp-client con transport stdio) y para desconectar saca solo ese bloque: el archivo
  puede tener expresiones `!!js` y comentarios que no se pueden reescribir con un parser.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SERVER = "brain"
# hook para tests: permite apuntar a un HOME falso sin tocar las configs reales
HOME = Path(os.environ.get("BRAIN_AGENTS_HOME") or Path.home())
APPS = Path(os.environ.get("BRAIN_AGENTS_APPS") or "/Applications")
IS_WIN, IS_MAC = os.name == "nt", sys.platform == "darwin"


def _app_data() -> Path:
    """Carpeta de config de las apps de escritorio: macOS ~/Library/Application Support,
    Windows %APPDATA% y Linux ~/.config (o $XDG_CONFIG_HOME)."""
    if IS_MAC:
        return HOME / "Library/Application Support"
    if IS_WIN:
        return Path(os.environ["APPDATA"]) if os.environ.get("APPDATA") and not os.environ.get("BRAIN_AGENTS_HOME") \
            else HOME / "AppData/Roaming"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg and not os.environ.get("BRAIN_AGENTS_HOME") else HOME / ".config"



class AgentError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code, self.detail = code, detail


def _uv() -> str:
    # path absoluto: las apps de escritorio no heredan el PATH del shell
    for c in (shutil.which("uv"), str(Path.home() / ".local/bin/uv"), str(Path.home() / ".local/bin/uv.exe"),
              str(Path.home() / ".cargo/bin/uv.exe"), "/opt/homebrew/bin/uv", "/usr/local/bin/uv"):
        if c and os.path.exists(c):
            return c
    return "uv"


def server_spec() -> dict:
    return {"command": _uv(), "args": ["run", "--directory", str(ROOT), "python", "server.py"]}


# ---------- clientes ----------
# kind: "json" (mcpServers en un .json), "toml" (~/.codex/config.toml), "cli" (claude code)
CLIENTS = {
    "claude_desktop": {
        "group": "desktop", "name": "Claude Desktop", "kind": "json",
        "path": _app_data() / "Claude/claude_desktop_config.json", "key": "mcpServers",
        "app": "Claude.app", "process": "Claude", "win_exe": "AnthropicClaude/claude.exe",
    },
    "chatgpt": {
        "group": "desktop", "name": "ChatGPT", "kind": "toml", "path": HOME / ".codex/config.toml",
        "app": "ChatGPT.app", "shares": "codex",
    },
    "openmausbot": {
        "group": "desktop", "name": "OpenMausBot", "kind": "json", "path": HOME / ".openmausbot/config.json",
        "key": "mcpServers", "app": "OpenMausBot.app", "process": "OpenMausBot",
        "win_exe": "Programs/OpenMausBot/OpenMausBot.exe",
    },
    "manus": {
        # sin archivo de config documentado: se agrega con su formulario "Run a command" (valores para copiar)
        "group": "desktop", "name": "Manus Studio", "kind": "form", "path": None, "app": "Manus.app",
    },
    "deepseek_harness": {
        # capa de parches del usuario, compartida por el escritorio, el CLI y la web; dsh la recarga sola
        "group": "desktop", "name": "DeepSeek Harness", "kind": "dsh",
        "path": (Path(os.environ["DSH_HOME"]) if os.environ.get("DSH_HOME", "").strip() and not os.environ.get("BRAIN_AGENTS_HOME")
                 else HOME / ".dsh") / "cordis.patch.yml",
        "app": "DeepSeek Harness.app", "cli": "dsh",
    },
    "claude_code": {
        "group": "other", "name": "Claude Code", "kind": "cli", "path": HOME / ".claude.json", "key": "mcpServers",
        "cli": "claude",
    },
    "codex": {
        "group": "other", "name": "Codex CLI", "kind": "toml", "path": HOME / ".codex/config.toml",
        "cli": "codex", "shares": "chatgpt",
    },
    "cursor": {
        "group": "other", "name": "Cursor", "kind": "json", "path": HOME / ".cursor/mcp.json", "key": "mcpServers",
        "app": "Cursor.app",
    },
    "vscode": {
        "group": "other", "name": "VS Code (Copilot)", "kind": "json",
        "path": _app_data() / "Code/User/mcp.json", "key": "servers", "typed": True,
        "app": "Visual Studio Code.app",
    },
    "windsurf": {
        "group": "other", "name": "Windsurf", "kind": "json", "path": HOME / ".codeium/windsurf/mcp_config.json",
        "key": "mcpServers", "app": "Windsurf.app",
    },
    "gemini_cli": {
        "group": "other", "name": "Gemini CLI", "kind": "json", "path": HOME / ".gemini/settings.json",
        "key": "mcpServers", "cli": "gemini",
    },
}


def _installed(c: dict) -> bool:
    if c["path"] is not None and c["kind"] == "dsh" and c["path"].parent.is_dir():
        return True  # ~/.dsh existe: dsh corrió alguna vez (escritorio o CLI)
    if c.get("app") and ((APPS / c["app"]).exists() or (HOME / "Applications" / c["app"]).exists()):
        return True
    if c.get("cli") and shutil.which(c["cli"]):
        return True
    return c["path"] is not None and c["path"].exists()  # si ya tiene config, está instalado aunque no lo encontremos


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text() or "{}")
    except json.JSONDecodeError as e:
        # ej. VS Code permite comentarios (JSONC): no lo reescribimos para no romperlo
        raise AgentError("bad_config", f"{path}: {e}") from e
    if not isinstance(data, dict):
        raise AgentError("bad_config", f"{path}: no es un objeto JSON")
    return data


# ---------- DeepSeek Harness (parches YAML de Cordis) ----------
DSH_ROW_ID = "brain-mcp"
_DSH_BEGIN = "# >>> brain: server MCP (lo agrega y lo saca el dashboard de brain; no editar a mano)"
_DSH_END = "# <<< brain"


class _DshLoader(yaml.SafeLoader):
    """Lee el YAML de dsh sin evaluar nada: `!!js` y otros tags propios quedan como texto."""


_DshLoader.add_multi_constructor("", lambda loader, suffix, node: loader.construct_scalar(node)
                                 if isinstance(node, yaml.ScalarNode) else None)


def _dsh_parse(text: str, path: Path) -> list:
    try:
        data = yaml.load(text, Loader=_DshLoader) if text.strip() else []
    except yaml.YAMLError as e:
        raise AgentError("bad_config", f"{path}: {e}") from e
    if data is None:
        return []
    if not isinstance(data, list):
        raise AgentError("bad_config", f"{path}: dsh espera una lista YAML de parches")
    return data


def _dsh_row(patches: list) -> dict | None:
    for p in patches:
        if isinstance(p, dict):
            for row in p.get("insert") or []:
                if isinstance(row, dict) and row.get("id") == DSH_ROW_ID:
                    return row.get("config") or {}
    return None


def _dsh_strip(text: str) -> str:
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        if line.rstrip() == _DSH_BEGIN:
            skipping = True
            continue
        if skipping:
            if line.rstrip() == _DSH_END:
                skipping = False
            continue
        out.append(line)
    return "".join(out)


def _dsh_block() -> str:
    spec = server_spec()
    q = json.dumps  # un string JSON es un escalar YAML válido (comillas dobles), también con barras de Windows
    return "\n".join([
        _DSH_BEGIN,
        "- insert:",
        f"    - id: {DSH_ROW_ID}",
        "      name: '@deepseek-ai/dsh-mcp-client'",
        "      config:",
        f"        serverName: {SERVER}",
        "        transport: stdio",
        f"        command: {q(spec['command'])}",
        f"        args: [{', '.join(q(a) for a in spec['args'])}]",
        _DSH_END,
    ]) + "\n"


def _write_dsh(c: dict, add: bool) -> None:
    path = c["path"]
    text = path.read_text() if path.exists() else ""
    _dsh_parse(text, path)  # nunca tocar un archivo que dsh tampoco podría leer
    new = _dsh_strip(text)
    if add:
        base = "" if new.strip() in ("", "[]") else new.rstrip("\n") + "\n\n"
        new = base + _dsh_block()
    elif new == text:
        return
    else:
        new = new.rstrip("\n") + "\n" if new.strip() else ""
    patches = _dsh_parse(new, path)
    if add and _dsh_row(patches) is None:  # p. ej. el archivo era una lista en estilo flujo
        raise AgentError("bad_config", f"{path}: no se pudo agregar brain sin riesgo")
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)
    path.write_text(new)


def _entry(c: dict) -> dict | None:
    """La entrada 'brain' actual en la config del cliente, o None."""
    try:
        if c["kind"] == "dsh":
            return _dsh_row(_dsh_parse(c["path"].read_text(), c["path"])) if c["path"].exists() else None
        if c["kind"] == "form":
            return None
        if c["kind"] == "toml":
            if not c["path"].exists():
                return None
            return (tomllib.loads(c["path"].read_text()).get("mcp_servers") or {}).get(SERVER)
        return (_read_json(c["path"]).get(c["key"]) or {}).get(SERVER)
    except (AgentError, tomllib.TOMLDecodeError):
        return None


def _app_running(c: dict) -> bool:
    if not c.get("process"):
        return False
    try:
        if IS_WIN:
            r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {c['process']}.exe", "/NH"], capture_output=True, text=True)
            return f"{c['process'].lower()}.exe" in r.stdout.lower()
        return subprocess.run(["pgrep", "-x", c["process"]], capture_output=True).returncode == 0
    except FileNotFoundError:  # sin pgrep/tasklist: se asume cerrada
        return False


def _seen(key: str) -> bool:
    from . import clients  # el handshake MCP anota qué apps usaron brain (data/clients.json)
    try:
        return any(x.get("key") == key for x in clients.load()["seen"].values())
    except Exception:
        return False


def status(key: str) -> dict:
    c = CLIENTS[key]
    entry = _entry(c)
    args = (entry or {}).get("args") or []
    return {
        "key": key, "name": c["name"], "group": c["group"], "installed": _installed(c),
        "connected": entry is not None,
        # conectado pero apuntando a otra carpeta (ej. una instalación vieja)
        "elsewhere": entry is not None and str(ROOT) not in [str(a) for a in args],
        "kind": c["kind"],
        # apps que se conectan con su formulario: no hay config que leer, pero sí se ve si ya usaron brain
        "detected": c["kind"] == "form" and _seen(key),
        "config_path": str(c["path"]).replace(str(HOME), "~", 1) if c["path"] else "",
        "shares": c.get("shares"),
        "app_running": _app_running(c),
        "auto": c["kind"] in ("json", "toml", "dsh") or (c["kind"] == "cli" and bool(shutil.which(c.get("cli", "")))),
    }


def all_status() -> list[dict]:
    return [status(k) for k in CLIENTS]


def _backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak-brain"))


# ---------- escritura ----------

def _write_json(c: dict, add: bool) -> None:
    data = _read_json(c["path"])
    servers = data.setdefault(c["key"], {})
    if not isinstance(servers, dict):
        raise AgentError("bad_config", f"{c['path']}: '{c['key']}' no es un objeto")
    if add:
        spec = server_spec()
        servers[SERVER] = {"type": "stdio", **spec} if c.get("typed") else spec
    elif SERVER in servers:
        del servers[SERVER]
    else:
        return
    c["path"].parent.mkdir(parents=True, exist_ok=True)
    _backup(c["path"])
    c["path"].write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


_TOML_SECTION = re.compile(r'^\s*\[mcp_servers\.(?:"brain"|brain)(?:\.[^\]]+)?\]\s*$')
_TOML_ANY_SECTION = re.compile(r"^\s*\[")


def _strip_toml_section(text: str) -> str:
    """Saca [mcp_servers.brain] (y subtablas tipo [mcp_servers.brain.env]) sin tocar el resto."""
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        if _TOML_SECTION.match(line):
            skipping = True
            continue
        if skipping and _TOML_ANY_SECTION.match(line):
            skipping = False
        if not skipping:
            out.append(line)
    return "".join(out).rstrip() + ("\n" if out else "")


def _write_toml(c: dict, add: bool) -> None:
    path = c["path"]
    text = path.read_text() if path.exists() else ""
    if text:
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise AgentError("bad_config", f"{path}: {e}") from e
    new = _strip_toml_section(text)
    if add:
        spec = server_spec()
        # json.dumps produce strings válidos como "basic strings" de TOML
        new += (("\n" if new.strip() else "") + f"[mcp_servers.{SERVER}]\n"
                f"command = {json.dumps(spec['command'])}\n"
                f"args = [{', '.join(json.dumps(a) for a in spec['args'])}]\n")
    elif new == text:
        return
    try:
        tomllib.loads(new)  # nunca dejar un TOML roto (ej. si brain estaba definido como tabla inline)
    except tomllib.TOMLDecodeError as e:
        raise AgentError("bad_config", f"{path}: {e}") from e
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)
    path.write_text(new)


def _cli(c: dict, add: bool) -> None:
    exe = shutil.which(c["cli"])
    if not exe:
        raise AgentError("no_cli", c["cli"])
    subprocess.run([exe, "mcp", "remove", "-s", "user", SERVER], capture_output=True, text=True)
    if add:
        spec = server_spec()
        r = subprocess.run([exe, "mcp", "add", "-s", "user", SERVER, "--", spec["command"], *spec["args"]],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise AgentError("cli_failed", (r.stderr or r.stdout).strip())


def _quit_app(c: dict) -> None:
    if IS_MAC:
        subprocess.run(["osascript", "-e", f'tell application "{c["process"]}" to quit'], capture_output=True)
    elif IS_WIN:  # sin /F: le pide que cierre, así guarda su config
        subprocess.run(["taskkill", "/IM", f"{c['process']}.exe"], capture_output=True)
    else:
        subprocess.run(["pkill", "-x", c["process"]], capture_output=True)
    for _ in range(40):
        if not _app_running(c):
            time.sleep(0.5)  # margen para que termine de escribir su config al salir
            return
        time.sleep(0.5)
    raise AgentError("app_still_running", c["name"])


def _open_app(c: dict) -> None:
    """Vuelve a abrir la app después de escribir su config (best effort fuera de macOS)."""
    try:
        if IS_MAC:
            subprocess.run(["open", "-a", c["process"]], capture_output=True)
        elif IS_WIN:
            exe = Path(os.environ.get("LOCALAPPDATA", "")) / c.get("win_exe", "")
            if c.get("win_exe") and exe.exists():
                subprocess.Popen([str(exe)], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        elif shutil.which(c["process"].lower()):
            subprocess.Popen([c["process"].lower()], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def set_connected(key: str, connected: bool, restart_app: bool = False) -> dict:
    """Conecta/desconecta brain en un cliente. restart_app: si la app está abierta y hay que
    escribir con la app cerrada (Claude Desktop), cerrarla y volver a abrirla."""
    if key not in CLIENTS:
        raise AgentError("unknown", key)
    c = CLIENTS[key]
    if c["kind"] == "form":
        raise AgentError("form_only", c["name"])  # se agrega en la app con su formulario (valores en la tarjeta)
    reopen = False
    if c.get("process") and _app_running(c):
        if not restart_app:
            raise AgentError("app_running", c["name"])
        _quit_app(c)
        reopen = True
    if c["kind"] == "json":
        _write_json(c, connected)
    elif c["kind"] == "toml":
        _write_toml(c, connected)
    elif c["kind"] == "dsh":
        _write_dsh(c, connected)
    else:
        _cli(c, connected)
    if reopen:
        _open_app(c)
    return status(key)


def manual_snippets() -> dict:
    """Para cualquier otro cliente MCP: config JSON genérica + la línea de comando."""
    spec = server_spec()
    return {
        # para apps con formulario "Add MCP server → Run a command" (nombre, comando, un argumento por línea)
        "form": {"name": SERVER, "command": spec["command"], "args": "\n".join(spec["args"])},
        # la carpeta de ESTA instalación (se calcula en cada Mac, nunca queda fija)
        "install_path": str(ROOT).replace(str(Path.home()), "~", 1),
        "json": json.dumps({"mcpServers": {SERVER: spec}}, indent=2, ensure_ascii=False),
        "command": " ".join(json.dumps(x) if " " in x else x for x in [spec["command"], *spec["args"]]),
        "toml": f"[mcp_servers.{SERVER}]\ncommand = {json.dumps(spec['command'])}\n"
                f"args = [{', '.join(json.dumps(a) for a in spec['args'])}]",
    }
