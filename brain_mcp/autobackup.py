"""Backup automático (Ajustes → Backup → «Backup automático»).

Lo corre el dashboard mientras está abierto: un thread revisa cada 30 s si toca hacer un backup y, si toca,
llama a `backup.create()` con la carpeta, el nombre y las credenciales que eligió el usuario. Si la compu
estuvo apagada (o el dashboard cerrado) cuando tocaba, se hace uno al volver a abrirlo.

Ajustes y estado en data/backup_auto.json:
- `enabled`, `folder` (vacío = data/backups), `frequency` (hours | daily | weekly), `hours`, `at` (HH:MM),
  `weekday` (0 = lunes), `name` (plantilla con {date}, {time}, {version} y {n}), `keep` (cuántos conservar;
  0 = todos), `include_secrets`, `only_changes` (saltear si no cambió nada desde el último).
- `state`: último backup, último error, contador {n} y la lista de archivos que hizo brain. La limpieza
  (`keep`) solo borra archivos de esa lista: nunca toca otros .zip que haya en la carpeta.
"""
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

from . import backup, history, locks, updates, vault

log = logging.getLogger(__name__)

FREQUENCIES = ("hours", "daily", "weekly")
DEFAULTS = {"enabled": False, "folder": "", "frequency": "daily", "hours": 6, "at": "03:00", "weekday": 0,
            "name": "brain-auto-{date}-{time}", "keep": 10, "include_secrets": False, "only_changes": True}
TOKENS = ("date", "time", "version", "n")
RETRY = 15 * 60          # después de un error, se reintenta a los 15 minutos
TICK = 30                # cada cuánto mira el scheduler
_RUN = threading.Lock()  # un backup a la vez dentro del proceso (el botón «Hacer uno ahora» y el thread)


class AutoBackupError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code, self.detail = code, detail


def _path() -> Path:
    return history.DATA / "backup_auto.json"


def _read() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load() -> dict:
    raw = _read()
    cfg = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
    cfg["state"] = raw.get("state") or {}
    return cfg


def _write(cfg: dict) -> dict:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return cfg


def _update_state(**changes) -> dict:
    with locks.locked(history.DATA / ".backup_auto.lock"):
        cfg = load()
        cfg["state"] = {**cfg["state"], **changes}
        return _write(cfg)


def folder(cfg: dict | None = None) -> Path:
    f = (cfg or load())["folder"]
    return Path(f) if f else backup.backups_dir()


# ---------- validación ----------

