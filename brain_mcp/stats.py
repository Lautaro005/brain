"""Estadísticas y chequeos de salud para el dashboard.

Devuelve claves y datos, no textos: la traducción (ES/EN) la hace el dashboard.
"""
import json
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import frontmatter

from . import agents, chroma_store, history, memory
from .graph import build_graph
from .vault import VAULT

HOME = Path.home()
DAYS = 30


def _local_day(ts: str) -> str:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date().isoformat()


def vault_stats() -> dict:
    g = build_graph()
    kinds = Counter(n["kind"] for n in g["nodes"])
    files = [n for n in g["nodes"] if n["kind"] not in ("folder", "tag")]
    real_links = [l for l in g["links"] if l["kind"] != "folder"]

    sources = []
    for f in sorted((VAULT / "knowledge" / "sources").glob("*.md")):
        try:
            meta = frontmatter.load(f).metadata
        except Exception:
            continue
        url = str(meta.get("url") or "")
        sources.append({
            "path": f.relative_to(VAULT).as_posix(),
            "title": str(meta.get("description") or f.stem),
            "url": url,
            "domain": urlparse(url).netloc.removeprefix("www.") if url else "—",
            "scraped_at": str(meta.get("scraped_at") or ""),
            "chunks": len(meta.get("chroma_ids") or []),
        })
    sources.sort(key=lambda s: s["scraped_at"], reverse=True)

    changes = history.all_meta()
    per_day = Counter(_local_day(c["ts"]) for c in changes)
    today = date.today()
    activity = [
        {"day": (d := today - timedelta(days=i)).isoformat(), "count": per_day.get(d.isoformat(), 0)}
        for i in range(DAYS - 1, -1, -1)
    ]
    ops = Counter(c["op"] for c in changes)
    memories = sum(len(c["items"]) for c in memory.list_all())

    return {
        "files": len(files),
        "by_kind": {k: kinds.get(k, 0) for k in ("project", "skill", "source", "note")},
        "memories": memories,
        "memory_categories": kinds.get("memory", 0),
        "has_profile": kinds.get("profile", 0) > 0,
        "tags": kinds.get("tag", 0),
        "links": len(real_links),
        "words": sum(n.get("words", 0) for n in files),
        "sources": sources,
        "domains": [{"label": d, "value": n}
                    for d, n in Counter(s["domain"] for s in sources).most_common(8)],
        "activity": activity,
        "ops": [{"key": k, "value": ops.get(k, 0)} for k in history.OPS],
        "changes_total": len(changes),
        "recent": changes[:12],
    }


def chroma_count() -> int | None:
    try:
        return chroma_store.get_collection().count()
    except Exception:
        return None


def health(services_status: dict[str, str]) -> list[dict]:
    ollama_up = services_status.get("ollama") in ("running", "external")
    model = False
    if ollama_up:
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1) as r:
                model = any(m["name"].startswith("nomic-embed-text")
                            for m in json.load(r).get("models", []))
        except Exception:
            pass

    pw = HOME / "Library" / "Caches" / "ms-playwright"
    # "cmd" = comando literal (no se traduce); si no hay, el dashboard muestra el hint traducido
    return [
        {"key": "ollama", "ok": ollama_up, "cmd": None},
        {"key": "model", "ok": model, "cmd": "ollama pull nomic-embed-text"},
        {"key": "chroma", "ok": services_status.get("chroma") in ("running", "external"), "cmd": None},
        {"key": "playwright", "ok": pw.exists() and any(pw.glob("chromium*")),
         "cmd": "uv run playwright install chromium"},
        # al menos un agente conectado (detalle en la vista "Conectar agente")
        {"key": "agents", "ok": any(x["connected"] for x in agents.all_status()), "cmd": None},
    ]
