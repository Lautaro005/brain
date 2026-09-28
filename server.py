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

from brain_mcp import chroma_store, clients, connectors, entities, keyword_store, memory, vault  # noqa: E402
from brain_mcp.summarize import summarize  # noqa: E402
from brain_mcp.chunking import chunk_text  # noqa: E402
from brain_mcp.chroma_store import ChromaUnavailable  # noqa: E402
from brain_mcp.embeddings import OllamaUnavailable  # noqa: E402
from brain_mcp.scrape import ScrapeError, scrape  # noqa: E402

log = logging.getLogger("brain-mcp")
SOURCES_DIR = "knowledge/sources"
ABSTRACT_MIN_WORDS = 800  # fuentes más largas llevan un resumen corto en el frontmatter (abstract)

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
        "guardalo con add_memory; si reemplaza a un hecho anterior (se mudó, cambió de trabajo…), pasá "
        "el hecho viejo en supersede para que quede en el historial en vez de convivir con el nuevo. "
        "Usá list_skills antes de tareas complejas y search_knowledge para buscar en fuentes scrapeadas "
        "(es híbrida: sirve para temas y también para nombres, IDs o fechas exactas). Para convertir lo "
        "que sabés de un tema en un skill, usá distill_skill y guardá el resultado con write_file. "
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
def set_frontmatter(path: str, fields: dict) -> str:
    """Cambia campos del frontmatter (description, tags, related, name…) sin tocar el contenido.
    Ej: set_frontmatter("projects/brain.md", {"description": "App de memoria local"}). null borra el campo.
    Preferila a reescribir el archivo cuando solo cambia un metadato."""
    try:
        return f"OK: frontmatter de {vault.set_frontmatter(path, fields)} actualizado ({', '.join(map(str, fields))})"
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
def add_memory(fact: str, category: str = "General", supersede: str | None = None) -> str:
    """Guarda un hecho duradero sobre el usuario en memory/<category>.md (sin duplicar), con la
    fecha en que se guardó. Ej: add_memory("Prefiere respuestas cortas", "Preferencias").
    Si este hecho reemplaza a uno anterior (cambió de dirección, de trabajo, etc.), pasá el texto
    del hecho viejo en `supersede` — no se borra, queda marcado como superado con fecha en el
    mismo archivo (sección ## Historial)."""
    try:
        r = memory.add([fact], category, source="claude", supersede=supersede)
        msg = f"OK: guardado en {r['path']}" if r["added"] else f"Ya estaba en {r['path']}"
        if r.get("superseded"):
            msg += f"; '{r['superseded']['text']}' pasó al historial de {r['superseded']['path']}"
        if r.get("warning"):
            msg += f" (aviso: {r['warning']})"
        return msg
    except Exception as e:
        return _err(e)


@mcp.tool()
def memory_history(category: str = "General") -> list[str] | str:
    """Hechos superados de una categoría de memoria (los que se reemplazaron con add_memory(...,
    supersede=...)), con la fecha en que dejaron de valer y qué los reemplazó."""
    try:
        return memory.history(category) or f"Sin historial en la categoría {category}."
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
        extra = " Con resumen (abstract)." if r.get("abstract") else ""
        return f"{verb}: '{r['title']}' → {r['path']} ({r['chunks']} chunks indexados{via}).{extra}"
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
        old_ids = list(vault.parse(vault.read_file(md_path))[0].get("chroma_ids", []))
    except vault.VaultError:
        pass

    chunks = chunk_text(data["text"])
    ids = [f"{slug}-{i}" for i in range(len(chunks))]
    metas = [{"url": url, "source_md_path": md_path, "chunk_index": i} for i in range(len(chunks))]

    chroma_store.delete(old_ids)
    chroma_store.upsert(ids, chunks, metas)
    keyword_store.delete(old_ids)
    keyword_store.upsert(ids, chunks, metas)

    fields = dict(name=slug, description=data["title"], url=url, scraped_at=data["fetched_at"], chroma_ids=ids)
    # best-effort con el modelo de chat de Ollama: sin él la fuente se guarda igual, sin estos campos
    abstract = summarize(data["text"]) if len(data["text"].split()) > ABSTRACT_MIN_WORDS else None
    if abstract:
        fields["abstract"] = abstract
    ents = entities.extract(data["text"])
    if ents:
        fields["entities"] = ents
    post = frontmatter.Post(data["text"], **fields)
    vault.write_file(md_path, frontmatter.dumps(post) + "\n")
    return {"updated": bool(old_ids), "title": data["title"], "path": md_path,
            "chunks": len(chunks), "rendered_js": data["rendered_js"],
            "abstract": abstract, "entities": ents}