def check_folder(raw: str) -> str:
    """Carpeta absoluta, que se pueda crear y escribir, y que no esté dentro del vault (el backup se
    incluiría a sí mismo). Vacío = data/backups. Devuelve el path normalizado."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    p = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not p.is_absolute():
        raise AutoBackupError("folder_relative", raw)
    p = p.resolve()
    v = vault.VAULT.resolve()
    if p == v or v in p.parents:
        raise AutoBackupError("folder_in_vault", str(p))
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / f".brain-write-test-{os.getpid()}"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        raise AutoBackupError("folder_not_writable", f"{p}: {e.strerror or e}") from e
    return str(p)


def check_name(template: str) -> str:
    t = (template or "").strip()
    if t.lower().endswith(".zip"):
        t = t[:-4]
    if not t or len(t) > 80 or not re.fullmatch(r"[\w.{}-]+", t, re.A):
        raise AutoBackupError("bad_name", template)
    unknown = [tok for tok in re.findall(r"\{(\w*)\}", t) if tok not in TOKENS]
    if unknown or re.sub(r"\{\w*\}", "", t).count("{") or re.sub(r"\{\w*\}", "", t).count("}"):
        raise AutoBackupError("bad_name", template)
    return t


def render_name(template: str, n: int, now: datetime | None = None) -> str:
    now = now or datetime.now()
    ver = re.sub(r"[^\w.-]", "", updates.current())
    out = (template.replace("{date}", now.strftime("%Y-%m-%d")).replace("{time}", now.strftime("%H%M%S"))
           .replace("{version}", ver).replace("{n}", str(n)))
    return out + ".zip"


def save(body: dict) -> dict:
    """Guarda los ajustes (valida todo antes de escribir). Devuelve status()."""
    with locks.locked(history.DATA / ".backup_auto.lock"):
        cfg = load()
        new = dict(cfg)
        if "enabled" in body:
            new["enabled"] = bool(body["enabled"])
        if "folder" in body:
            new["folder"] = check_folder(str(body["folder"] or ""))
        if "frequency" in body:
            if body["frequency"] not in FREQUENCIES:
                raise AutoBackupError("bad_frequency", str(body["frequency"]))
            new["frequency"] = body["frequency"]
        if "hours" in body:
            try:
                h = int(body["hours"])
            except (TypeError, ValueError) as e:
                raise AutoBackupError("bad_hours", str(body["hours"])) from e
            if not 1 <= h <= 24 * 30:
                raise AutoBackupError("bad_hours", str(h))
            new["hours"] = h
        if "at" in body:
            at = str(body["at"] or "").strip()
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at):
                raise AutoBackupError("bad_time", at)
            new["at"] = at
        if "weekday" in body:
            try:
                wd = int(body["weekday"])
            except (TypeError, ValueError) as e:
                raise AutoBackupError("bad_weekday", str(body["weekday"])) from e
            if not 0 <= wd <= 6:
                raise AutoBackupError("bad_weekday", str(wd))
            new["weekday"] = wd
        if "name" in body:
            new["name"] = check_name(str(body["name"]))
        if "keep" in body:
            try:
                k = int(body["keep"])
            except (TypeError, ValueError) as e:
                raise AutoBackupError("bad_keep", str(body["keep"])) from e
            if not 0 <= k <= 1000:
                raise AutoBackupError("bad_keep", str(k))
            new["keep"] = k
        for k in ("include_secrets", "only_changes"):
            if k in body:
                new[k] = bool(body[k])
        st = dict(cfg["state"])
        if new["enabled"] and (not cfg["enabled"] or any(new[k] != cfg[k] for k in ("frequency", "hours", "at", "weekday"))):
            st["since"] = datetime.now().isoformat(timespec="seconds")  # el horario se cuenta desde acá
        new["state"] = st
        _write(new)
    return status()


# ---------- cuándo toca ----------

def _slot(cfg: dict, after: datetime) -> datetime:
    """Primer horario programado estrictamente posterior a `after`."""
    if cfg["frequency"] == "hours":
        return after + timedelta(hours=int(cfg["hours"]))
    hh, mm = (int(x) for x in cfg["at"].split(":"))
    t = after.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if cfg["frequency"] == "daily":
        return t if t > after else t + timedelta(days=1)
    t += timedelta(days=(int(cfg["weekday"]) - t.weekday()) % 7)
    return t if t > after else t + timedelta(days=7)


def _parse(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None


def next_run(cfg: dict | None = None, now: datetime | None = None) -> datetime | None:
    cfg = cfg or load()
    if not cfg["enabled"]:
        return None
    now = now or datetime.now()
    st = cfg["state"]
    base = _parse(st.get("last_run")) or _parse(st.get("since")) or now
    due = _slot(cfg, base)
    # después de un error se reintenta en 15 minutos (y no en cada vuelta del scheduler)
    tried = _parse(st.get("last_attempt"))
    if st.get("last_error") and tried and tried + timedelta(seconds=RETRY) > due:
        due = tried + timedelta(seconds=RETRY)
    return due


def signature() -> str:
    """Cambia cuando cambia algo que va al backup: el historial del vault y los ajustes."""
    try:
        last = history.since(-1)["last_id"]
    except Exception:
        last = -1
    files = 0
    try:
        files = sum(1 for f in vault.VAULT.rglob("*") if f.is_file())
    except OSError:
        pass
    mt = []
    for fn in backup.CONFIG_FILES:
        p = history.DATA / fn
        try:
            mt.append(f"{fn}:{p.stat().st_mtime_ns}")
        except OSError:
            pass
    return f"{last}|{files}|{'|'.join(mt)}"


# ---------- correr ----------

def _prune(cfg: dict, made: list[str]) -> tuple[list[str], list[str]]:
    """Deja los `keep` más nuevos de los que hizo brain. Devuelve (los que quedan, los borrados)."""
    alive = [p for p in made if Path(p).is_file()]
    keep = int(cfg["keep"])
    if not keep or len(alive) <= keep:
        return alive, []
    alive.sort(key=lambda p: Path(p).stat().st_mtime)
    gone = alive[:-keep]
    for p in gone:
        try:
            Path(p).unlink()
        except OSError as e:
            log.warning("no se pudo borrar el backup viejo %s: %s", p, e)
    return [p for p in alive if p not in gone], gone


def run_now(reason: str = "manual") -> dict:
    """Hace un backup con los ajustes del automático (lo usan el scheduler y «Hacer uno ahora»)."""
    if not _RUN.acquire(blocking=False):
        raise AutoBackupError("busy")
    try:
        cfg = load()
        st = cfg["state"]
        n = int(st.get("n") or 0) + 1
        now = datetime.now()
        sig = signature()
        _update_state(last_attempt=now.isoformat(timespec="seconds"))
        try:
            dest = folder(cfg)
            if cfg["folder"] and not dest.is_dir():
                # una carpeta configurada que desapareció (un disco externo desconectado): no se crea
                # en otro lado sin avisar; queda el error en la tarjeta
                raise AutoBackupError("folder_missing", str(dest))
            r = backup.create(include_secrets=cfg["include_secrets"], folder=dest,
                              name=render_name(cfg["name"], n, now), auto=True)
        except AutoBackupError as e:
            _update_state(last_error=f"{e.code}: {e.detail}"[:400])
            raise
        except Exception as e:
            _update_state(last_error=str(e)[:400])
            raise AutoBackupError("failed", str(e)) from e
        made, gone = _prune(cfg, [*(cfg["state"].get("files") or []), r["path"]])
        _update_state(last_run=now.isoformat(timespec="seconds"), last_name=r["name"], last_path=r["path"],
                      last_size=r["size"], last_error="", last_reason=reason, sig=sig, n=n, files=made)
        log.info("backup automático: %s (%s)", r["path"], reason)
        return {**r, "pruned": [Path(p).name for p in gone]}
    finally:
        _RUN.release()


def tick(now: datetime | None = None) -> dict | None:
    """Una vuelta del scheduler: hace el backup si toca. Devuelve el resultado o None."""
    cfg = load()
    if cfg["enabled"] and not (cfg["state"].get("last_run") or cfg["state"].get("since")):
        cfg = _update_state(since=(now or datetime.now()).isoformat(timespec="seconds"))  # prendido a mano en el JSON
    due = next_run(cfg, now)
    if due is None or due > (now or datetime.now()):
        return None
    if cfg["only_changes"] and cfg["state"].get("sig") and cfg["state"]["sig"] == signature() \
            and Path(cfg["state"].get("last_path") or "").is_file():
        # nada cambió desde el último: se corre el horario sin crear un archivo igual al anterior
        _update_state(last_run=(now or datetime.now()).isoformat(timespec="seconds"), last_reason="skipped",
                      last_error="")
        return {"skipped": True}
    try:
        return run_now("scheduled")
    except AutoBackupError as e:
        log.warning("backup automático: %s %s", e.code, e.detail)
        return {"error": e.code}


def status() -> dict:
    cfg = load()
    nxt = next_run(cfg)
    st = cfg["state"]
    return {**{k: v for k, v in cfg.items() if k != "state"},
            "folder_effective": str(folder(cfg)), "default_folder": str(backup.backups_dir()),
            "next_run": nxt.isoformat(timespec="seconds") if nxt else None,
            "last_run": st.get("last_run"), "last_name": st.get("last_name"), "last_path": st.get("last_path"),
            "last_size": st.get("last_size"), "last_error": st.get("last_error") or "", "last_reason": st.get("last_reason"),
            "count": len([p for p in st.get("files") or [] if Path(p).is_file()]),
            "next_n": int(st.get("n") or 0) + 1, "preview": render_name(cfg["name"], int(st.get("n") or 0) + 1),
            "running": _RUN.locked()}


def list_dirs(path: str = "") -> dict:
    """Para el selector de carpeta del dashboard: subcarpetas (sin ocultas) de `path` (o de la carpeta home)."""
    p = Path(os.path.expanduser(path or "~"))
    if not p.is_absolute():
        p = Path.home() / p
    p = p.resolve()
    if not p.is_dir():
        raise AutoBackupError("not_a_dir", str(p))
    dirs = []
    try:
        for c in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            if not c.name.startswith(".") and c.is_dir():
                dirs.append(c.name)
            if len(dirs) >= 500:
                break
    except OSError as e:
        raise AutoBackupError("not_readable", f"{p}: {e.strerror or e}") from e
    roots = []
    if os.name == "nt":  # pragma: no cover - Windows
        roots = [f"{d}:\\" for d in "CDEFGHIJKLMNOPQRSTUVWXYZ" if Path(f"{d}:\\").exists()]
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "dirs": dirs,
            "home": str(Path.home()), "sep": os.sep, "roots": roots}


# ---------- scheduler ----------

_THREAD: dict = {"t": None, "stop": None}


def start_scheduler(delay: float = 90) -> None:
    """Thread daemon del dashboard. `delay`: espera inicial, así Chroma ya está prendido para el primero."""
    if _THREAD["t"] and _THREAD["t"].is_alive():
        return
    stop = threading.Event()

    def loop():
        if stop.wait(delay):
            return
        while not stop.is_set():
            try:
                tick()
            except Exception:
                log.exception("backup automático")
            stop.wait(TICK)

    t = threading.Thread(target=loop, name="autobackup", daemon=True)
    _THREAD.update(t=t, stop=stop)
    t.start()


def stop_scheduler() -> None:
    if _THREAD["stop"]:
        _THREAD["stop"].set()

