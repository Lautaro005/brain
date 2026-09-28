# brain — guía para agentes

Contexto técnico para cualquier agente (Claude Code u otro) que trabaje en este repo. La documentación para usuarios está en [`README.md`](README.md).

> **Antes de tocar nada:** leé [`CHANGES.md`](CHANGES.md) para saber qué se hizo, qué se decidió y por qué.
> **Después de cualquier cambio:** agregá una entrada **al final** de `CHANGES.md` (fecha, qué cambió, en qué archivos y por qué). Sin excepciones, aunque el cambio sea chico.

La especificación original está en [`BUILD.md`](BUILD.md). `CHANGES.md` registra dónde y por qué la implementación se apartó de ella.

brain es un servidor MCP local: un vault en Markdown, el perfil y la memoria del usuario, y ChromaDB para búsqueda semántica, más un dashboard web (`brain` / `./brain.sh`) que lo maneja todo.

---

## Qué hace

- **Vault** (`vault/`): Markdown con frontmatter. `BRAIN.md` es el índice; además hay `profile.md`, `memory/`, `projects/`, `skills/` y `knowledge/sources/`. Se crea solo desde una plantilla la primera vez que arranca el server o el dashboard.
- **Historial de versiones**: cada escritura o borrado queda guardado (contenido anterior y nuevo) en `data/history.sqlite3`. Cualquier archivo se puede volver a una versión anterior con `file_history` + `restore_file`. No usa git, así que el repo de la app no arrastra datos ni repos anidados.
- **Perfil y memoria**: en el dashboard cargás datos sobre vos e importás la memoria que otro chatbot (Claude, ChatGPT, Gemini…) tiene de vos. Se guarda en `memory/<categoría>.md` y aparece en el grafo conectada a tu perfil y a los proyectos que menciona. Claude suma memorias nuevas con `add_memory`.
- **Búsqueda semántica**: `save_url` scrapea con trafilatura (y con Playwright si el sitio depende de JS), parte el texto en chunks, genera embeddings con Ollama (`nomic-embed-text`) y los guarda en Chroma, que corre como server HTTP compartido para que varios clientes MCP puedan escribir a la vez. `search_knowledge` busca ahí.
- **Chat** (vista del dashboard y `/chat`): un modelo de chat de Ollama con el perfil, las memorias, `BRAIN.md` y fragmentos de chats anteriores como contexto, y las mismas tools MCP que los agentes para consultar y editar el vault. El historial vive en la colección `chats` de Chroma. Se puede sacar a una ventana flotante.
- **Dashboard** (`./brain.sh`): switches para Chroma, Ollama y el Inspector MCP, métricas y gráficos, salud del sistema, perfil y memoria, grafo de conexiones, guardar y buscar URLs, y logs. En español o inglés, con tema claro, oscuro o el del sistema.

## Tools MCP

| tool | qué hace |
|---|---|
| `list_vault(prefix?)` | lista los `.md` con su description |
| `read_file(path)` | devuelve el contenido de un archivo |
| `write_file(path, content)` | crea o reemplaza un archivo |
| `append_file(path, content)` | agrega al final |
| `str_replace_file(path, old, new)` | reemplazo puntual; `old` tiene que aparecer exactamente una vez |
| `set_frontmatter(path, fields)` | cambia campos del frontmatter sin tocar el contenido; `null` borra el campo |
| `delete_file(path)` | borra (recuperable) |
| `file_history(path)` | versiones de un archivo (id, fecha, operación) |
| `restore_file(path, version_id)` | vuelve un archivo a una versión; la restauración también queda en el historial |
| `add_memory(fact, category?, supersede?)` | guarda un hecho con fecha en `memory/` (sin duplicar). `supersede` pasa el hecho viejo a `## Historial` del mismo archivo |
| `memory_history(category?)` | hechos superados de una categoría |
| `list_skills()` / `get_skill(name)` | skills en `vault/skills/` |
| `distill_skill(topic)` | junta búsqueda + memoria + fuentes sobre un tema para que el agente redacte el skill (no escribe) |
| `save_url(url, render_js?)` | scrapea, indexa (Chroma + FTS5) y crea o actualiza `knowledge/sources/<slug>.md`, con `abstract` si pasa de 800 palabras y `entities`. Si el fetch normal saca menos de 30 palabras, prueba con Playwright; `render_js=True` lo fuerza |
| `search_knowledge(query, top_k?)` | búsqueda híbrida: semántica + BM25, fusionadas con RRF |
| `reindex_keyword_search()` | reconstruye el índice por palabra desde `knowledge/**.md` |
| `list_sources()` | todo lo scrapeado, con `abstract` |

