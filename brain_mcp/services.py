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

from . import remote
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
    group = "core"  # "remote": los del acceso remoto, que el dashboard muestra aparte

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
            "key": self.key, "label": self.label, "description": self.description, "group": self.group,
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
            env = self._env() if callable(self._env) else self._env
            # path completo del ejecutable: en Windows "npx" es npx.cmd y "uv" puede no estar en el PATH del proceso
            cmd[0] = shutil.which(cmd[0]) or cmd[0]
            self.logs.append(f"$ {' '.join(cmd)}")
            self.last_exit = None
            self.proc = subprocess.Popen(
                cmd, cwd=ROOT, env={**os.environ, **env},
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
                self.on_line(line)
        code = proc.wait()
        self.logs.append(f"[proceso terminado, código {code}]")

    def on_line(self, line: str) -> None:
        """Cada línea de salida del proceso (para los servicios que sacan datos de sus logs)."""

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


class RemoteMCP(Service):
    """server.py --http: el server MCP por URL, solo en 127.0.0.1 (lo publica el túnel)."""
    group = "remote"


# Líneas de cloudflared sobre sus conexiones con Cloudflare (cada túnel abre ~4, con connIndex=0..3)
CONN_INDEX = re.compile(r"connIndex=(\d+)")
CONN_UP = "Registered tunnel connection"
CONN_DOWN = ("Connection terminated", "Unregistered tunnel connection", "Serve tunnel error", "Retrying connection",
             "Register tunnel error", "Lost connection", "failed to serve tunnel connection")
# el túnel ya no existe del lado de Cloudflare (un quick tunnel que se borró mientras la compu dormía):
# cloudflared reintenta para siempre sin éxito, hay que abrir otro
TUNNEL_GONE = ("Tunnel not found", "Unauthorized: Tunnel", "tunnel not found")


class Tunnel(Service):
    """cloudflared: publica el server MCP por HTTP en internet (ver remote.py).

    La URL se toma de los logs cuando aparece y se guarda: antes se buscaba en los últimos 500 renglones, y
    cuando la compu se dormía cloudflared llenaba los logs de reintentos, la URL se perdía y la tarjeta quedaba
    en «Abriendo el túnel…» aunque el túnel siguiera vivo. También se siguen sus conexiones con Cloudflare,
    así el watchdog (RemoteWatchdog) sabe si se recuperó solo o hay que reabrirlo."""
    group = "remote"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._reset()
        self.prev_url: str | None = None      # la URL anterior, si cambió al reabrir el túnel
        self.url_changed_at: float | None = None
        self.restarts = 0

    def _reset(self) -> None:
        self.url: str | None = None
        self.conns: set[str] = set()
        self.seen_conn = False                # vimos al menos un "Registered…": el formato de log es el esperado
        self.down_since: float | None = None  # desde cuándo no hay ninguna conexión activa
        self.gone = False

    def on_line(self, line: str) -> None:
        m = remote.QUICK_URL.search(line)
        if m and "api.trycloudflare.com" not in m.group(0):
            self.url = m.group(0)
        idx = CONN_INDEX.search(line)
        if CONN_UP in line:
            self.conns.add(idx.group(1) if idx else "0")
            self.seen_conn, self.down_since = True, None
        elif idx and any(w in line for w in CONN_DOWN):
            self.conns.discard(idx.group(1))
            if not self.conns and self.down_since is None:
                self.down_since = time.time()
        if any(w in line for w in TUNNEL_GONE):
            self.gone = True

    def connected(self) -> bool | None:
        """True/False según las conexiones con Cloudflare; None si todavía no se puede saber."""
        if not self.seen_conn:
            return None
        return bool(self.conns)

    def healthy(self) -> bool:
        # cloudflared no abre un puerto propio: está listo cuando ya tiene la URL pública
        return self.ours() and self.link() is not None

    def link(self) -> str | None:
        cfg = remote.load()
        if cfg["mode"] == "named":
            return f"https://{cfg['hostname']}" if self.seen_conn and cfg["hostname"] else None
        return self.url or remote.tunnel_url(self.logs)

    def info(self) -> dict:
        d = super().info()
        d.update(reconnecting=self.ours() and self.connected() is False, restarts=self.restarts,
                 url_changed_at=self.url_changed_at, prev_url=self.prev_url)
        return d

    def start(self) -> None:
        old = self.link() if self.proc is not None else None
        self.logs.clear()  # si no, la URL de la vez anterior (quick cambia en cada arranque) parecería lista
        self._reset()
        self._old_url = old
        super().start()

    def on_ready(self) -> None:
        """Lo llama el watchdog cuando el túnel (re)abierto ya tiene URL: anota si cambió."""
        old, new = getattr(self, "_old_url", None), self.link()
        if old and new and old != new:
            self.prev_url, self.url_changed_at = old, time.time()
        self._old_url = None


def _tunnel_cmd() -> list[str]:
    return remote.tunnel_command()[0]


def _tunnel_env() -> dict:
    return remote.tunnel_command()[1]


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
        RemoteMCP("remote_mcp", "MCP por URL", "El server MCP por HTTP, solo en esta máquina", remote.PORT,
                  ["uv", "run", "python", "server.py", "--http"], env={"BRAIN_REMOTE": "1"}),
        Tunnel("tunnel", "Túnel de Cloudflare", "Publica el MCP por URL en internet", remote.PORT, _tunnel_cmd, env=_tunnel_env),
    )
}