RRF_K = 60  # constante estándar de reciprocal rank fusion


def search_core(query: str, top_k: int = 5) -> tuple[list[dict], str | None, str | None]:
    """Búsqueda híbrida: semántica (Chroma) + por palabra (BM25 en SQLite), fusionadas con
    reciprocal rank fusion. Devuelve (hits, nota, parte caída: None | "semantic" | "keyword"). Si
    una de las dos partes no está disponible, usa la otra y lo dice en la nota; solo falla si
    fallan las dos (con el error de la semántica).

    Cada hit: text, url, source_md_path, chunk_index, score (similitud semántica 0–1, o None si
    solo lo encontró la búsqueda por palabra), match ("both" | "semantic" | "keyword")."""
    top_k = max(1, min(int(top_k), 50))
    vec, kw, vec_err, note, down = [], [], None, None, None
    try:
        vec = chroma_store.query(query, top_k=top_k * 3)
    except (OllamaUnavailable, ChromaUnavailable) as e:
        vec_err = e
    try:
        kw = keyword_store.query(query, top_k=top_k * 3)
    except Exception as e:
        log.warning("búsqueda por palabra falló: %s", e)
        if vec_err:
            raise vec_err
        note, down = "La búsqueda por palabra no estaba disponible; solo resultados semánticos.", "keyword"
    if vec_err:
        note, down = f"La búsqueda semántica no estaba disponible ({vec_err}); solo resultados por palabra.", "semantic"

    fused: dict[str, dict] = {}
    for kind, results in (("semantic", vec), ("keyword", kw)):
        for rank, r in enumerate(results):
            h = fused.setdefault(r["id"], {
                "text": r["text"], "url": r["metadata"].get("url"),
                "source_md_path": r["metadata"].get("source_md_path"),
                "chunk_index": r["metadata"].get("chunk_index"), "score": None, "rrf": 0.0, "match": kind,
            })
            h["rrf"] += 1 / (RRF_K + rank + 1)
            if kind == "semantic":
                h["score"] = round(1 - r["distance"], 4)
            elif h["match"] == "semantic":
                h["match"] = "both"
    hits = sorted(fused.values(), key=lambda h: -h["rrf"])[:top_k]
    for h in hits:
        h["rrf"] = round(h["rrf"], 5)
    return hits, note, down


@mcp.tool()
def search_knowledge(query: str, top_k: int = 5) -> list[dict] | str:
    """Búsqueda híbrida en las fuentes scrapeadas y lo capturado de conexiones: semántica (sentido)
    + por palabra exacta (nombres propios, IDs, fechas, números). Devuelve chunks con url,
    source_md_path, score (similitud semántica, None si solo matcheó por palabra) y match."""
    try:
        hits, note, _ = search_core(query, top_k)
        if not hits:
            return "No hay resultados (¿todavía no se guardó ninguna URL con save_url?)." + (f" Nota: {note}" if note else "")
        return hits + ([{"note": note}] if note else [])
    except (OllamaUnavailable, ChromaUnavailable) as e:
        return _err(e)
    except Exception as e:
        log.exception("search_knowledge falló")
        return _err(e)


def _chunk_ids(path: str, meta: dict, n: int) -> list[str]:
    """Ids de los chunks de un .md de knowledge/: los chroma_ids del frontmatter si coinciden en
    cantidad; si no (captura sin indexar, nota escrita a mano), los mismos que se habrían usado."""
    ids = [str(i) for i in (meta.get("chroma_ids") or [])]
    if len(ids) == n:
        return ids
    name = str(meta.get("name") or path.rsplit("/", 1)[-1][:-3])
    if path.startswith(SOURCES_DIR + "/"):
        return [f"{name}-{i}" for i in range(n)]
    for base in ("composio", "connections"):
        if path.startswith(f"knowledge/{base}/"):
            return [f"{base}-{name}-{i}" for i in range(n)]
    return [f"note-{path}-{i}" for i in range(n)]