## Instalación

Para usuarios: `curl -fsSL https://raw.githubusercontent.com/Lautaro005/brain/main/install.sh | bash`. Instala en `~/.brain` y crea el comando `brain` (ver README.md).

Para desarrollar: `git clone https://github.com/Lautaro005/brain && cd brain && uv sync && ./brain.sh`.

Los agentes se conectan desde el dashboard (**Conectar agente**), que usa `brain_mcp/agents.py`. `register-desktop.sh` queda como alternativa por terminal para Claude Desktop.

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
- `data/`: `history.sqlite3` (historial de versiones), `search.sqlite3` (índice por palabra), `chroma/` (índice vectorial) y el lock de escritura.

Para arrancar de cero: cerrá el dashboard y los clientes MCP, borrá `vault/` y `data/`, y volvé a abrir. El vault se recrea vacío desde la plantilla.

## Estructura

```
server.py              entrypoint MCP (stdio); define las tools
dashboard.py           server HTTP del dashboard (páginas, API, acciones)
brain.sh               comando principal: dashboard + subcomandos update/path/uninstall/help
install.sh             instalador de un comando (curl | bash): uv, clon en ~/.brain, deps, Chromium, Ollama, comando `brain`
start.sh               lanzador del server MCP
chroma_server.sh       Chroma suelto, sin dashboard (127.0.0.1:8055)
register-desktop.sh    registra el server en Claude Desktop (correr con Claude cerrado)
brain_mcp/
  vault.py             operaciones sobre vault/ + validación de paths + plantilla + lock
  history.py           historial de versiones en SQLite (reemplaza a git)
  memory.py            perfil + memoria: parser de imports, categorías, relaciones
  agents.py            conectar/desconectar brain en clientes MCP (Claude, ChatGPT/Codex, Cursor, VS Code…)
  clients.py           "Mis conexiones": clientes detectados por el handshake MCP (clientInfo) + anotados a mano
  chat.py              chat con Ollama (/api/chat con stream + tools), contexto de memoria e historial en Chroma
  chat.html            UI del chat (embebida en el dashboard y como ventana flotante en /chat?mode=pop)
  connectors.py        conexiones: brain como cliente MCP de otros servers (stdio/URL/Composio), proxy + auto-captura
  embeddings.py        Ollama HTTP, con prefijos search_document/search_query
  chroma_store.py      ChromaDB HttpClient, colección "sources"
  keyword_store.py     índice por palabra: SQLite FTS5 (BM25) en data/search.sqlite3, misma interfaz que chroma_store
  summarize.py         resumen corto (abstract) con el modelo de chat de Ollama, best-effort; también generate() y model()
  entities.py          entidades (nombres) con el modelo de chat, best-effort; van a `entities` del frontmatter
  reflect.py           revisa memoria y knowledge y SUGIERE arreglos (duplicados, contradicciones, sin entidades); nunca escribe
  chunking.py          chunks de ~500 palabras con overlap de 50
  scrape.py            trafilatura + fallback a Playwright (Chromium headless, thread dedicado)
  graph.py             arma nodos y links del vault
  graph.html           UI del grafo (d3 + marked + DOMPurify por CDN), embebida en el dashboard
  services.py          procesos que maneja el dashboard (Chroma, Ollama, Inspector)
  stats.py             estadísticas del vault + chequeos de salud
  dashboard.html       UI del dashboard (ES/EN, gráficos en SVG a mano, sin librería)
docs/index.html        landing page (GitHub Pages, main /docs): un solo HTML sin build, EN/ES, blanco y negro
docs/docs/index.html   documentación (/docs/): páginas por hash (#memory…), sidebar, "en esta página", anterior/siguiente
docs/changelog/        changelog (/changelog/): versión + fecha de publicación, EN/ES
docs/assets/           pages.css + pages.js compartidos por docs y changelog (la landing sigue autocontenida)
SECURITY.md            política de seguridad (GitHub la muestra en la pestaña Security)
COMMONS-CLAUSE.md      la condición de la licencia; LICENSE queda con el MIT puro para que GitHub lo detecte
docs/.nojekyll         Pages sirve el HTML tal cual (sin Jekyll)
PRODUCT.md / DESIGN.md contexto de producto y sistema visual de la landing (skill impeccable)
LICENSE                MIT + Commons Clause (se puede usar y modificar, no vender)
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
- **Agentes (`agents.py`)**: nunca reescribir la config de un cliente sin backup (`.bak-brain`) ni borrar entradas ajenas. Si el archivo no se puede parsear de forma segura (por ejemplo, JSONC de VS Code o un TOML con `brain` en una tabla inline), se devuelve `bad_config` y el usuario lo agrega a mano. Claude Desktop reescribe su config al salir: solo se escribe con la app cerrada. ChatGPT desktop y Codex CLI comparten `~/.codex/config.toml`. Para probar sin tocar las configs reales: `BRAIN_AGENTS_HOME=/tmp/fakehome BRAIN_AGENTS_APPS=/tmp/fakeapps`.
- **Instalador**: `install.sh` tiene que ser idempotente (correrlo de nuevo actualiza) y no puede pedir input, porque se ejecuta con `curl | bash`. Para probarlo sin GitHub: `BRAIN_REPO=file:///ruta/a/un/clon BRAIN_HOME=/tmp/x/.brain BRAIN_BIN=/tmp/x/bin bash install.sh`. Si se renombra el repo, cambiar la URL en `install.sh` (REPO) y en README.md.
- **Docs públicas vs internas**: `README.md` está en inglés, porque es la cara del repo en GitHub. `CLAUDE.md`, `CHANGES.md` y `BUILD.md` siguen en español. Si cambia algo visible para usuarios (tools, agentes, instalación, comandos), actualizar README.md y la landing (`docs/index.html`, en sus dos idiomas).
- **Landing**: textos en el diccionario `I18N` del final de `docs/index.html` (en/es); sin datos inventados (usuarios, métricas, testimonios): las demos están marcadas como ejemplo. La dirección visual (fichero Zettelkasten) está en `.impeccable/surfaces/docs-index-html.md` y DESIGN.md.
- **Conexiones (`connectors.py`)**: `server.py` usa `BrainServer`, una subclase de `MCPServer` que sobrescribe `list_tools`/`call_tool`. Las tools proxeadas se leen en cada pedido desde `data/connections.json` + `data/connections_tools.json`, así prender o apagar una conexión se refleja sin reiniciar. Nombre proxeado: `<prefijo>__<tool>` (`connectors.SEP`). Cada llamada abre y cierra una sesión con el server de origen. La auto-captura corre en un thread (`anyio.to_thread`) y nunca rompe la llamada. Los secretos van a `.env` (600, gitignored) como `$env:CLAVE`, y `connectors.public()` jamás los devuelve. Composio guarda los tokens OAuth en su nube: la UI lo aclara y no hay que prometer lo contrario.
- **Probar conexiones sin tocar datos reales**: apuntar `vault.VAULT`, `history.DATA/DB_PATH` y `connectors.DATA/CONFIG/CACHE/ENV_PATH` a una carpeta temporal y usar un server MCP de prueba (un `MCPServer` con un par de tools), por stdio o por `run_streamable_http_async(port=…)`.
- **Estilo del dashboard**: sigue el sistema de la landing (DESIGN.md) en versión de trabajo: monocromo, Archivo + Courier Prime, rojo solo para errores y acciones destructivas. La capa final de `<style>` en `dashboard.html` ("Sistema fichero") pisa los estilos base; el grafo distingue los tipos de nodo por relleno, contorno y trazo, no por color.
- **Frontmatter**: toda escritura de un `.md` pasa por `vault.check_frontmatter()`: si el YAML es inválido lo repara (pone entre comillas los valores con `:`) o rechaza la escritura con `VaultError`. Para leer, usar siempre `vault.parse(text)` (nunca `frontmatter.load` directo): no se rompe con YAML inválido. `memory.get_profile()` no asume cuántas líneas tiene el cuerpo.
- **Chat (`chat.py`)**: usa `server.mcp.list_tools()`/`call_tool()`, así ve exactamente las tools de los agentes, incluidas las de conexiones. `/api/chat/send` responde NDJSON en streaming (start · token · tool · tool_result · notice · done · error). Las tools se mandan con el esquema limpio (`_clean_schema`: sin `anyOf`/`title`, todo objeto con `properties`). Si Ollama rechaza las tools nativas (modelo sin soporte o template que no puede convertir, típico de GGUF de Hugging Face: "Unable to generate parser for this template"), pasa al **modo texto**: las tools van en el system prompt y el modelo las pide con `<tool_call>{…}</tool_call>`; `_TagFilter` los saca del stream y los resultados vuelven como mensaje. El modo se recuerda por modelo mientras corre el dashboard. En los dos modos también se aceptan `<tool_call>` o bloques ```json``` en el texto. Eventos extra: `rewrite` (texto limpio) y `tool`/`tool_result` con índice `i` y el resultado completo (hasta 1500 caracteres, también guardado en el historial). Historial en la colección `chats` de Chroma (`<id>:meta` + `<id>:00000`…, con embeddings); sin Chroma el chat funciona pero no se guarda. `chroma_store.get_collection(name)` y `chroma_store.call(fn, name)` sirven para cualquier colección.
- **Ventana flotante**: Document Picture-in-Picture pedido desde `window.top` (no anda dentro de un iframe) con un iframe a `/chat?mode=pop`; sin esa API, `window.open`. La PiP se cierra si se cierra la pestaña del dashboard.
- **Mis conexiones (`clients.py`)**: `BrainServer` sobrescribe `_handle_list_tools`/`_handle_call_tool` (métodos privados del SDK) para anotar `clientInfo` en `data/clients.json` (flock en `data/.clients.lock`, como mucho una escritura por minuto por cliente). Si el SDK cambia esos nombres, la detección se apaga sola sin romper nada (está en try/except).
- **Preferencias del navegador** (localStorage, mismo origen para dashboard, grafo y chat): `brain-theme`, `brain-lang`, `brain-side-min` (sidebar plegado), `brain-graph-colors` (colores por tipo de nodo, los lee `graph.html` y escucha el evento `storage`), `brain-chat-model`, `brain-chat-current`.
- **Licencia**: MIT + Commons Clause. No es "open source" según la OSI: en README y landing se dice "source available" / "código a la vista". `LICENSE` tiene **solo** el texto MIT (sin agregados: GitHub/licensee exige ~98% de coincidencia para detectarlo) y la condición está en `COMMONS-CLAUSE.md`. No renombrar ese archivo a `LICENSE-*`: licensee lo tomaría como una segunda licencia y mostraría "Other".
- **Búsqueda híbrida**: cada llamada a `chroma_store.upsert/delete` va acompañada de `keyword_store.upsert/delete` con los mismos argumentos. `server.search_core()` devuelve `(hits, nota, parte caída)` y la usan la tool y el dashboard. El índice por palabra se puede reconstruir siempre desde el vault (`reindex_keyword_core`), así que `data/search.sqlite3` es descartable.
- **Memoria con fecha**: cada viñeta activa termina en `<!-- since:YYYY-MM-DD -->` y lo superado va a `## Historial` al final del mismo archivo. `memory._load()` devuelve `(meta, [{text, since}], historial)`; `list_all()` sigue devolviendo `items` como strings (solo activos).
- **Modelo de chat opcional** (`summarize.py`, `entities.py`): `BRAIN_SUMMARY_MODEL` (o llama3.2, o el primer modelo de chat instalado; `off` apaga), `BRAIN_LLM_TIMEOUT` (45 s). Nunca levantan excepción ni frenan una escritura; sin modelo, simplemente no hay `abstract`/`entities`. Las entidades se calculan en los call sites (`server.py`, `connectors.py`, `memory.py`), no en `vault.py`.
- **Reflect** solo sugiere: no agregar nada que escriba en el vault desde `reflect.py`.
- **Docs del sitio**: `docs/docs/index.html` tiene cada página como `<section class="page" id="…">` con un bloque `.l-en` y otro `.l-es`. `pages.js` renombra los ids a `page-…` al cargar (el hash nombra la página; si coincidiera con un id, el navegador bajaría hasta ella). Al agregar una página, sumar `data-title-en/es` y `data-group-en/es`. Cada release nuevo va arriba en `docs/changelog/index.html`, con la fecha en que se publica.
- **Composio tiene dos tipos de key**: `ck_…` (consumer) va directo a `https://connect.composio.dev/mcp` con el header `x-consumer-api-key` y no puede usar la API de desarrollador (`_composio()` la rechaza con `composio_consumer_key` en vez de dejar que responda 401). `ak_…` (proyecto) usa `backend.composio.dev/api/v3.1` con `x-api-key`: auth configs + `POST /mcp/servers`. `composio_configure()` detecta el tipo por el prefijo. Si falla una conexión por URL, `_http_probe()` repite el `initialize` a mano para mostrar el status HTTP real, que el cliente MCP no expone.
