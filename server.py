"""brain-mcp: servidor MCP (stdio) sobre el vault Markdown + ChromaDB.

stdout está reservado para JSON-RPC: todo log va a stderr.
"""
import hashlib
import logging
import re
import sys
from urllib.parse import urlparse

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # si no, una línea por cada request a Chroma

import frontmatter  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402

from brain_mcp import chroma_store, memory, vault  # noqa: E402
from brain_mcp.chunking import chunk_text  # noqa: E402
from brain_mcp.chroma_store import ChromaUnavailable  # noqa: E402
from brain_mcp.embeddings import OllamaUnavailable  # noqa: E402
from brain_mcp.scrape import ScrapeError, scrape  # noqa: E402

log = logging.getLogger("brain-mcp")
SOURCES_DIR = "knowledge/sources"

mcp = MCPServer(
    "brain",
    instructions=(
        "Base de conocimiento personal del usuario. Leé primero BRAIN.md (read_file 'BRAIN.md') y "
        "profile.md + memory/ para saber con quién hablás. Cuando aprendas algo duradero sobre el usuario, "
        "guardalo con add_memory. "
        "Usá list_skills antes de tareas complejas y search_knowledge para buscar en fuentes scrapeadas. "
        "Al escribir notas, conectalas: usá [[nombre]] para linkear otros archivos del vault, y en el "
        "frontmatter 'tags: [a, b]' y 'related: [nombre]' — así aparecen relacionadas en la vista de grafo."
    ),
)


def _err(e: Exception) -> str:
    return f"Error: {e}"


def _slug(url: str) -> str:
    u = urlparse(url)
    base = re.sub(r"[^a-z0-9]+", "-", f"{u.netloc}{u.path}".lower()).strip("-")[:60].strip("-")
    return f"{base or 'source'}-{hashlib.sha1(url.encode()).hexdigest()[:6]}"


# ---------- vault ----------

@mcp.tool()
def list_vault(prefix: str | None = None) -> list[dict] | str:
    """Lista archivos .md del vault (path + description del frontmatter). prefix opcional, ej. 'projects'."""
    try:
        return vault.list_files(prefix)
    except Exception as e:
        return _err(e)