class KeepAwake:
    """Que la compu no se duerma sola (por inactividad) mientras el acceso remoto está prendido: con la compu
    dormida no hay túnel ni server que respondan. La pantalla sí se puede apagar. Si el usuario cierra la tapa
    o la duerme a mano, se duerme igual: al despertar, el watchdog recupera el túnel.
    - macOS: `caffeinate -i -s -w <pid del dashboard>` (se cierra solo si el dashboard muere).
    - Linux: `systemd-inhibit --what=idle:sleep … sleep infinity`, si hay systemd.
    - Windows: SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) desde un thread propio."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self._win_stop: threading.Event | None = None
        self.method: str | None = None
        self.failed = False  # el sistema no lo permitió (ej. systemd-inhibit sin permiso): no reintentar cada 5 s

    def active(self) -> bool:
        if self._win_stop is not None:
            return True
        return self.proc is not None and self.proc.poll() is None

    def _command(self) -> list[str] | None:
        import platform
        if platform.system() == "Darwin" and shutil.which("caffeinate"):
            return ["caffeinate", "-i", "-s", "-w", str(os.getpid())]
        if platform.system() == "Linux" and shutil.which("systemd-inhibit") and shutil.which("sleep"):
            return ["systemd-inhibit", "--what=idle:sleep", "--who=brain", "--why=Acceso remoto de brain prendido",
                    "--mode=block", "sleep", "infinity"]
        return None

    def start(self) -> bool:
        if self.active():
            return True
        if self.failed:
            return False
        if self.proc is not None:  # se cerró solo: este sistema no deja evitar que se duerma
            log.info("no se pudo evitar que la compu se duerma (%s salió con %s)", self.method, self.proc.returncode)
            self.proc, self.method, self.failed = None, None, True
            return False
        if os.name == "nt":  # pragma: no cover - se prueba en Windows
            import ctypes
            stop = threading.Event()

            def hold():
                k = ctypes.windll.kernel32
                k.SetThreadExecutionState(0x80000000 | 0x00000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
                stop.wait()
                k.SetThreadExecutionState(0x80000000)
            threading.Thread(target=hold, name="keep-awake", daemon=True).start()
            self._win_stop, self.method = stop, "SetThreadExecutionState"
            return True
        cmd = self._command()
        if not cmd:
            self.failed = True
            return False
        try:
            self.proc = subprocess.Popen([shutil.which(cmd[0]) or cmd[0], *cmd[1:]], stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_GROUP)
            self.method = cmd[0]
            return True
        except OSError as e:
            log.info("no se pudo evitar que la compu se duerma: %s", e)
            self.failed = True
            return False

    def stop(self) -> None:
        if self._win_stop is not None:
            self._win_stop.set()
            self._win_stop = None
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                _terminate(proc, force=False)
                proc.wait(timeout=3)
            except Exception:
                try:
                    _terminate(proc, force=True)
                except Exception:
                    pass
        self.method, self.failed = None, False


KEEP_AWAKE = KeepAwake()


class RemoteWatchdog:
    """Mantiene vivo el acceso remoto mientras está prendido (`enabled` en data/remote.json):
    - si se cayó el server MCP por HTTP o cloudflared, los vuelve a prender;
    - si la compu se durmió (el thread no corrió por más de un minuto) o cloudflared perdió todas sus
      conexiones con Cloudflare, le da un rato para reconectarse solo (con el mismo túnel, la URL no cambia)
      y, si no puede o el túnel ya no existe del lado de Cloudflare, lo reabre;
    - prende y apaga KeepAwake.
    Solo lo corre el dashboard (dashboard.main), igual que el resto de los servicios."""
    INTERVAL = 5
    GRACE = 45        # segundos sin conexiones antes de reabrir el túnel
    NO_URL = 90       # segundos sin URL desde que arrancó antes de reabrirlo
    MIN_GAP = 60      # como mucho un reinicio por minuto…
    BACKOFF = 300     # …y cada 5 minutos si ya falló varias veces seguidas

    def __init__(self, mcp: Service, tunnel: Tunnel, awake: KeepAwake):
        self.mcp, self.tunnel, self.awake = mcp, tunnel, awake
        self.last_tick = time.time()
        self.woke_at: float | None = None
        self.last_restart = 0.0
        self.fails = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _restart_tunnel(self, why: str, now: float) -> None:
        gap = self.BACKOFF if self.fails >= 3 else self.MIN_GAP
        if now - self.last_restart < gap:
            return
        self.last_restart = now
        self.fails += 1
        self.tunnel.restarts += 1
        log.info("acceso remoto: reabro el túnel (%s)", why)
        self.tunnel.stop()
        self.tunnel.start()
        self.tunnel.logs.append(f"[brain] túnel reabierto: {why}")

    def tick(self, now: float | None = None) -> str | None:
        """Una vuelta. Devuelve qué hizo (para los tests y los logs) o None."""
        with self._lock:
            return self._tick(time.time() if now is None else now)

    def _tick(self, now: float) -> str | None:
        slept = now - self.last_tick > max(60, self.INTERVAL * 6)
        self.last_tick = now
        cfg = remote.load()
        if not cfg["enabled"]:
            self.awake.stop()
            self.fails, self.woke_at = 0, None
            return None
        if cfg.get("keep_awake", True):
            self.awake.start()
        else:
            self.awake.stop()
        if slept:
            self.woke_at = now
            self.tunnel.logs.append("[brain] la compu se despertó: reviso el túnel")
        if self.mcp.status() == "external":
            return None  # otro programa tomó el puerto: no se publica (lo avisa la tarjeta)
        did = None
        if not self.mcp.ours():
            try:
                self.mcp.start()
                did = "mcp_started"
            except Exception as e:
                log.warning("acceso remoto: no se pudo volver a prender el server MCP: %s", e)
                return "mcp_failed"
        t = self.tunnel
        if not t.ours():
            if now - self.last_restart >= (self.BACKOFF if self.fails >= 3 else self.MIN_GAP):
                try:
                    self.last_restart, self.fails = now, self.fails + 1
                    t.restarts += 1
                    t.start()
                    t.logs.append("[brain] cloudflared se había cerrado: lo volví a abrir")
                    return "tunnel_started"
                except Exception as e:
                    log.warning("acceso remoto: no se pudo volver a abrir el túnel: %s", e)
                    return "tunnel_failed"
            return did
        if t.link():
            t.on_ready()
        if t.gone:
            self._restart_tunnel("el túnel ya no existe en Cloudflare", now)
            return "tunnel_restarted"
        up = t.connected()
        if up:
            self.fails, self.woke_at = 0, None
            return did
        started = t.started_at or now
        if up is False and t.down_since and now - t.down_since > self.GRACE:
            self._restart_tunnel("sin conexión con Cloudflare" + (" después de dormir" if self.woke_at else ""), now)
            return "tunnel_restarted"
        if up is None and not t.link() and now - started > self.NO_URL:
            self._restart_tunnel("no apareció la URL", now)
            return "tunnel_restarted"
        return did

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.last_tick = time.time()

        def loop():
            while not self._stop.wait(self.INTERVAL):
                try:
                    self.tick()
                except Exception:
                    log.exception("watchdog del acceso remoto")
        self._thread = threading.Thread(target=loop, name="remote-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.awake.stop()


WATCHDOG = RemoteWatchdog(SERVICES["remote_mcp"], SERVICES["tunnel"], KEEP_AWAKE)


def stop_all() -> None:
    try:
        WATCHDOG.stop()
    except Exception:
        pass
    for s in SERVICES.values():
        try:
            s.stop()
        except Exception as e:
            log.warning("No se pudo apagar %s: %s", s.label, e)
