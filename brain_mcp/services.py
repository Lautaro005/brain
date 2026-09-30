"""Procesos que el dashboard puede prender/apagar: Chroma, Ollama y el Inspector MCP.

Cada servicio corre como subproceso propio (grupo de procesos aparte), con su salida guardada en
memoria para la vista de logs. Si un servicio ya está corriendo por fuera (ej. la app Ollama o un
./chroma_server.sh en otra terminal) se marca como "external" y el dashboard no lo toca.
"""
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path

from .chroma_store import CHROMA_HOST, CHROMA_PORT

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# el formato cambió entre versiones (MCP_PROXY_AUTH_TOKEN / MCP_INSPECTOR_API_TOKEN, localhost / 127.0.0.1)
INSPECTOR_URL = re.compile(r"http://(?:localhost|127\.0\.0\.1):6274/?\?\w*TOKEN=[\w-]+")


def _http_ok(url: str, timeout: float = 0.8) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _find_ollama() -> str | None:
    for c in (shutil.which("ollama"), "/usr/local/bin/ollama", "/opt/homebrew/bin/ollama", "/usr/bin/ollama",
              "/Applications/Ollama.app/Contents/Resources/ollama",
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")):
        if c and os.path.exists(c):
            return c
    return None


class Service:
    def __init__(self, key: str, label: str, description: str, port: int, cmd, env=None):
        self.key, self.label, self.description, self.port = key, label, description, port
        self._cmd, self._env = cmd, env or {}
        self.proc: subprocess.Popen | None = None
        self.logs: deque[str] = deque(maxlen=500)
        self.started_at: float | None = None
        self.last_exit: int | None = None
        self._lock = threading.Lock()

    # --- a redefinir ---
    def healthy(self) -> bool:
        return _port_open(self.port)

    def link(self) -> str | None:
        return None

    # --- estado ---
    def ours(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def status(self) -> str:
        if self.ours():
            return "running" if self.healthy() else "starting"
        if self.proc is not None and self.last_exit is None:
            self.last_exit = self.proc.returncode
        if self.healthy():
            return "external"
        return "error" if self.last_exit not in (None, 0, *_STOP_CODES) else "stopped"

    def info(self) -> dict:
        st = self.status()
        return {
            "key": self.key, "label": self.label, "description": self.description,
            "port": self.port, "status": st, "managed": self.ours(),
            "uptime": int(time.time() - self.started_at) if self.ours() and self.started_at else None,
            "exit_code": self.last_exit if st == "error" else None,
            "link": self.link() if st in ("running", "external") else None,
        }

    # --- acciones ---
    def start(self) -> None:
        with self._lock:
            if self.ours():
                return
            if self.healthy():
                raise RuntimeError(f"{self.label} ya está corriendo por fuera del dashboard.")
            cmd = list(self._cmd() if callable(self._cmd) else self._cmd)
            # path completo del ejecutable: en Windows "npx" es npx.cmd y "uv" puede no estar en el PATH del proceso
            cmd[0] = shutil.which(cmd[0]) or cmd[0]
            self.logs.append(f"$ {' '.join(cmd)}")
            self.last_exit = None
            self.proc = subprocess.Popen(
                cmd, cwd=ROOT, env={**os.environ, **self._env},
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, bufsize=1, **_GROUP,
            )
            self.started_at = time.time()
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            line = ANSI.sub("", line.rstrip())
            if line.strip():
                self.logs.append(line)
        code = proc.wait()
        self.logs.append(f"[proceso terminado, código {code}]")

    def stop(self) -> None:
        with self._lock:
            proc = self.proc
            if proc is None or proc.poll() is not None:
                return
            try:
                _terminate(proc, force=False)
                proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                _terminate(proc, force=True)
                proc.wait(timeout=3)
            except ProcessLookupError:
                pass
            # lo cerró el dashboard: no es un error aunque el código de salida no sea 0 (en Windows,
            # terminate() deja 1; en macOS/Linux, -SIGTERM)
            self.last_exit = 0


# Cada servicio corre en su propio grupo de procesos para poder cerrarlo con sus hijos.
# Windows no tiene grupos POSIX: se usa un process group nuevo y terminate()/kill().
if os.name == "nt":  # pragma: no cover - se prueba en el CI de Windows
    _GROUP = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    _STOP_CODES: tuple[int, ...] = ()  # Windows no tiene SIGKILL: solo cuenta el cierre hecho por stop()

    def _terminate(proc: subprocess.Popen, force: bool) -> None:
        proc.kill() if force else proc.terminate()
else:
    _GROUP = {"start_new_session": True}
    _STOP_CODES = (-signal.SIGTERM, -signal.SIGKILL)  # cerrado por una señal: no es un error

    def _terminate(proc: subprocess.Popen, force: bool) -> None:
        os.killpg(proc.pid, signal.SIGKILL if force else signal.SIGTERM)


class Chroma(Service):
    def healthy(self) -> bool:
        return _http_ok(f"http://{CHROMA_HOST}:{CHROMA_PORT}/api/v2/heartbeat")


class Ollama(Service):
    def healthy(self) -> bool:
        return _http_ok("http://127.0.0.1:11434/api/version")


class Inspector(Service):
    def link(self) -> str | None:
        for line in reversed(self.logs):
            m = INSPECTOR_URL.search(line)
            if m:
                return m.group(0)
        return "http://localhost:6274/"


def _ollama_cmd() -> list[str]:
    exe = _find_ollama()
    if not exe:
        raise RuntimeError("No encontré Ollama. Instalalo desde https://ollama.com/download.")
    return [exe, "serve"]


SERVICES: dict[str, Service] = {
    s.key: s
    for s in (
        Chroma("chroma", "Chroma", "Base vectorial compartida por todos los clientes MCP",
               CHROMA_PORT,
               ["uv", "run", "chroma", "run", "--path", "./data/chroma",
                "--host", CHROMA_HOST, "--port", str(CHROMA_PORT)],
               env={"RUST_LOG": "warn"}),  # sin esto loguea una línea INFO por cada request
        Ollama("ollama", "Ollama", "Embeddings con nomic-embed-text", 11434, _ollama_cmd),
        Inspector("inspector", "Inspector MCP", "Probar las tools a mano en el navegador", 6274,
                  ["npx", "-y", "@modelcontextprotocol/inspector", "uv", "run", "python", "server.py"],
                  env={"MCP_AUTO_OPEN_ENABLED": "false"}),
    )
}


def stop_all() -> None:
    for s in SERVICES.values():
        try:
            s.stop()
        except Exception as e:
            log.warning("No se pudo apagar %s: %s", s.label, e)
