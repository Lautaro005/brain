"""Perfil y memoria del usuario dentro del vault.

- profile.md: datos que el usuario carga sobre sí mismo (nombre, en una línea, sobre mí).
- memory/<categoria>.md: un archivo por categoría con un hecho por viñeta. Se llena importando la
  memoria de otro chatbot (Claude, ChatGPT, Gemini…) o con la tool add_memory.

En el grafo, profile.md es un nodo central y cada categoría de memoria cuelga de él; si una
memoria menciona un proyecto/skill/fuente existente, la categoría queda relacionada con ese nodo.
"""
import json
import re
import unicodedata
from datetime import date

import frontmatter

from . import entities, vault

PROFILE_PATH = "profile.md"
MEMORY_DIR = "memory"
SOURCES = {"claude": "Claude", "chatgpt": "ChatGPT", "gemini": "Gemini", "other": "Otro"}
DEFAULT_CATEGORY = "General"
HISTORY_HEADING = "## Historial"

BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s+")
HEADING = re.compile(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$")
LABEL_LINE = re.compile(r"^\s*\*\*(.+?)\*\*:?\s*$|^\s*([A-ZÁÉÍÓÚÑ][^:]{1,40}):\s*$")
DATE_PREFIX = re.compile(r"^\[?\d{4}-\d{2}-\d{2}(?:[ T][\d:.]+Z?)?\]?\s*[-:–]?\s*")
# fecha en que se guardó un hecho: comentario HTML al final del bullet (no se ve al leer la nota)
SINCE = re.compile(r"\s*<!--\s*since:(\d{4}-\d{2}-\d{2})\s*-->\s*$")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def slugify(s: str) -> str:
    return _norm(s).replace(" ", "-")[:50].strip("-") or "general"


def _strip_since(line: str) -> tuple[str, str | None]:
    """'Vive en Palermo <!-- since:2026-09-28 -->' → ('Vive en Palermo', '2026-09-28')."""
    m = SINCE.search(line)
    return (line[:m.start()], m.group(1)) if m else (line, None)


def _clean_item(s: str) -> str:
    s = _strip_since(s)[0]
    s = BULLET.sub("", s.strip())
    s = DATE_PREFIX.sub("", s)
    return s.strip().strip('"').strip()


def _today() -> str:
    return date.today().isoformat()


# ---------- parser ----------

def _from_json(data) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    def walk(node, category: str) -> None:
        if isinstance(node, str):
            if node.strip():
                out.append((category, node))
        elif isinstance(node, list):
            for x in node:
                walk(x, category)
        elif isinstance(node, dict):
            # formatos tipo {"content": "..."} / {"memory": "..."} / {"text": "..."}
            for key in ("content", "memory", "text", "value", "fact"):
                if isinstance(node.get(key), str):
                    cat = node.get("category") or node.get("type") or category
                    out.append((str(cat), node[key]))
                    return
            for k, v in node.items():
                walk(v, category if k.lower() in ("memories", "items", "data", "facts") else str(k))

    walk(data, DEFAULT_CATEGORY)
    return out


def _from_text(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    category = DEFAULT_CATEGORY
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line in ("---", "***"):
            continue
        m = HEADING.match(line)
        if m:
            category = m.group(1).strip("*: ").strip()
            continue
        m = LABEL_LINE.match(line)
        if m and not BULLET.match(line):
            category = (m.group(1) or m.group(2)).strip()
            continue
        out.append((category, line))
    return out


def parse(text: str) -> list[dict]:
    """Texto pegado o archivo (markdown, lista plana o JSON) → [{category, items: [...]}].
    Deduplica y descarta frases de relleno del chatbot ("Acá tenés…")."""
    text = (text or "").strip()
    pairs: list[tuple[str, str]]
    try:
        pairs = _from_json(json.loads(text)) if text[:1] in "[{" else _from_text(text)
    except json.JSONDecodeError:
        pairs = _from_text(text)

    cats: dict[str, list[str]] = {}
    seen: set[str] = set()
    for cat, item in pairs:
        item = _clean_item(item)
        key = _norm(item)
        if len(key) < 3 or key in seen or len(item) > 1000:
            continue
        seen.add(key)
        cats.setdefault(cat.strip() or DEFAULT_CATEGORY, []).append(item)
    return [{"category": c, "items": items} for c, items in cats.items()]


# ---------- archivos de memoria ----------

def _memory_path(category: str) -> str:
    return f"{MEMORY_DIR}/{slugify(category)}.md"


def _load(path: str) -> tuple[dict, list[dict], list[str]]:
    """(meta, hechos activos [{text, since}], líneas de ## Historial tal cual están)."""
    try:
        meta, body = vault.parse(vault.read_file(path))
    except vault.VaultError:
        return {}, [], []
    items, old, in_history = [], [], False
    for line in body.splitlines():
        if line.strip().lower() == HISTORY_HEADING.lower():
            in_history = True
            continue
        if not BULLET.match(line):
            continue
        if in_history:
            old.append(BULLET.sub("", line.strip()))
        else:
            items.append({"text": _clean_item(line), "since": _strip_since(line)[1]})
    return meta, items, old


def _texts(items: list[dict]) -> list[str]:
    return [i["text"] for i in items]


def _find(items: list[dict], target: str) -> int | None:
    """Índice del hecho activo que coincide con target: igual con _norm(), o si no hay, el único
    que lo contiene (o está contenido en él). None si no hay uno solo claro."""
    t = _norm(target)
    if len(t) < 3:
        return None
    for i, it in enumerate(items):
        if _norm(it["text"]) == t:
            return i
    near = [i for i, it in enumerate(items) if t in _norm(it["text"]) or _norm(it["text"]) in t]
    return near[0] if len(near) == 1 else None


def _related_names(items: list[str]) -> list[str]:
    """Nombres de proyectos/skills/fuentes/notas del vault que aparecen mencionados en las memorias."""
    text = " " + _norm(" ".join(items)) + " "
    names = []
    for f in vault.list_files():
        p = f["path"]
        if p.startswith(MEMORY_DIR + "/") or p in ("BRAIN.md", PROFILE_PATH):
            continue
        try:
            name = str(vault.parse(vault.read_file(p))[0].get("name") or p.rsplit("/", 1)[-1][:-3])
        except Exception:
            continue
        if len(_norm(name)) >= 4 and f" {_norm(name)} " in text:
            names.append(name)
    return sorted(set(names))


def _save(category: str, meta: dict, items: list[dict], old: list[str] | None = None,
          op: str | None = None, path: str | None = None) -> str:
    """Escribe los hechos activos (cada uno con su <!-- since -->) y, si hay, la sección
    ## Historial al final del mismo archivo."""
    path = path or _memory_path(category)
    old = old or []
    if not items and not old:
        try:
            vault.delete_file(path)
        except vault.VaultError:
            pass
        return path
    lines = [f"- {i['text']}" + (f" <!-- since:{i['since']} -->" if i.get("since") else "") for i in items]
    body = f"# {category}\n\n" + "\n".join(lines) + "\n"
    if old:
        body += f"\n{HISTORY_HEADING}\n\n" + "\n".join(f"- {o}" for o in old) + "\n"
    sources = sorted(set(meta.get("sources") or []))
    fields = dict(
        name=slugify(category),
        description=f"Memoria · {category} ({len(items)})",
        category=category,
        sources=sources,
        related=_related_names(_texts(items)),
    )
    ents = entities.extract("\n".join(_texts(items))) if items else []
    if ents:
        fields["entities"] = ents
    elif meta.get("entities"):  # sin Ollama no se pierden las que ya había
        fields["entities"] = meta["entities"]
    post = frontmatter.Post(body, **fields)
    return vault.write_file(path, frontmatter.dumps(post) + "\n", op=op)


def _category_of(path: str, meta: dict) -> str:
    return str(meta.get("category") or path.rsplit("/", 1)[-1][:-3])


def add(items: list[str], category: str = DEFAULT_CATEGORY, source: str | None = None,
        supersede: str | None = None) -> dict:
    """Agrega memorias a una categoría (sin duplicar). Devuelve {path, added, total}.

    supersede: texto de un hecho activo que el nuevo reemplaza (cambió de dirección, de trabajo…).
    Ese hecho no se borra: pasa a ## Historial de su archivo, con la fecha y el hecho nuevo. Se
    busca primero en la misma categoría y después en las demás. Si no aparece, se guarda igual y
    la respuesta trae superseded=None + warning."""
    category = category.strip() or DEFAULT_CATEGORY
    path = _memory_path(category)
    meta, current, old = _load(path)
    known = {_norm(i["text"]) for i in current}
    today = _today()
    new = []
    for raw in items:
        item = _clean_item(raw)
        if len(_norm(item)) >= 3 and _norm(item) not in known:
            known.add(_norm(item))
            new.append({"text": item, "since": today})

    superseded, warning = None, None
    if supersede and supersede.strip():
        note_to = new[0]["text"] if new else _clean_item(items[0]) if items else ""
        idx = _find(current, supersede)
        if idx is not None and _norm(current[idx]["text"]) != _norm(note_to):
            gone = current.pop(idx)
            old.append(_history_line(gone, note_to, today))
            superseded = {"text": gone["text"], "path": path}
        elif idx is None:
            # otra categoría: el hecho viejo pasa al historial de SU archivo
            for f in vault.list_files(MEMORY_DIR):
                if f["path"] == path:
                    continue
                m2, cur2, old2 = _load(f["path"])
                j = _find(cur2, supersede)
                if j is not None:
                    gone = cur2.pop(j)
                    old2.append(_history_line(gone, note_to, today))
                    _save(_category_of(f["path"], m2), m2, cur2, old2, op="edit", path=f["path"])
                    superseded = {"text": gone["text"], "path": f["path"]}
                    break
        if superseded is None:
            warning = "no se encontró un hecho activo que coincida con supersede, se guardó igual"

    if not new and not (superseded and superseded["path"] == path):
        return {"path": path, "added": 0, "total": len(current), "superseded": superseded, "warning": warning}
    if source:
        meta["sources"] = sorted(set(meta.get("sources") or []) | {source})
    path = _save(category, meta, current + new, old)
    return {"path": path, "added": len(new), "total": len(current) + len(new),
            "superseded": superseded, "warning": warning}


def _history_line(fact: dict, replaced_by: str, today: str) -> str:
    since = f"desde {fact['since']}, " if fact.get("since") else ""
    return f'{fact["text"]} ({since}superado el {today}, ver: "{replaced_by}")'


def import_categories(categories: list[dict], source: str) -> dict:
    total, files = 0, []
    for c in categories:
        r = add([str(i) for i in c.get("items", [])], str(c.get("category") or DEFAULT_CATEGORY), source)
        total += r["added"]
        if r["added"]:
            files.append(r["path"])
    return {"added": total, "files": files}


def remove_item(category: str, item: str) -> dict:
    path = _memory_path(category)
    meta, items, old = _load(path)
    keep = [i for i in items if _norm(i["text"]) != _norm(item)]
    if len(keep) == len(items):
        raise vault.VaultError("Esa memoria no existe.")
    _save(category, meta, keep, old, op="edit")
    return {"removed": 1, "total": len(keep)}


def list_all() -> list[dict]:
    """Solo hechos activos (lo superado queda en ## Historial, ver history())."""
    out = []
    for f in vault.list_files(MEMORY_DIR):
        meta, items, _ = _load(f["path"])
        out.append({
            "category": _category_of(f["path"], meta),
            "path": f["path"], "sources": meta.get("sources") or [],
            "items": _texts(items), "since": [i["since"] for i in items],
        })
    return sorted(out, key=lambda c: (-len(c["items"]), c["category"].lower()))


def history(category: str) -> list[str]:
    """Hechos superados de una categoría (sección ## Historial de su archivo)."""
    return _load(_memory_path(category))[2]


# ---------- perfil ----------

def get_profile() -> dict:
    try:
        m, body = vault.parse(vault.read_file(PROFILE_PATH))
    except vault.VaultError:
        return {"exists": False, "name": "", "headline": "", "about": ""}
    # el cuerpo es "# Nombre" + "Sobre mí"; un agente puede haberlo reescrito sin la línea en blanco
    # (o solo con el título), así que se saca el título sin asumir cuántas líneas hay
    body = body.strip()
    if body.startswith("# "):
        body = body.split("\n", 1)[1] if "\n" in body else ""
    return {"exists": True, "name": str(m.get("display_name") or ""),
            "headline": str(m.get("headline") or ""), "about": body.strip()}


def save_profile(name: str, headline: str, about: str) -> str:
    name, headline, about = name.strip(), headline.strip(), about.strip()
    title = name or "Perfil"
    post = frontmatter.Post(
        f"# {title}\n\n{about}\n" if about else f"# {title}\n",
        name="profile",
        description=headline or "Perfil del usuario",
        display_name=name,
        headline=headline,
    )
    return vault.write_file(PROFILE_PATH, frontmatter.dumps(post) + "\n")