@mcp.tool()
def read_file(path: str) -> str:
    """Devuelve el contenido completo de un archivo del vault (path relativo, ej. 'BRAIN.md')."""
    try:
        return vault.read_file(path)
    except Exception as e:
        return _err(e)


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Crea o REEMPLAZA ENTERO un archivo del vault. Queda en el historial (file_history)."""
    try:
        return f"OK: escrito {vault.write_file(path, content)}"
    except Exception as e:
        return _err(e)


@mcp.tool()
def append_file(path: str, content: str) -> str:
    """Agrega contenido al final de un archivo del vault (lo crea si no existe). Queda en el historial."""
    try:
        return f"OK: agregado a {vault.append_file(path, content)}"
    except Exception as e:
        return _err(e)


@mcp.tool()
def str_replace_file(path: str, old: str, new: str) -> str:
    """Reemplazo puntual: 'old' debe aparecer exactamente una vez en el archivo. Queda en el historial."""
    try:
        return f"OK: editado {vault.str_replace_file(path, old, new)}"
    except Exception as e:
        return _err(e)


@mcp.tool()
def delete_file(path: str) -> str:
    """Borra un archivo del vault. Recuperable con file_history + restore_file."""
    try:
        return f"OK: borrado {vault.delete_file(path)}"
    except Exception as e:
        return _err(e)


@mcp.tool()
def file_history(path: str) -> list[dict] | str:
    """Versiones de un archivo del vault, la más nueva primero (id, fecha, operación). El id sirve
    para restore_file."""
    try:
        return vault.file_history(path) or f"Sin historial para {path}."
    except Exception as e:
        return _err(e)


@mcp.tool()
def restore_file(path: str, version_id: int) -> str:
    """Vuelve un archivo a como quedó en la versión version_id (ver file_history). La restauración
    también queda en el historial, así que se puede deshacer."""
    try:
        return f"OK: {vault.restore_version(path, version_id)} restaurado a la versión {version_id}"
    except Exception as e:
        return _err(e)


# ---------- memoria del usuario ----------

@mcp.tool()
def add_memory(fact: str, category: str = "General") -> str:
    """Guarda un hecho duradero sobre el usuario en memory/<category>.md (sin duplicar).
    Ej: add_memory("Prefiere respuestas cortas", "Preferencias")."""
    try:
        r = memory.add([fact], category, source="claude")
        return f"OK: guardado en {r['path']}" if r["added"] else f"Ya estaba en {r['path']}"
    except Exception as e:
        return _err(e)


# ---------- skills ----------

@mcp.tool()
def list_skills() -> list[dict] | str:
    """Lista los skills disponibles (skills/*.md) con su description, para decidir cuál aplica."""
    try:
        return vault.list_files("skills")
    except Exception as e:
        return _err(e)


@mcp.tool()
def get_skill(name: str) -> str:
    """Devuelve el contenido de un skill por nombre (ej. 'mi-skill' o 'mi-skill.md')."""
    try:
        fname = name if name.endswith(".md") else f"{name}.md"
        return vault.read_file(f"skills/{fname}")
    except Exception as e:
        return _err(e)


# ---------- knowledge ----------

@mcp.tool()
def save_url(url: str, render_js: bool = False) -> str:
    """Scrapea una URL, la chunkea, la indexa en Chroma y crea/actualiza su .md en knowledge/sources/.
    Si la URL ya estaba guardada, la actualiza (no duplica). Si el fetch normal no saca texto útil
    (sitio con mucho JS), renderiza solo con Playwright; render_js=True fuerza ese camino directo."""
    try:
        r = save_url_core(url, render_js)
        verb = "Actualizado" if r["updated"] else "Guardado"
        via = ", renderizado con Playwright" if r["rendered_js"] else ""
        return f"{verb}: '{r['title']}' → {r['path']} ({r['chunks']} chunks indexados{via})"
    except (ScrapeError, OllamaUnavailable, ChromaUnavailable) as e:
        return _err(e)
    except Exception as e:
        log.exception("save_url falló")
        return _err(e)


def save_url_core(url: str, render_js: bool = False) -> dict:
    """Lo que hace save_url, devolviendo datos (lo usa también el dashboard). Levanta excepciones."""
    data = scrape(url, render_js=render_js)
    slug = _slug(url)
    md_path = f"{SOURCES_DIR}/{slug}.md"

    # si ya existe, borrar chunks viejos antes de insertar
    old_ids: list[str] = []
    try:
        old_ids = list(frontmatter.loads(vault.read_file(md_path)).metadata.get("chroma_ids", []))
    except vault.VaultError:
        pass

    chunks = chunk_text(data["text"])
    ids = [f"{slug}-{i}" for i in range(len(chunks))]
    metas = [{"url": url, "source_md_path": md_path, "chunk_index": i} for i in range(len(chunks))]

    chroma_store.delete(old_ids)
    chroma_store.upsert(ids, chunks, metas)

    post = frontmatter.Post(
        data["text"],
        name=slug,
        description=data["title"],
        url=url,
        scraped_at=data["fetched_at"],
        chroma_ids=ids,
    )
    vault.write_file(md_path, frontmatter.dumps(post) + "\n")
    return {"updated": bool(old_ids), "title": data["title"], "path": md_path,
            "chunks": len(chunks), "rendered_js": data["rendered_js"]}


@mcp.tool()
def search_knowledge(query: str, top_k: int = 5) -> list[dict] | str:
    """Búsqueda semántica en las fuentes scrapeadas. Devuelve chunks con url y source_md_path."""
    try:
        results = chroma_store.query(query, top_k=max(1, min(top_k, 50)))
        if not results:
            return "No hay resultados (¿todavía no se guardó ninguna URL con save_url?)."
        return [
            {
                "text": r["text"],
                "url": r["metadata"].get("url"),
                "source_md_path": r["metadata"].get("source_md_path"),
                "chunk_index": r["metadata"].get("chunk_index"),
                "score": round(1 - r["distance"], 4),
            }
            for r in results
        ]
    except (OllamaUnavailable, ChromaUnavailable) as e:
        return _err(e)
    except Exception as e:
        log.exception("search_knowledge falló")
        return _err(e)


@mcp.tool()
def list_sources() -> list[dict] | str:
    """Lista todas las fuentes scrapeadas (url, fecha, path del .md, título)."""
    try:
        out = []
        for f in vault.list_files(SOURCES_DIR):
            meta = frontmatter.loads(vault.read_file(f["path"])).metadata
            out.append(
                {
                    "url": meta.get("url"),
                    "scraped_at": str(meta.get("scraped_at", "")),
                    "path": f["path"],
                    "title": meta.get("description", ""),
                }
            )
        return out
    except Exception as e:
        return _err(e)


if __name__ == "__main__":
    vault.ensure_vault()
    mcp.run()
