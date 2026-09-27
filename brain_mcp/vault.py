"""Operaciones sobre archivos dentro de vault/. Cada escritura queda registrada en el historial
(brain_mcp/history.py), así cualquier archivo se puede volver a una versión anterior."""
import fcntl
import json
import logging
import re
from contextlib import contextmanager
from pathlib import Path

import frontmatter
import yaml

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


# ---------- frontmatter ----------
# Los agentes (y sobre todo los modelos chicos del chat) escriben YAML a mano y es fácil que dejen un
# valor sin comillas con ": " adentro. Ese archivo después no se puede leer: el grafo y list_vault
# pierden su description sin avisar. Por eso toda escritura valida el frontmatter, lo repara si puede
# (poniendo entre comillas los valores problemáticos) y si no, rechaza la escritura con un error claro.

_FM = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_FM_LINE = re.compile(r"^(\s*[\w\-]+):[ \t]+(.+?)\s*$")


def _fm_ok(fm: str) -> bool:
    try:
        data = yaml.safe_load(fm)
    except yaml.YAMLError:
        return False
    return data is None or isinstance(data, dict)


def _fm_repair(fm: str) -> str:
    out = []
    for line in fm.splitlines():
        m = _FM_LINE.match(line)
        if m and not m.group(2).startswith(("'", '"', "[", "{", "|", ">", "&", "*", "!")):
            test = f"{m.group(1).strip()}: {m.group(2)}"
            if not _fm_ok(test) or not isinstance((yaml.safe_load(test) or {}).get(m.group(1).strip()), (str, int, float, bool, type(None))):
                line = f"{m.group(1)}: {json.dumps(m.group(2), ensure_ascii=False)}"
        out.append(line)
    return "\n".join(out)


def check_frontmatter(text: str, path: str = "") -> str:
    """Devuelve el texto con el frontmatter válido (reparado si hacía falta) o levanta VaultError."""
    m = _FM.match(text)
    if not m or _fm_ok(m.group(1)):
        return text
    fixed = _fm_repair(m.group(1))
    if _fm_ok(fixed):
        if path:
            log.info("frontmatter reparado en %s", path)
        return text[:m.start(1)] + fixed + text[m.end(1):]
    raise VaultError(
        f"El frontmatter YAML de {path or 'el archivo'} no es válido y no se pudo reparar. "
        "Poné entre comillas los valores que tengan ':' o '#', o usá set_frontmatter(path, fields)."
    )


def parse(text: str) -> tuple[dict, str]:
    """(metadata, contenido) sin romperse nunca: si el YAML es inválido, lo repara o lo ignora."""
    try:
        post = frontmatter.loads(text)
        return dict(post.metadata), post.content
    except Exception:
        pass
    try:
        post = frontmatter.loads(check_frontmatter(text))
        return dict(post.metadata), post.content
    except Exception:
        m = _FM.match(text)
        return {}, text[m.end():] if m else text


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
            desc = parse(f.read_text(encoding="utf-8"))[0].get("description", "")
        except Exception:
            desc = ""
        out.append({"path": _rel(f), "description": desc})
    return out


def read_file(path: str) -> str:
    p = _resolve(path)
    if not p.is_file():
        raise VaultError(f"No existe: {path}")
    return p.read_text(encoding="utf-8")


def _checked(p: Path, text: str) -> str:
    return check_frontmatter(text, _rel(p)) if p.suffix == ".md" else text


def write_file(path: str, content: str, op: str | None = None) -> str:
    p = _resolve(path)
    content = _checked(p, content)
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
        after = _checked(p, (before or "") + content)
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
        after = _checked(p, text.replace(old, new, 1))
        p.write_text(after, encoding="utf-8")
        history.record("edit", _rel(p), text, after)
    return _rel(p)


def set_frontmatter(path: str, fields: dict) -> str:
    """Cambia campos del frontmatter sin tocar el contenido. Un valor None borra el campo."""
    p = _resolve(path)
    if not isinstance(fields, dict) or not fields:
        raise VaultError("fields tiene que ser un objeto con al menos un campo, ej. {\"description\": \"...\"}.")
    with _lock():
        if not p.is_file():
            raise VaultError(f"No existe: {path}")
        text = p.read_text(encoding="utf-8")
        meta, body = parse(text)
        for k, v in fields.items():
            if v is None:
                meta.pop(str(k), None)
            else:
                meta[str(k)] = v
        after = frontmatter.dumps(frontmatter.Post(body, **meta)) + "\n"
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
