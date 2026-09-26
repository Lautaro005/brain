# brain-mcp

Servidor MCP local y personal: una base de conocimiento en Markdown (el **vault**), tu **perfil y memoria** (importable desde otros chatbots) y ChromaDB para búsqueda semántica, con una tool que scrapea URLs y las indexa sola. Corre en tu Mac y se conecta a cualquier cliente MCP (Claude Desktop/Cowork, Claude Code, Cursor). Incluye un dashboard web para manejarlo todo.

> **Antes de tocar nada:** leé [`CHANGES.md`](CHANGES.md) para saber qué se hizo, qué se decidió y por qué.
> **Después de cualquier cambio:** agregá una entrada **al final** de `CHANGES.md` (fecha, qué cambió, en qué archivos y por qué). Sin excepciones, aunque el cambio sea chico.

La especificación original está en [`BUILD.md`](BUILD.md). `CHANGES.md` registra dónde y por qué la implementación se apartó de ella.

---

## Qué hace

- **Vault** (`vault/`): Markdown con frontmatter. `BRAIN.md` es el índice; además hay `profile.md`, `memory/`, `projects/`, `skills/` y `knowledge/sources/`. Se crea solo desde una plantilla la primera vez que arranca el server o el dashboard.
- **Historial de versiones**: cada escritura o borrado queda guardado (contenido anterior y nuevo) en `data/history.sqlite3`. Cualquier archivo se puede volver a una versión anterior con `file_history` + `restore_file`. No usa git, así que el repo de la app no arrastra datos ni repos anidados.
- **Perfil y memoria**: en el dashboard cargás datos sobre vos e importás la memoria que otro chatbot (Claude, ChatGPT, Gemini…) tiene de vos. Se guarda en `memory/<categoría>.md` y aparece en el grafo conectada a tu perfil y a los proyectos que menciona. Claude suma memorias nuevas con `add_memory`.
- **Búsqueda semántica**: `save_url` scrapea con trafilatura (y con Playwright si el sitio depende de JS), parte el texto en chunks, genera embeddings con Ollama (`nomic-embed-text`) y los guarda en Chroma, que corre como server HTTP compartido para que varios clientes MCP puedan escribir a la vez. `search_knowledge` busca ahí.
- **Dashboard** (`./brain.sh`): switches para Chroma, Ollama y el Inspector MCP, métricas y gráficos, salud del sistema, perfil y memoria, grafo de conexiones, guardar y buscar URLs, y logs. En español o inglés, con tema claro, oscuro o el del sistema.

## Tools MCP

| tool | qué hace |
|---|---|
| `list_vault(prefix?)` | lista los `.md` con su description |
| `read_file(path)` | devuelve el contenido de un archivo |
| `write_file(path, content)` | crea o reemplaza un archivo |
| `append_file(path, content)` | agrega al final |
| `str_replace_file(path, old, new)` | reemplazo puntual; `old` tiene que aparecer exactamente una vez |
| `delete_file(path)` | borra (recuperable) |
| `file_history(path)` | versiones de un archivo (id, fecha, operación) |
| `restore_file(path, version_id)` | vuelve un archivo a una versión; la restauración también queda en el historial |
| `add_memory(fact, category?)` | guarda un hecho sobre el usuario en `memory/` (sin duplicar) |
| `list_skills()` / `get_skill(name)` | skills en `vault/skills/` |
| `save_url(url, render_js?)` | scrapea, indexa y crea o actualiza `knowledge/sources/<slug>.md`. Si el fetch normal saca menos de 30 palabras, prueba con Playwright; `render_js=True` lo fuerza |
| `search_knowledge(query, top_k?)` | búsqueda semántica |
| `list_sources()` | todo lo scrapeado |

## Instalación

