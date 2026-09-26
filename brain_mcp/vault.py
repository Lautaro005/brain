"""Operaciones sobre archivos dentro de vault/. Cada escritura queda registrada en el historial
(brain_mcp/history.py), así cualquier archivo se puede volver a una versión anterior."""
import fcntl
import logging
from contextlib import contextmanager
from pathlib import Path

import frontmatter

from . import history

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
VAULT = (ROOT / "vault").resolve()

# El vault no se versiona con la app: se crea desde esta plantilla la primera vez.
TEMPLATE_BRAIN = """---
name: brain
description: Índice central del brain — dónde está cada cosa
---
# Brain — índice

- Perfil del usuario: `profile.md` (leelo primero para saber con quién hablás)
- Memoria sobre el usuario: `memory/` (usá `add_memory` para guardar hechos nuevos)
- Proyectos: `projects/`
- Skills: `skills/` (usá `list_skills` antes de tareas complejas)
- Conocimiento scrapeado: `knowledge/sources/` o `search_knowledge`
"""
TEMPLATE_DIRS = ("projects", "skills", "memory", "knowledge/sources")


class VaultError(ValueError):
    pass


def ensure_vault() -> None:
    """Crea vault/ con la estructura base si no existe (primer arranque o repo recién clonado)."""
    for d in TEMPLATE_DIRS:
        (VAULT / d).mkdir(parents=True, exist_ok=True)
    brain = VAULT / "BRAIN.md"
    if not brain.exists():
        brain.write_text(TEMPLATE_BRAIN, encoding="utf-8")


def _resolve(path: str) -> Path:
    if not path or not path.strip():
        raise VaultError("Path vacío.")
    p = Path(path)
    if p.is_absolute():
        raise VaultError(f"Path absoluto no permitido: {path}")
    if ".." in p.parts:
        raise VaultError(f"'..' no permitido en el path: {path}")
    if p.parts and p.parts[0].startswith("."):
        raise VaultError(f"No se pueden tocar archivos ocultos del vault: {path}")
    resolved = (VAULT / p).resolve()  # resuelve symlinks
    if resolved != VAULT and VAULT not in resolved.parents:
        raise VaultError(f"El path se sale del vault: {path}")
    return resolved


def _rel(p: Path) -> str:
    return p.relative_to(VAULT).as_posix()


@contextmanager
def _lock():
    """Lock entre procesos: varios server.py (Claude Code + Desktop) + el dashboard pueden escribir
    a la vez, y leer-modificar-escribir + registrar en el historial tiene que ser atómico."""
    history.DATA.mkdir(parents=True, exist_ok=True)
    with open(history.DATA / ".vault.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _read_or_none(p: Path) -> str | None:
    return p.read_text(encoding="utf-8") if p.is_file() else None


def list_files(prefix: str | None = None) -> list[dict]:
    base = _resolve(prefix) if prefix else VAULT
    if not base.exists():
        return []
    files = [base] if base.is_file() else sorted(base.rglob("*.md"))
    out = []
    for f in files:
        if any(part.startswith(".") for part in f.relative_to(VAULT).parts):
            continue
        try:
            desc = frontmatter.load(f).metadata.get("description", "")
        except Exception:
            desc = ""
        out.append({"path": _rel(f), "description": desc})
    return out


def read_file(path: str) -> str:
    p = _resolve(path)
    if not p.is_file():
        raise VaultError(f"No existe: {path}")
    return p.read_text(encoding="utf-8")


def write_file(path: str, content: str, op: str | None = None) -> str:
    p = _resolve(path)
    with _lock():
        before = _read_or_none(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        history.record(op or ("update" if before is not None else "create"), _rel(p), before, content)
    return _rel(p)


def append_file(path: str, content: str) -> str:
    p = _resolve(path)
    with _lock():
        before = _read_or_none(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        after = (before or "") + content
        p.write_text(after, encoding="utf-8")
        history.record("append", _rel(p), before, after)
    return _rel(p)


def str_replace_file(path: str, old: str, new: str) -> str:
    p = _resolve(path)
    with _lock():
        if not p.is_file():
            raise VaultError(f"No existe: {path}")
        text = p.read_text(encoding="utf-8")
        n = text.count(old) if old else 0
        if n != 1:
            raise VaultError(f"'old' tiene que aparecer exactamente 1 vez en {path}; aparece {n}.")
        after = text.replace(old, new, 1)
        p.write_text(after, encoding="utf-8")
        history.record("edit", _rel(p), text, after)
    return _rel(p)


def delete_file(path: str) -> str:
    p = _resolve(path)
    with _lock():
        if not p.is_file():
            raise VaultError(f"No existe: {path}")
        before = p.read_text(encoding="utf-8")
        p.unlink()
        history.record("delete", _rel(p), before, None)
    return _rel(p)


def file_history(path: str, limit: int = 30) -> list[dict]:
    return history.list_changes(path=_rel(_resolve(path)), limit=limit)


def restore_version(path: str, version_id: int) -> str:
    """Deja el archivo como quedó después del cambio version_id (si ese cambio lo borró, lo borra)."""
    p = _resolve(path)
    ch = history.get_change(version_id)
    if not ch or ch["path"] != _rel(p):
        raise VaultError(f"La versión {version_id} no corresponde a {path}.")
    with _lock():
        before = _read_or_none(p)
        if ch["after"] is None:
            if before is None:
                return _rel(p)
            p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(ch["after"], encoding="utf-8")
        history.record("restore", _rel(p), before, ch["after"])
    return _rel(p)
