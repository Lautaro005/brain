"""/organize: panorama del vault para ordenarlo (lo usa la skill integrada del chat y la tool vault_overview).

No escribe nada. Resume lo que sirve para decidir cómo ordenar: carpetas, notas sueltas, notas sin
conexiones en el grafo, sin descripción, tags usados una sola vez o con variantes, y nombres parecidos
(posibles duplicados). La skill (skills/organize.md) le dice al modelo cómo usarlo.
"""
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from .graph import build_graph

SKILL = Path(__file__).resolve().parent / "skills" / "organize.md"
MANAGED = ("memory/", "knowledge/sources/")  # los escribe brain: no se proponen para mover
FIXED = {"BRAIN.md", "profile.md"}
MAX_FILES = 120
DUP_NAME = 0.85


def skill() -> str:
    """Instrucciones de la skill, sin el frontmatter."""
    text = SKILL.read_text(encoding="utf-8")
    return text.split("---", 2)[2].strip() if text.startswith("---") else text


def _norm_tag(t: str) -> str:
    t = t.lower().strip("#").replace("_", "-")
    return re.sub(r"(es|s)$", "", t) if len(t) > 4 else t


def overview() -> dict:
    g = build_graph()
    notes = {n["id"]: n for n in g["nodes"] if n.get("path") and n["kind"] not in ("folder",) and n["id"].endswith(".md")}
    degree: Counter = Counter()
    tags_of: dict[str, list[str]] = defaultdict(list)
    for link in g["links"]:
        s, t, k = link["source"], link["target"], link["kind"]
        if k == "tag":
            tags_of[s].append(t[4:])
            continue
        if k == "folder":
            continue
        degree[s] += 1
        degree[t] += 1
    tag_count = Counter(t for ts in tags_of.values() for t in ts)
    variants: dict[str, set[str]] = defaultdict(set)
    for t in tag_count:
        variants[_norm_tag(t)].add(t)

    own = [p for p in notes if p not in FIXED and not p.startswith(MANAGED)]
    folders = Counter(p.split("/")[0] + "/" if "/" in p else "(raíz)" for p in notes)
    orphans = [p for p in own if degree[p] == 0]
    no_desc = [p for p in own if not (notes[p].get("description") or "").strip()]
    loose = [p for p in own if "/" not in p]
    stems = {p: Path(p).stem.lower() for p in own}
    dups = []
    items = list(stems.items())
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (pa, a), (pb, b) = items[i], items[j]
            if a == b or SequenceMatcher(None, a, b).ratio() >= DUP_NAME:
                dups.append([pa, pb])
    return {
        "total": len(notes),
        "own": len(own),
        "folders": dict(sorted(folders.items())),
        "files": [{"path": p, "description": notes[p].get("description", ""), "tags": sorted(set(tags_of.get(p, []))),
                   "links": degree[p], "words": notes[p].get("words", 0)} for p in sorted(own)[:MAX_FILES]],
        "truncated": max(0, len(own) - MAX_FILES),
        "loose": loose,
        "orphans": orphans,
        "no_description": no_desc,
        "tags": dict(tag_count.most_common()),
        "single_use_tags": sorted(t for t, n in tag_count.items() if n == 1),
        "tag_variants": sorted(sorted(v) for v in variants.values() if len(v) > 1),
        "similar_names": dups[:30],
        "managed": sorted(p for p in notes if p.startswith(MANAGED) or p in FIXED),
    }


def overview_text() -> str:
    """El panorama en Markdown compacto, para el system prompt del chat."""
    o = overview()
    lines = [f"# Panorama del vault\n{o['total']} notas en total; {o['own']} son del usuario (el resto las maneja brain).",
             "Carpetas: " + ", ".join(f"{k} {v}" for k, v in o["folders"].items())]
    lines.append("\n## Notas del usuario (path · links en el grafo · tags · descripción)")
    for f in o["files"]:
        tags = " ".join("#" + t for t in f["tags"])
        lines.append(f"- {f['path']} · {f['links']} links{' · ' + tags if tags else ''} · {f['description'][:90] or '(sin descripción)'}")
    if o["truncated"]:
        lines.append(f"- … y {o['truncated']} más (usá list_vault)")
    def block(title, items, fmt=str):
        if items:
            lines.append(f"\n## {title} ({len(items)})\n" + "\n".join("- " + fmt(x) for x in items[:40]))
    block("Sueltas en la raíz", o["loose"])
    block("Sin conexiones en el grafo", o["orphans"])
    block("Sin descripción", o["no_description"])
    block("Tags con variantes (¿unificar?)", o["tag_variants"], lambda v: ", ".join("#" + t for t in v))
    block("Tags usados una sola vez", o["single_use_tags"], lambda t: "#" + t)
    block("Nombres parecidos (¿duplicados?)", o["similar_names"], lambda d: " ↔ ".join(d))
    if o["tags"]:
        lines.append("\nTags en uso: " + ", ".join(f"#{t} ({n})" for t, n in list(o["tags"].items())[:40]))
    return "\n".join(lines)
