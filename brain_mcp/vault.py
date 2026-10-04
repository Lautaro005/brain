"""Operaciones sobre archivos dentro de vault/. Cada escritura queda registrada en el historial
(brain_mcp/history.py), así cualquier archivo se puede volver a una versión anterior."""
import json
import logging
import re
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import frontmatter
import yaml

from . import history, locks

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
VAULT = (ROOT / "vault").resolve()

# El vault no se versiona con la app: se crea desde esta plantilla la primera vez.
TEMPLATE_BRAIN = """---
type: Index
name: brain
title: Índice del brain
description: Índice central del brain — dónde está cada cosa
created: {today}
updated: {today}
---
# Brain — índice

- Perfil del usuario: [profile.md](profile.md) (leelo primero para saber con quién hablás)
- Memoria sobre el usuario: `memory/` (usá `add_memory` para guardar hechos nuevos)
- Proyectos: `projects/`
- Skills: `skills/` (usá `list_skills` antes de tareas complejas)
- Conocimiento scrapeado: `knowledge/sources/` o `search_knowledge`

## Formato de las notas
Markdown con frontmatter YAML, compatible con OKF (Open Knowledge Format): `type` (Project, Memory, Skill,
Source, Profile, Index, Reference o Note), `name`, `title`, `description` (una frase), `tags`, `related`,
y las fechas `created` y `updated`, que brain completa solo. Para linkear notas sirven `[texto](ruta.md)`
(Markdown estándar, más portable) o `[[nombre]]`.
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
        brain.write_text(TEMPLATE_BRAIN.format(today=date.today().isoformat()), encoding="utf-8")


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
    with locks.locked(history.DATA / ".vault.lock"):
        yield


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


# ---------- metadatos de formato (perfil OKF de brain) ----------
# Cada nota lleva `type` (qué es), `created` (cuándo se creó) y `updated` (último cambio). brain los completa
# solo en cada escritura, tocando únicamente esas líneas del YAML (no reformatea el resto del frontmatter).
# OKF (Open Knowledge Format v0.2) solo exige `type`; el resto de los campos de brain son extensiones válidas.

TYPES = {"projects": "Project", "skills": "Skill", "memory": "Memory", "knowledge": "Source"}
STAMP_FIELDS = ("type", "created", "updated")


def note_type(rel: str) -> str:
    if rel == "BRAIN.md" or rel.endswith("/index.md") or rel == "index.md":
        return "Index"
    if rel == "profile.md":
        return "Profile"
    if rel.startswith("knowledge/") and not rel.startswith("knowledge/sources/"):
        return "Reference"
    return TYPES.get(rel.split("/")[0], "Note") if "/" in rel else "Note"


def _day(v) -> str | None:
    """Fecha YYYY-MM-DD de un valor de frontmatter o del historial (ISO con hora), o None."""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    m = re.match(r"\s*['\"]?(\d{4}-\d{2}-\d{2})", str(v or ""))
    return m.group(1) if m else None


def _first_seen(rel: str) -> str | None:
    """Fecha del primer cambio registrado de un archivo (para `created` de notas viejas)."""
    try:
        from contextlib import closing
        with closing(history._connect()) as con:
            row = con.execute("SELECT MIN(ts) FROM changes WHERE path = ?", (rel,)).fetchone()
        return _day(row[0]) if row and row[0] else None
    except Exception:
        return None


def stamp(text: str, rel: str, before: str | None = None, touch: bool = True, today: str | None = None,
          created: str | None = None, updated: str | None = None) -> str:
    """Completa type/created/updated en el frontmatter de `text` (una nota .md):
    - type: si falta, según la carpeta (note_type);
    - created: el que ya tenga; si no, el de la versión anterior; si no, el primer cambio del historial
      (o `created`), y si es nueva, hoy;
    - updated: hoy si `touch`; si no, solo se agrega cuando falta (con `updated` o created).
    Edita solo esas líneas; si el archivo no tiene frontmatter, le agrega uno con esos tres campos."""
    today = today or date.today().isoformat()
    m = _FM.match(text)
    meta = {}
    if m:
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            return text  # check_frontmatter ya lo validó: no debería pasar
        if not isinstance(meta, dict):
            return text
    want: dict[str, str] = {}
    if not meta.get("type"):
        want["type"] = note_type(rel)
    if not _day(meta.get("created")):
        prev = None
        if before:
            pm = _FM.match(before)
            if pm:
                try:
                    prev = _day((yaml.safe_load(pm.group(1)) or {}).get("created"))
                except (yaml.YAMLError, AttributeError):
                    prev = None
        want["created"] = prev or created or (_first_seen(rel) if before is not None else None) or today
    if touch and _day(meta.get("updated")) != today:
        want["updated"] = today
    elif not touch and not _day(meta.get("updated")):
        want["updated"] = updated or want.get("created") or _day(meta.get("created")) or today
    if not want:
        return text
    if not m:
        head = "".join(f"{k}: {want[k]}\n" for k in STAMP_FIELDS if k in want)
        return f"---\n{head}---\n" + text
    lines = m.group(1).split("\n")
    for k in STAMP_FIELDS:
        if k not in want:
            continue
        rx = re.compile(rf"^{k}\s*:")
        idx = next((i for i, ln in enumerate(lines) if rx.match(ln)), None)
        if idx is not None:
            lines[idx] = f"{k}: {want[k]}"
        elif k == "type":
            lines.insert(0, f"type: {want[k]}")
        else:
            lines.append(f"{k}: {want[k]}")
    return text[:m.start(1)] + "\n".join(lines) + text[m.end(1):]


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


def _stamped(p: Path, text: str, before: str | None, touch: bool = True) -> str:
    return stamp(text, _rel(p), before, touch) if p.suffix == ".md" else text


def write_file(path: str, content: str, op: str | None = None, stamp_meta: bool = True) -> str:
    """stamp_meta=False escribe el contenido exacto (deshacer un cambio, volver atrás): no toca type/fechas."""
    p = _resolve(path)
    content = _checked(p, content)
    with _lock():
        before = _read_or_none(p)
        if stamp_meta:
            content = _stamped(p, content, before)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        history.record(op or ("update" if before is not None else "create"), _rel(p), before, content)
    return _rel(p)


def append_file(path: str, content: str) -> str:
    p = _resolve(path)
    with _lock():
        before = _read_or_none(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        after = _stamped(p, _checked(p, (before or "") + content), before)
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
        after = _stamped(p, _checked(p, text.replace(old, new, 1)), text)
        p.write_text(after, encoding="utf-8")
        history.record("edit", _rel(p), text, after)
    return _rel(p)


# campos de control que no cuentan como "actualizar la nota" (no mueven `updated`)
BOOKKEEPING = {"checked_at", "refresh_error"}


def set_frontmatter(path: str, fields: dict, op: str = "edit") -> str:
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
        after = frontmatter.dumps(frontmatter.Post(body, **meta), sort_keys=False) + "\n"
        touch = not set(map(str, fields)) <= BOOKKEEPING and "updated" not in fields
        after = _stamped(p, after, text, touch)
        p.write_text(after, encoding="utf-8")
        history.record(op, _rel(p), text, after)
    return _rel(p)


def backfill_metadata() -> dict:
    """Completa type/created/updated en las notas que no los tienen (Ajustes → Formato de las notas).
    created = primer cambio del historial (o la fecha del archivo), updated = último cambio (o la fecha del
    archivo). No cambia nada más; cada nota tocada queda en el historial con la operación "format"."""
    done, skipped = [], 0
    for f in sorted(VAULT.rglob("*.md")):
        rel = _rel(f)
        if any(part.startswith(".") for part in f.relative_to(VAULT).parts):
            continue
        with _lock():
            text = f.read_text(encoding="utf-8")
            try:
                text_ok = check_frontmatter(text, rel)
            except VaultError:
                skipped += 1
                continue
            mtime = datetime.fromtimestamp(f.stat().st_mtime).date().isoformat()
            try:
                from contextlib import closing
                with closing(history._connect()) as con:
                    first, last = con.execute("SELECT MIN(ts), MAX(ts) FROM changes WHERE path = ?", (rel,)).fetchone()
            except Exception:
                first = last = None
            after = stamp(text_ok, rel, None, touch=False, created=_day(first) or mtime, updated=_day(last) or mtime)
            if after == text:
                continue
            f.write_text(after, encoding="utf-8")
            history.record("format", rel, text, after)
            done.append(rel)
    return {"updated": done, "count": len(done), "skipped": skipped}


def missing_metadata() -> int:
    """Cuántas notas no tienen type, created o updated (para Ajustes)."""
    n = 0
    for f in VAULT.rglob("*.md"):
        if any(part.startswith(".") for part in f.relative_to(VAULT).parts):
            continue
        try:
            meta = parse(f.read_text(encoding="utf-8"))[0]
        except Exception:
            continue
        if not meta.get("type") or not _day(meta.get("created")) or not _day(meta.get("updated")):
            n += 1
    return n


def delete_file(path: str) -> str:
    p = _resolve(path)
    with _lock():
        if not p.is_file():
            raise VaultError(f"No existe: {path}")
        before = p.read_text(encoding="utf-8")
        p.unlink()
        history.record("delete", _rel(p), before, None)
    return _rel(p)


def move_file(src: str, dst: str) -> str:
    """Mueve o renombra un archivo. Queda en el historial como un borrado en src y un alta en dst,
    así cualquiera de los dos se puede restaurar."""
    a, b = _resolve(src), _resolve(dst)
    if a == b:
        raise VaultError("El origen y el destino son el mismo archivo.")
    if a.suffix != b.suffix:
        raise VaultError(f"El destino tiene que tener la misma extensión ({a.suffix}).")
    with _lock():
        if not a.is_file():
            raise VaultError(f"No existe: {src}")
        if b.exists():
            raise VaultError(f"Ya existe: {dst}. Elegí otro nombre o unilos a mano.")
        text = a.read_text(encoding="utf-8")
        b.parent.mkdir(parents=True, exist_ok=True)
        b.write_text(text, encoding="utf-8")
        history.record("move", _rel(b), None, text)
        a.unlink()
        history.record("move", _rel(a), text, None)
        # carpeta vacía que quedó atrás (nunca la raíz del vault)
        parent = a.parent
        while parent != VAULT and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    return _rel(b)


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
