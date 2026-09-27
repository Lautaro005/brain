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

from brain_mcp import chroma_store, clients, connectors, memory, vault  # noqa: E402
from brain_mcp.chunking import chunk_text  # noqa: E402
from brain_mcp.chroma_store import ChromaUnavailable  # noqa: E402
from brain_mcp.embeddings import OllamaUnavailable  # noqa: E402
from brain_mcp.scrape import ScrapeError, scrape  # noqa: E402

log = logging.getLogger("brain-mcp")
SOURCES_DIR = "knowledge/sources"

class BrainServer(MCPServer):
    """MCPServer + tools proxeadas de las conexiones (data/connections.json).

    list_tools y call_tool se leen en cada pedido, así prender o apagar una conexión desde el
    dashboard se refleja sin reiniciar el server (el cliente ve el cambio al volver a listar)."""

    @staticmethod
    def _note_client(ctx) -> None:
        # anota qué app está usando brain (clientInfo del handshake) para "Mis conexiones" del
        # dashboard: así aparecen también las apps que agregaron brain con un formulario
        try:
            info = ctx.session.client_params.client_info
            clients.record(info.name, info.version)
        except Exception:
            pass

    async def _handle_list_tools(self, ctx, params):
        self._note_client(ctx)
        return await super()._handle_list_tools(ctx, params)

    async def _handle_call_tool(self, ctx, params):
        self._note_client(ctx)
        return await super()._handle_call_tool(ctx, params)

    async def list_tools(self):
        from mcp.types import Tool as MCPTool

        own = await super().list_tools()
        names = {t.name for t in own}
        extra = [MCPTool(name=t["name"], description=t["description"], input_schema=t["input_schema"])
                 for t in connectors.proxied_tools() if t["name"] not in names]
        return own + extra

    async def call_tool(self, name, arguments, context=None):
        if connectors.SEP in name and self._tool_manager.get_tool(name) is None:
            from mcp.types import CallToolResult, TextContent

            t = connectors.resolve_tool(name)
            if t is None:
                return CallToolResult(content=[TextContent(type="text", text=f"Error: la tool {name} no existe o su conexión está desactivada.")], is_error=True)
            try:
                content, is_error = await connectors.call(t["conn_id"], t["tool"], arguments or {})
            except connectors.ConnectorError as e:
                return CallToolResult(content=[TextContent(type="text", text=f"Error: {e}")], is_error=True)
            except Exception as e:
                log.exception("Falló la tool proxeada %s", name)
                return CallToolResult(content=[TextContent(type="text", text=f"Error en {t['conn_id']}: {connectors._short(e)}")], is_error=True)
            return CallToolResult(content=content, is_error=is_error)
        return await super().call_tool(name, arguments, context)


mcp = BrainServer(
    "brain",
    instructions=(
        "Base de conocimiento personal del usuario. Leé primero BRAIN.md (read_file 'BRAIN.md') y "
        "profile.md + memory/ para saber con quién hablás. Cuando aprendas algo duradero sobre el usuario, "
        "guardalo con add_memory. "
        "Usá list_skills antes de tareas complejas y search_knowledge para buscar en fuentes scrapeadas. "
        "Las tools con prefijo (ej. gmail__..., composio__...) vienen de conexiones del usuario: su "
        "resultado se guarda solo en knowledge/ y después se puede buscar con search_knowledge. "
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


# ---------- conexiones ----------

@mcp.tool()
def list_connections() -> list[dict] | str:
    """Conexiones configuradas (otros servers MCP que brain re-expone): nombre, si están activas y sus tools."""
    try:
        return [{k: c[k] for k in ("id", "name", "kind", "enabled", "capture", "tools", "error")}
                for c in map(connectors.public, connectors.load())]
    except Exception as e:
        return _err(e)


@mcp.tool()
async def refresh_connectors() -> str:
    """Vuelve a descubrir las tools de todas las conexiones activas (después de conectar una app nueva)."""
    out = []
    for c in connectors.load():
        if not c.get("enabled", True):
            continue
        p = await connectors.discover(c["id"])
        out.append(f"{p['name']}: {len(p['tools'])} tools" + (f" (error: {p['error']})" if p["error"] else ""))
    return "\n".join(out) or "No hay conexiones activas."


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
