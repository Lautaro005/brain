# brain-mcp — Spec de construcción

Servidor MCP local y personal: una base de conocimiento en Markdown (el "vault") más ChromaDB para búsqueda semántica, con una tool que scrapea URLs y las indexa automáticamente. Corre standalone en tu Mac y se conecta a cualquier cliente MCP (Claude Desktop, Claude Code, Cursor, etc.).

Proyecto independiente: se clona en cualquier carpeta (por ejemplo `~/brain-mcp/`).

---

## 1. Prerrequisitos

- **macOS**, Python **3.11+**
- **[uv](https://docs.astral.sh/uv/)** instalado (`brew install uv`) — maneja venv y dependencias sin pasos manuales
- **Ollama** instalado y corriendo, con el modelo de embeddings ya bajado:
  ```bash
  brew install ollama
  ollama serve &            # o abrir la app Ollama (queda corriendo en la barra de menú)
  ollama pull nomic-embed-text
  ```
  Verificar que responde antes de seguir:
  ```bash
  curl http://localhost:11434/api/embeddings -d '{"model":"nomic-embed-text","prompt":"test"}'
  ```
  Si esto no devuelve un vector, nada de lo demás va a andar — Ollama tiene que estar arriba *antes* de levantar el server MCP.
- **Server de Chroma** corriendo, también *antes* que cualquier cliente MCP. Todos los procesos de `server.py` (uno por cliente: Claude Code, Claude Desktop, Cursor…) se conectan a este único server por HTTP:
  ```bash
  ./chroma_server.sh        # = uv run chroma run --path ./data/chroma --host 127.0.0.1 --port 8055
  ```
  Verificar que responde:
  ```bash
  curl http://127.0.0.1:8055/api/v2/heartbeat
  ```
  En el uso diario no hace falta levantarlo a mano: `./brain.sh` abre el dashboard y prende Ollama y Chroma si no están corriendo (ver README). Si no está arriba, `save_url` y `search_knowledge` devuelven "El server de Chroma no está corriendo…". El puerto se cambia con la variable `BRAIN_CHROMA_PORT` (la leen tanto el script como `chroma_store.py`).
- **Chromium de Playwright** (una sola vez, para el scraping de sitios con JS):
  ```bash
  uv run playwright install chromium
  ```

---

## 2. Estructura final del proyecto

```
brain-mcp/
├── pyproject.toml
├── .gitignore
├── start.sh                     # ← "un comando fácil" para levantar el server
├── chroma_server.sh             # server HTTP de Chroma compartido (levantar antes que los clientes)
├── server.py                    # entrypoint MCP (stdio)
├── brain_mcp/
│   ├── __init__.py
│   ├── vault.py                 # read/write/append/str_replace/delete/list, todo sobre vault/
│   ├── scrape.py                 # url -> texto limpio (trafilatura, fallback a Playwright si hay mucho JS)
│   ├── embeddings.py             # llama a Ollama (nomic-embed-text) vía HTTP
│   ├── chroma_store.py           # upsert/query en ChromaDB
│   └── chunking.py               # split de texto largo en chunks
└── vault/                        # ESTA carpeta es la base de conocimiento real
    ├── BRAIN.md                  # contexto central, se lee siempre primero
    ├── projects/
    │   └── .gitkeep
    ├── skills/
    │   └── .gitkeep
    └── knowledge/
        └── sources/               # se auto-generan acá los .md de cada URL scrapeada

# NO versionado (en .gitignore): datos del usuario
vault/                            # se crea solo desde una plantilla (vault.ensure_vault)
data/                             # history.sqlite3 (historial), chroma/ (índice), .vault.lock
.venv/
__pycache__/
```

`vault/` no se versiona con la app: tiene datos personales. Se crea desde una plantilla la primera vez que arranca el server o el dashboard. Cada `write`/`append`/`str_replace`/`delete` guarda el contenido anterior y el nuevo en `data/history.sqlite3`, así si el modelo borra o pisa algo que no debía, hay rollback (`file_history` + `restore_file`).

---

## 3. Stack

| pieza | librería |
|---|---|
| protocolo MCP | `mcp` (SDK oficial de Python) |
| vector store | `chromadb` en modo server: `chroma run` local + `HttpClient` desde cada proceso |
| scraping | `trafilatura` (extracción de texto limpio de artículos/blogs/docs) + `playwright` (Chromium headless para sitios con JS) |
| embeddings | HTTP directo a Ollama (`nomic-embed-text`) — no hace falta librería extra, `requests` alcanza |
| frontmatter | `python-frontmatter` (parsear/escribir el YAML de cada `.md`) |
| historial | `sqlite3` de la stdlib (WAL), en `data/history.sqlite3` |

`pyproject.toml` mínimo:
```toml
[project]
name = "brain-mcp"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "mcp[cli]",
    "chromadb",
    "trafilatura",
    "python-frontmatter",
    "requests",
]
```

---

## 4. Cómo se arma (orden sugerido)

1. `uv init` en la carpeta, pegar el `pyproject.toml` de arriba, `uv sync`.
2. Crear `vault/BRAIN.md` con el índice inicial (ver formato abajo) y las subcarpetas vacías.
3. `brain_mcp/embeddings.py` — función `embed(texts: list[str], task: str) -> list[list[float]]` que le pega a `POST http://localhost:11434/api/embeddings` una vez por texto (Ollama no batchea nativamente en `/api/embeddings`), con reintentos simples y un error claro si Ollama no responde.
4. `brain_mcp/chunking.py` — split simple por párrafos, agrupando hasta ~500 tokens aprox. (contar palabras alcanza, no hace falta un tokenizer real) con un poco de overlap (~50 palabras) entre chunks consecutivos.
5. `brain_mcp/chroma_store.py` — `HttpClient(host="127.0.0.1", port=8055)` contra el server de `./chroma_server.sh` (que persiste en `./data/chroma`), colección `"sources"`, con una embedding function *custom* que llama a `embeddings.py` (Chroma te deja pasar cualquier callable que cumpla la interfaz `EmbeddingFunction`).
6. `brain_mcp/scrape.py` — `scrape(url, render_js=False) -> {title, text, fetched_at, rendered_js}` usando `trafilatura.fetch_url` + `trafilatura.extract`. Si no sale texto útil (menos de 30 palabras: los sitios con JS devuelven solo el esqueleto) o la descarga falla, renderizar con Playwright (Chromium headless, un solo navegador reusado) y pasarle el HTML renderizado a `trafilatura.extract`. `render_js=True` va directo a Playwright. Si tampoco sale texto, levantar un error legible en vez de guardar vacío.
7. `brain_mcp/vault.py` — funciones puras sobre archivos dentro de `vault/`:
   - `list_files(prefix=None)`, `read_file(path)`, `write_file(path, content)`, `append_file(path, content)`, `str_replace_file(path, old, new)`, `delete_file(path)`
   - todas resuelven el path relativo a `vault/` y **rechazan cualquier path que se salga de esa carpeta** (nada de `../..`)
   - cada operación de escritura/borrado registra el antes y el después en el historial (`history.record`), bajo un lock de archivo entre procesos
8. `server.py` — arma el server MCP (stdio) y expone las tools (sección 5). Acá es donde se conectan `vault.py`, `chroma_store.py` y `scrape.py`.
9. `start.sh`:
   ```bash
   #!/usr/bin/env bash
   cd "$(dirname "$0")"
   uv run python server.py
   ```
   `chmod +x start.sh`. Con esto, "entrar a la carpeta y levantar el server" es literal: `cd brain-mcp && ./start.sh`.
10. Probar el server *solo*, antes de conectarlo a ningún cliente, con el inspector oficial de MCP:
    ```bash
    npx @modelcontextprotocol/inspector uv run python server.py
    ```
    Esto abre una UI web donde podés llamar cada tool a mano y ver qué devuelve — mucho más fácil que debuggear a ciegas desde Claude Desktop.
11. Recién ahí, registrar el server en cada cliente (sección 6).

---

## 5. Tools MCP a exponer

| tool | input | qué hace |
|---|---|---|
| `list_vault` | `prefix?: str` | lista archivos del vault (path + description del frontmatter) |
| `read_file` | `path: str` | devuelve contenido completo de un `.md` |
| `write_file` | `path: str, content: str` | crea o **reemplaza entero** un archivo (queda en el historial) |
| `append_file` | `path: str, content: str` | agrega al final (queda en el historial) |
| `str_replace_file` | `path: str, old: str, new: str` | reemplazo puntual (falla si `old` no matchea exactamente una vez) |
| `delete_file` | `path: str` | borra el archivo (recuperable) |
| `file_history` | `path: str` | versiones del archivo (id, fecha, operación) |
| `restore_file` | `path: str, version_id: int` | vuelve el archivo a esa versión |
| `add_memory` | `fact: str, category?: str` | guarda un hecho sobre el usuario en `memory/<categoría>.md` |
| `list_skills` | — | como `list_vault` pero filtrado a `skills/` |
| `get_skill` | `name: str` | devuelve el contenido de un skill puntual |
| `save_url` | `url: str, render_js?: bool` | scrapea (con fallback automático a Playwright; `render_js=True` lo fuerza), chunkea, embebe en Chroma, y crea/actualiza el `.md` en `knowledge/sources/` |
| `search_knowledge` | `query: str, top_k?: int` | busca semánticamente en Chroma, devuelve chunk + link al `.md` de origen |
| `list_sources` | — | lista todo lo scrapeado (url, fecha, path del `.md`) |

---

## 6. Formato de los archivos

**`vault/BRAIN.md`** (se lee siempre primero, mantenerlo corto — es índice, no contenido):
```markdown
---
name: brain
description: Índice central del brain — dónde está cada cosa
---
# Brain — índice

- Perfil del usuario: `profile.md` (leelo primero para saber con quién hablás)
- Memoria sobre el usuario: `memory/` (usá `add_memory` para guardar hechos nuevos)
- Proyectos: `projects/`
- Skills: `skills/` (usá `list_skills` antes de tareas complejas)
- Conocimiento scrapeado: `knowledge/sources/` o `search_knowledge`
```

**Un archivo de skill** (`vault/skills/<nombre>.md`) — mismo formato que un SKILL.md de Claude:
```markdown
---
name: nombre-del-skill
description: Una frase de cuándo usar este skill (esto es lo que el modelo lee para decidir si aplica)
---
Instrucciones paso a paso en markdown normal...
```

**Un archivo de fuente scrapeada** (`vault/knowledge/sources/<slug>.md`), autogenerado por `save_url`:
```markdown
---
name: <slug>
description: <título o primeras líneas, para que aparezca bien en list_vault>
url: https://...
scraped_at: 2026-09-26T14:32:00Z
chroma_ids: ["<slug>-0", "<slug>-1", "..."]
---
<texto completo extraído>
```

Metadata de cada chunk en Chroma:
```json
{"url": "...", "source_md_path": "knowledge/sources/<slug>.md", "chunk_index": 0}
```

Así: desde un resultado de `search_knowledge` volvés al `.md` con `source_md_path`, y desde el `.md` volvés a sus chunks en Chroma con `chroma_ids`.

---

## 7. Cosas en las que fijarse (gotchas)

- **stdout es sagrado**: el transporte stdio de MCP usa stdout para el protocolo JSON-RPC. Ningún `print()` de debug puede ir a stdout — todo log va a `stderr` (o a un logger configurado con `stream=sys.stderr`). Un solo `print()` suelto rompe el server silenciosamente.
- **Ollama tiene que estar arriba antes de arrancar** — si `save_url` o `search_knowledge` no pueden conectar a `localhost:11434`, capturar el `ConnectionError` y devolver un mensaje de error claro ("Ollama no está corriendo"), no un stack trace.
- **Prefijos de nomic-embed-text**: este modelo fue entrenado con prefijos de instrucción — para indexar usá `"search_document: " + texto`, para buscar usá `"search_query: " + texto`. Sin esto la búsqueda semántica anda notablemente peor. No es opcional, es cómo el modelo espera el input.
- **Paths dentro del vault**: `write_file`/`delete_file`/`str_replace_file` reciben paths que decide el modelo — validar siempre que el path resuelto quede dentro de `vault/` (rechazar `..`, paths absolutos, symlinks raros). Es la única superficie de riesgo real acá.
- **Varios clientes MCP a la vez**: cada cliente (Claude Code, Claude Desktop, Cursor) levanta su propio proceso de `server.py`. Por eso Chroma corre como server aparte (`./chroma_server.sh`) y todos usan `HttpClient`: con `PersistentClient` cada proceso abriría el mismo SQLite y habría lock contention. Lo mismo pasa con las escrituras al vault: van serializadas con un lock de archivo (`data/.vault.lock`) que cubre leer, escribir y registrar en el historial, y el SQLite del historial está en modo WAL.
- **`save_url` sobre una URL ya guardada**: decidir que sea *update*, no duplicado — si ya existe un `.md` para ese slug, borrar sus `chroma_ids` viejos de Chroma antes de insertar los nuevos, y recién ahí sobreescribir el `.md`.
- **Trafilatura puede fallar** en sitios muy dependientes de JS: ahí entra Playwright solo. Ojo: la API sync de Playwright queda atada al thread que la creó, y las tools MCP corren en threads que cambian; por eso todo Playwright vive en un thread dedicado. Paywalls y logins siguen sin resolverse.
- **`.gitignore`**: `vault/`, `data/`, `.venv/`, `__pycache__/`. Nunca versionar `vault/` ni `data/`: son datos personales y archivos binarios de índice.
- **BRAIN.md corto**: si se llena de contenido en vez de índice, cada sesión arranca gastando contexto de más. Todo el contenido real va en `profile.md`, `memory/`, `projects/`, `skills/`, `knowledge/` — BRAIN.md solo apunta para allá.

---

## 8. Conectar a un cliente MCP (una vez que ya probaste con el inspector)

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "brain": {
      "command": "uv",
      "args": ["run", "--directory", "/ruta/absoluta/a/brain-mcp", "python", "server.py"]
    }
  }
}
```

**Claude Code**: `claude mcp add -s user brain -- uv run --directory /ruta/absoluta/a/brain-mcp python server.py`

**Cursor** (`.cursor/mcp.json` del proyecto o global): mismo `command`/`args` que Claude Desktop.

Usar siempre el **path absoluto** con `--directory` — el cliente no arranca el proceso parado en la carpeta del proyecto, así que un comando relativo rompe.

---

## 9. Qué queda afuera de esta v1 (a propósito)

- Embeddings vía API (OpenAI/Voyage) como alternativa a Ollama
- Búsqueda híbrida (keyword + semántica)
