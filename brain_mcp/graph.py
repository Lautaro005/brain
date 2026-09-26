"""Construye el grafo de relaciones del vault (nodos + links) para la vista web."""
import re
from pathlib import Path

import frontmatter

from .vault import VAULT

WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
MDLINK = re.compile(r"\]\(([^)\s]+?\.md)\)")
CODEPATH = re.compile(r"`([\w\-./]+)`")

TOP_KINDS = {"projects": "project", "skills": "skill", "knowledge": "source", "memory": "memory"}


def _kind(rel: str) -> str:
    if rel == "BRAIN.md":
        return "hub"
    if rel == "profile.md":
        return "profile"
    return TOP_KINDS.get(rel.split("/")[0], "note")


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [s.strip() for s in v.split(",") if s.strip()]
    return [str(x) for x in v]


def build_graph() -> dict:
    files = sorted(
        f for f in VAULT.rglob("*.md") if ".git" not in f.relative_to(VAULT).parts
    )
    nodes: dict[str, dict] = {}
    links: set[tuple[str, str, str]] = set()
    docs: dict[str, tuple[dict, str]] = {}
    by_name: dict[str, str] = {}

    def add_dir(rel_dir: str) -> str:
        nid = f"dir:{rel_dir}"
        if nid not in nodes:
            nodes[nid] = {"id": nid, "label": rel_dir.split("/")[-1] + "/", "kind": "folder", "path": rel_dir}
            parent = rel_dir.rsplit("/", 1)[0] if "/" in rel_dir else None
            links.add((add_dir(parent) if parent else "BRAIN.md", nid, "folder"))
        return nid

    for f in files:
        rel = f.relative_to(VAULT).as_posix()
        try:
            post = frontmatter.load(f)
            meta, body = post.metadata, post.content
        except Exception:
            meta, body = {}, f.read_text(encoding="utf-8", errors="replace")
        docs[rel] = (meta, body)
        label = str(meta.get("name") or f.stem)
        if rel == "profile.md":
            label = str(meta.get("display_name") or "profile")
        elif rel.startswith("memory/"):
            label = str(meta.get("category") or label)
        nodes[rel] = {
            "id": rel,
            "label": label,
            "kind": _kind(rel),
            "path": rel,
            "description": str(meta.get("description", "")),
            "url": meta.get("url"),
            "words": len(body.split()),
        }
        for key in {label.lower(), f.stem.lower(), rel.lower(), rel[:-3].lower()}:
            by_name.setdefault(key, rel)

    if "BRAIN.md" not in nodes:
        nodes["BRAIN.md"] = {"id": "BRAIN.md", "label": "brain", "kind": "hub", "path": "BRAIN.md"}

    for rel in docs:
        if "/" in rel:
            links.add((add_dir(rel.rsplit("/", 1)[0]), rel, "folder"))
        # cada categoría de memoria cuelga del perfil (y el perfil del brain)
        if rel.startswith("memory/") and "profile.md" in docs:
            links.add(("profile.md", rel, "memory"))
    if "profile.md" in docs:
        links.add(("BRAIN.md", "profile.md", "link"))

    def resolve(target: str, src: str) -> str | None:
        t = target.strip().strip("/")
        if not t:
            return None
        hit = by_name.get(t.lower())
        if hit:
            return hit
        for base in (Path(src).parent.as_posix(), ""):
            cand = (Path(base) / t).as_posix().lstrip("./")
            if cand in docs:
                return cand
            if (VAULT / cand).is_dir():
                return add_dir(cand)
        return None

    for rel, (meta, body) in docs.items():
        text = body
        targets = [(m, "link") for m in WIKILINK.findall(text)]
        targets += [(m, "link") for m in MDLINK.findall(text)]
        targets += [(m, "mention") for m in CODEPATH.findall(text)]
        targets += [(r, "related") for r in _as_list(meta.get("related"))]
        for t, kind in targets:
            dst = resolve(t, rel)
            if dst and dst != rel:
                links.add((rel, dst, kind))
        for tag in _as_list(meta.get("tags")):
            tid = f"tag:{tag.lower()}"
            nodes.setdefault(tid, {"id": tid, "label": f"#{tag}", "kind": "tag", "path": None})
            links.add((rel, tid, "tag"))

    # una sola línea por par de nodos, quedándose con la relación más fuerte
    rank = {"link": 0, "memory": 1, "related": 2, "mention": 3, "tag": 4, "folder": 5}
    best: dict[frozenset, tuple[str, str, str]] = {}
    for s, t, k in sorted(links):
        key = frozenset((s, t))
        if key not in best or rank[k] < rank[best[key][2]]:
            best[key] = (s, t, k)

    return {
        "nodes": list(nodes.values()),
        "links": [{"source": s, "target": t, "kind": k} for s, t, k in sorted(best.values())],
    }