def reindex_keyword_core() -> dict:
    """Reconstruye el índice por palabra desde los .md de knowledge/ (sin re-scrapear ni embeber)."""
    keyword_store.clear()
    files = chunks_total = 0
    for f in vault.list_files("knowledge"):
        meta, body = vault.parse(vault.read_file(f["path"]))
        chunks = chunk_text(body)
        if not chunks:
            continue
        url = meta.get("url") or (f"mcp://{meta['connection']}/{meta.get('tool', '')}" if meta.get("connection") else None)
        ids = _chunk_ids(f["path"], meta, len(chunks))
        keyword_store.upsert(ids, chunks, [{"url": url, "source_md_path": f["path"], "chunk_index": i}
                                           for i in range(len(chunks))])
        files += 1
        chunks_total += len(chunks)
    return {"files": files, "chunks": chunks_total}


@mcp.tool()
def reindex_keyword_search() -> str:
    """Reconstruye el índice de búsqueda por palabra desde los .md de knowledge/ (sources,
    connections, composio). Sirve para fuentes guardadas antes de que existiera la búsqueda
    híbrida; no re-scrapea ni vuelve a calcular embeddings."""
    try:
        r = reindex_keyword_core()
        return f"OK: {r['chunks']} chunks de {r['files']} archivos indexados para búsqueda por palabra."
    except Exception as e:
        return _err(e)


@mcp.tool()
def list_sources() -> list[dict] | str:
    """Lista todas las fuentes scrapeadas (url, fecha, path del .md, título y, si es larga, un
    resumen corto en 'abstract' para decidir si leerla entera con read_file)."""
    try:
        out = []
        for f in vault.list_files(SOURCES_DIR):
            meta = vault.parse(vault.read_file(f["path"]))[0]
            out.append(
                {
                    "url": meta.get("url"),
                    "scraped_at": str(meta.get("scraped_at", "")),
                    "path": f["path"],
                    "title": meta.get("description", ""),
                    "abstract": meta.get("abstract"),
                }
            )
        return out
    except Exception as e:
        return _err(e)


# ---------- skills a partir de la memoria ----------

def distill_core(topic: str) -> dict:
    topic = topic.strip()
    if len(memory._norm(topic)) < 2:
        raise ValueError("topic vacío")
    t = memory._norm(topic)
    words = [w for w in t.split() if len(w) > 2] or t.split()

    def matches(text: str) -> bool:
        n = f" {memory._norm(text)} "
        return f" {t} " in n or all(f" {w}" in n for w in words)

    chunks, note = [], None
    try:
        hits, note, _ = search_core(topic, 15)
        chunks = [{"text": h["text"], "source_md_path": h["source_md_path"], "url": h["url"]} for h in hits]
    except Exception as e:  # sin ninguna búsqueda, igual se junta memoria y fuentes
        note = f"Búsqueda no disponible: {e}"

    mem = []
    for c in memory.list_all():
        cat_hit = matches(c["category"])
        mem += [{"category": c["category"], "fact": i} for i in c["items"] if cat_hit or matches(i)]

    sources = []
    for f in vault.list_files("knowledge"):
        meta = vault.parse(vault.read_file(f["path"]))[0]
        text = " ".join([str(meta.get("description") or ""), str(meta.get("abstract") or ""),
                         " ".join(map(str, meta.get("entities") or []))])
        if matches(text):
            sources.append(f["path"])
    for c in chunks:
        if c["source_md_path"] and c["source_md_path"] not in sources:
            sources.append(c["source_md_path"])

    slug = memory.slugify(topic)
    skill_path = f"skills/{slug}.md"
    try:
        vault.read_file(skill_path)
        existing = skill_path
    except vault.VaultError:
        existing = None
    return {
        "topic": topic, "skill_path": skill_path, "existing_skill": existing,
        "chunks": chunks, "memory": mem, "sources": sources, "note": note,
        "instructions": (
            f"Sintetizá este material en un skill reutilizable (cuándo usarlo, pasos, criterios, "
            f"ejemplos) y guardalo con write_file('{skill_path}', ...) con frontmatter name: {slug} y "
            "una description de una línea. Citá las fuentes con [[nombre]] y sumá related: [...]. "
            + ("Ya existe un skill con ese nombre: leelo y actualizalo en vez de pisarlo. " if existing else "")
            + "Si el material no alcanza, decíselo al usuario en vez de inventar."
        ),
    }


@mcp.tool()
def distill_skill(topic: str) -> dict | str:
    """Junta todo lo que brain tiene sobre `topic` (fuentes, memoria, notas de conexiones) para
    que el agente redacte un skill a partir de esto y lo guarde con write_file en
    skills/<topic-slug>.md. Esta tool NO escribe el skill sola — devuelve el material."""
    try:
        return distill_core(topic)
    except Exception as e:
        return _err(e)


if __name__ == "__main__":
    vault.ensure_vault()
    mcp.run()
