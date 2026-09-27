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

import frontmatter

from . import vault

PROFILE_PATH = "profile.md"
MEMORY_DIR = "memory"
SOURCES = {"claude": "Claude", "chatgpt": "ChatGPT", "gemini": "Gemini", "other": "Otro"}
DEFAULT_CATEGORY = "General"

BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s+")
HEADING = re.compile(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$")
LABEL_LINE = re.compile(r"^\s*\*\*(.+?)\*\*:?\s*$|^\s*([A-ZÁÉÍÓÚÑ][^:]{1,40}):\s*$")
DATE_PREFIX = re.compile(r"^\[?\d{4}-\d{2}-\d{2}(?:[ T][\d:.]+Z?)?\]?\s*[-:–]?\s*")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def slugify(s: str) -> str:
    return _norm(s).replace(" ", "-")[:50].strip("-") or "general"


def _clean_item(s: str) -> str:
    s = BULLET.sub("", s.strip())
    s = DATE_PREFIX.sub("", s)
    return s.strip().strip('"').strip()


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


def _load(path: str) -> tuple[dict, list[str]]:
    try:
        meta, body = vault.parse(vault.read_file(path))
    except vault.VaultError:
        return {}, []
    items = [_clean_item(l) for l in body.splitlines() if BULLET.match(l)]
    return meta, items


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


def _save(category: str, meta: dict, items: list[str], op: str | None = None) -> str:
    path = _memory_path(category)
    if not items:
        try:
            vault.delete_file(path)
        except vault.VaultError:
            pass
        return path
    sources = sorted(set(meta.get("sources") or []))
    post = frontmatter.Post(
        f"# {category}\n\n" + "\n".join(f"- {i}" for i in items) + "\n",
        name=slugify(category),
        description=f"Memoria · {category} ({len(items)})",
        category=category,
        sources=sources,
        related=_related_names(items),
    )
    return vault.write_file(path, frontmatter.dumps(post) + "\n", op=op)


def add(items: list[str], category: str = DEFAULT_CATEGORY, source: str | None = None) -> dict:
    """Agrega memorias a una categoría (sin duplicar). Devuelve {path, added, total}."""
    category = category.strip() or DEFAULT_CATEGORY
    meta, current = _load(_memory_path(category))
    known = {_norm(i) for i in current}
    new = []
    for raw in items:
        item = _clean_item(raw)
        if len(_norm(item)) >= 3 and _norm(item) not in known:
            known.add(_norm(item))
            new.append(item)
    if not new:
        return {"path": _memory_path(category), "added": 0, "total": len(current)}
    if source:
        meta["sources"] = sorted(set(meta.get("sources") or []) | {source})
    path = _save(category, meta, current + new)
    return {"path": path, "added": len(new), "total": len(current) + len(new)}


def import_categories(categories: list[dict], source: str) -> dict:
    total, files = 0, []
    for c in categories:
        r = add([str(i) for i in c.get("items", [])], str(c.get("category") or DEFAULT_CATEGORY), source)
        total += r["added"]
        if r["added"]:
            files.append(r["path"])
    return {"added": total, "files": files}


def remove_item(category: str, item: str) -> dict:
    meta, items = _load(_memory_path(category))
    keep = [i for i in items if _norm(i) != _norm(item)]
    if len(keep) == len(items):
        raise vault.VaultError("Esa memoria no existe.")
    _save(category, meta, keep, op="edit")
    return {"removed": 1, "total": len(keep)}


def list_all() -> list[dict]:
    out = []
    for f in vault.list_files(MEMORY_DIR):
        meta, items = _load(f["path"])
        out.append({
            "category": str(meta.get("category") or f["path"].rsplit("/", 1)[-1][:-3]),
            "path": f["path"], "sources": meta.get("sources") or [], "items": items,
        })
    return sorted(out, key=lambda c: (-len(c["items"]), c["category"].lower()))


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