Requisitos: macOS, [uv](https://docs.astral.sh/uv/) y [Ollama](https://ollama.com) (conviene la app, que queda en la barra de menú).

```bash
git clone <este repo> brain-mcp && cd brain-mcp
uv sync
uv run playwright install chromium     # navegador para scrapear sitios con JS
ollama pull nomic-embed-text           # modelo de embeddings
```

Registrar el server en los clientes (con el path absoluto de la carpeta):
- **Claude Code**: `claude mcp add -s user brain -- "$(command -v uv)" run --directory "$(pwd)" python server.py`
- **Claude Desktop / Cowork**: cerrá Claude con Cmd+Q y corré `./register-desktop.sh`. Hay que hacerlo con la app cerrada porque reescribe su config al salir.
- **Cursor**: en `.cursor/mcp.json`, el mismo `command` y `args` que usa `register-desktop.sh`.

## Uso

**El comando del día a día** (dejalo abierto mientras usás Claude):
```bash
./brain.sh                 # dashboard en http://127.0.0.1:8765; prende Ollama y Chroma si no están corriendo
```
- `--port 8766` usa otro puerto, `--no-autostart` no prende nada solo y `--no-browser` no abre el navegador.
- **Ctrl+C** cierra el dashboard y apaga lo que prendió él. Lo que ya corría por fuera (por ejemplo la app Ollama) aparece como "Externo", con el switch bloqueado, y no se toca.
- Primer paso recomendado: pestaña **Perfil** → cargá tus datos e importá tu memoria de otro chatbot.

Comandos sueltos, por si hacen falta sin el dashboard:
```bash
./chroma_server.sh         # solo Chroma en 127.0.0.1:8055 (otro puerto: BRAIN_CHROMA_PORT=8056 ./chroma_server.sh)
./start.sh                 # server MCP por stdio (normalmente lo lanza el cliente)
npx @modelcontextprotocol/inspector uv run python server.py   # Inspector (también tiene switch en el dashboard)
```

## Datos y privacidad

Todo lo tuyo vive en dos carpetas que el `.gitignore` excluye, así que nunca se suben al repo:
- `vault/`: tus notas, perfil y memoria.
- `data/`: `history.sqlite3` (historial de versiones), `chroma/` (índice vectorial) y el lock de escritura.

Para arrancar de cero: cerrá el dashboard y los clientes MCP, borrá `vault/` y `data/`, y volvé a abrir. El vault se recrea vacío desde la plantilla.

## Estructura

```
server.py              entrypoint MCP (stdio); define las tools
dashboard.py           server HTTP del dashboard (páginas, API, acciones)
brain.sh               lanzador del dashboard (el comando principal)
start.sh               lanzador del server MCP
chroma_server.sh       Chroma suelto, sin dashboard (127.0.0.1:8055)
register-desktop.sh    registra el server en Claude Desktop (correr con Claude cerrado)
brain_mcp/
  vault.py             operaciones sobre vault/ + validación de paths + plantilla + lock
  history.py           historial de versiones en SQLite (reemplaza a git)
  memory.py            perfil + memoria: parser de imports, categorías, relaciones
  embeddings.py        Ollama HTTP, con prefijos search_document/search_query
  chroma_store.py      ChromaDB HttpClient, colección "sources"
  chunking.py          chunks de ~500 palabras con overlap de 50
  scrape.py            trafilatura + fallback a Playwright (Chromium headless, thread dedicado)
  graph.py             arma nodos y links del vault
  graph.html           UI del grafo (d3 + marked + DOMPurify por CDN), embebida en el dashboard
  services.py          procesos que maneja el dashboard (Chroma, Ollama, Inspector)
  stats.py             estadísticas del vault + chequeos de salud
  dashboard.html       UI del dashboard (ES/EN, gráficos en SVG a mano, sin librería)
vault/                 (ignorado) la base de conocimiento, se crea sola
data/                  (ignorado) historial, Chroma, lock
```

## Convenciones del vault

- Cada `.md` lleva frontmatter con `name` y `description` (esta última es lo que muestran `list_vault` y el grafo).
- Para relacionar notas: `[[nombre]]` en el texto, y `tags: [a, b]` y `related: [nombre]` en el frontmatter.
- `BRAIN.md` tiene que ser un índice corto, no contenido. La info sobre el usuario va en `profile.md` y `memory/`.
- `memory/<categoría>.md`: una viñeta por hecho. El frontmatter `related` se calcula solo con los proyectos, skills o fuentes que las memorias mencionan por nombre.

## Gotchas

- **stdout es del protocolo**: nada de `print()` en el server; los logs van a stderr.
- **Paths**: `vault.py` rechaza `..`, paths absolutos, archivos ocultos y symlinks que salgan del vault. No aflojar esa validación.
- **SDK `mcp` 2.x**: se usa `from mcp.server.mcpserver import MCPServer`; `FastMCP` ya no existe.
- **Ollama o Chroma caídos**: `save_url` y `search_knowledge` devuelven un error legible. El resto de las tools funciona igual.
- **Puertos**: 8055 = Chroma, 8765 = dashboard, 6274/6275 = Inspector. Revisar con `lsof -nP -iTCP -sTCP:LISTEN` antes de elegir otro.
- **Playwright y threads**: la API sync de Playwright queda atada a su thread y las tools MCP corren en threads de anyio que cambian. Todo Playwright pasa por el thread dedicado de `scrape.py`; no llamarlo desde otro lado.
- **Concurrencia en el vault**: varios procesos (un `server.py` por cliente más el dashboard) escriben a la vez. Toda escritura pasa por un `flock` sobre `data/.vault.lock`, que cubre leer, escribir y registrar en el historial; SQLite va en modo WAL. No escribir en `vault/` salteando `vault.py`.
- **Seguridad del dashboard**: escucha solo en 127.0.0.1. Rechaza cualquier pedido cuyo `Host` no sea `127.0.0.1:<puerto>` o `localhost:<puerto>` (protección contra DNS rebinding), y los POST exigen el header `X-Brain: 1`, que fuerza un preflight CORS que el server nunca aprueba. Así ninguna página web puede prender procesos ni escribir en el vault. No sacar esas dos validaciones.
- **Idiomas**: los textos del dashboard y del grafo están en los diccionarios `I18N` de `dashboard.html` y `graph.html`. La API devuelve claves y códigos de error, no textos. Al agregar un texto, sumarlo en `es` y en `en`.
