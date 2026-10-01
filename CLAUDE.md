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
- **Acceso remoto por URL** (Conectar agente → Por URL): `server.py --http` en 127.0.0.1:8770 publicado con un túnel de Cloudflare (`cloudflared`), con token secreto en la URL. Para agentes en la nube (Claude.ai, ChatGPT, Manus en la web…).

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
| `move_file(src, dst)` | mueve o renombra (historial: `move` en origen y destino); no toca `BRAIN.md`, `profile.md`, `memory/` ni `knowledge/sources/` |
| `vault_overview()` | panorama para ordenar: sueltas, sin conexiones, sin descripción, variantes de tags, nombres parecidos (solo lee) |
| `add_memory(fact, category?, supersede?)` | guarda un hecho con fecha en `memory/` (sin duplicar). `supersede` pasa el hecho viejo a `## Historial` del mismo archivo |
| `memory_history(category?)` | hechos superados de una categoría |
| `list_skills()` / `get_skill(name)` | skills en `vault/skills/` |
| `distill_skill(topic)` | junta búsqueda + memoria + fuentes sobre un tema para que el agente redacte el skill (no escribe) |
| `save_url(url, render_js?)` | scrapea, indexa (Chroma + FTS5) y crea o actualiza `knowledge/sources/<slug>.md`, con `abstract` si pasa de 800 palabras y `entities`. Si el fetch normal saca menos de 30 palabras, prueba con Playwright; `render_js=True` lo fuerza |
| `search_knowledge(query, top_k?)` | búsqueda híbrida: semántica + BM25, fusionadas con RRF |
| `reindex_keyword_search()` | reconstruye el índice por palabra desde `knowledge/**.md` |
| `list_sources()` | todo lo scrapeado, con `abstract`, `scraped_at`, `checked_at` y `refresh_error` |
| `refresh_sources(path?)` | vuelve a bajar una fuente o todas; si falla conserva la copia anterior y anota `refresh_error` |
| `read_url(url, max_chars?)` | lee una página sin guardarla (para leer la doc de un server MCP) |
| `propose_connection(name, url? \| command?, …)` | propone una conexión; NO la crea: el usuario la aprueba en el chat o en Conexiones |

## Instalación

Para usuarios: macOS y Linux `curl -fsSL https://raw.githubusercontent.com/Lautaro005/brain/main/install.sh | bash`; Windows `irm https://raw.githubusercontent.com/Lautaro005/brain/main/install.ps1 | iex` o `brain-setup.exe` del release. Instala en `~/.brain` (`%USERPROFILE%\.brain`) y crea el comando `brain` (ver README.md).

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
uv run python server.py --http   # server MCP por HTTP en 127.0.0.1:8770 con token (lo prende el acceso remoto)
npx @modelcontextprotocol/inspector uv run python server.py   # Inspector (también tiene switch en el dashboard)
```

## Datos y privacidad

Todo lo tuyo vive en dos carpetas que el `.gitignore` excluye, así que nunca se suben al repo:
- `vault/`: tus notas, perfil y memoria.
- `data/`: `history.sqlite3` (historial de versiones), `chat_settings.json` (contexto del chat), `search.sqlite3` (índice por palabra), `chroma/` (índice vectorial), `remote.json` (acceso remoto: token de la URL y del túnel, 600), `bin/` (cloudflared, si lo bajó brain) y el lock de escritura.

Para arrancar de cero: cerrá el dashboard y los clientes MCP, borrá `vault/` y `data/`, y volvé a abrir. El vault se recrea vacío desde la plantilla.

## Estructura

```
server.py              entrypoint MCP (stdio); define las tools
dashboard.py           server HTTP del dashboard (páginas, API, acciones)
brain.sh               comando principal: dashboard + subcomandos update/path/uninstall/help
install.sh             instalador macOS/Linux (curl | bash): uv, clon en ~/.brain, deps, Chromium, Ollama, comando `brain`
install.ps1            instalador Windows (irm | iex): lo mismo con winget; crea brain.cmd en %USERPROFILE%\.local\bin
brain.ps1              el comando `brain` en Windows (equivalente de brain.sh)
installers/windows/    brain_setup.py → brain-setup.exe (PyInstaller en release.yml): trae install.ps1 y lo corre
scripts/               smoke_mcp.py (server.py por stdio y por --http con el token), check_site.py (links, estructura, versión y archivos generados de docs/) y build_site_files.py (llms.txt, sitemap.xml, robots.txt) para el CI
tests/                 pytest sin red/Ollama/Chroma (conftest aísla vault y data/ en tmp): `uv run pytest -q tests`
.github/workflows/     ci.yml (tests + instaladores en macOS/Linux/Windows + sitio, en cada commit a main y PR) y release.yml (.exe y archivos de Linux al publicar un release)
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
  organize.py          panorama del vault para /organize y vault_overview (solo lee)
  skills/organize.md   skill integrada de /organize (adaptada de file-organizer, MIT, con atribución en el frontmatter)
  ui.js                componentes compartidos del dashboard y el chat, servido en /ui.js: BrainUI.confirm y BrainUI.select
  updates.py           versión instalada (VERSION) + chequeo contra el último release de GitHub
  locks.py             lock de archivo entre procesos para macOS/Linux (flock) y Windows (msvcrt)
  remote.py            acceso remoto: token y ajustes (data/remote.json), auth ASGI del server --http, descarga de cloudflared, URL del túnel
  oauth.py             login OAuth de conexiones por URL (provider del SDK de MCP, tokens en data/oauth.json, callback /oauth/callback)
  backup.py            backup/restauración (.zip con vault, historial, ajustes y export de Chroma con embeddings) + reconstruir índices
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
docs/privacy/          política de privacidad (/privacy/), EN/ES: el chat de la home es de DokBot (www.dokbot.app)
docs/llms.txt, sitemap.xml, robots.txt   generados por scripts/build_site_files.py (no editarlos a mano)
docs/assets/           pages.css + pages.js compartidos por docs y changelog (la landing sigue autocontenida)
SECURITY.md            política de seguridad (GitHub la muestra en la pestaña Security)
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
- **Puertos**: 8055 = Chroma, 8765 = dashboard, 6274/6275 = Inspector, 8770 = MCP por URL del acceso remoto (`BRAIN_REMOTE_PORT`). Revisar con `lsof -nP -iTCP -sTCP:LISTEN` antes de elegir otro.
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
- **Preferencias del navegador** (localStorage, mismo origen para dashboard, grafo y chat): `brain-theme`, `brain-lang`, `brain-side-min` (sidebar plegado), `brain-graph-colors` (colores por tipo de nodo, los lee `graph.html` y escucha el evento `storage`), `brain-nav` (`{order, hidden}` del sidebar), `brain-chat-default` (modelo al abrir un chat nuevo; vacío = el último usado), `brain-chat-model`, `brain-chat-current`, `brain-update` (`{latest, url}` del último chequeo con versión nueva: mantiene el engranaje en verde hasta actualizar), `brain-chat-system` (system prompt del usuario, hasta 4000 caracteres; el chat lo manda en cada `/api/chat/send` como `system`), `brain-notify` (`{on, create, update, delete}` de las notificaciones del sistema). En sessionStorage, `brain-updating` (`{from, to}`) sobrevive a la recarga después de "Actualizar y reiniciar" para contar cómo salió.
- **Ajustes** es una vista más (`#ajustes`, `view-ajustes`), a la que lleva el engranaje del sidebar; no está en la lista de vistas reordenables y siempre se ve. Una vista oculta desde Ajustes sigue accesible por su `#hash`. Al agregar una vista nueva, sumarla a `VIEWS` y a `NAV_DEFAULT` (las que falten en `brain-nav` se agregan solas al final).
- **Versión**: el archivo `VERSION` (ej. `v0.02.5`) es la versión instalada; **subirlo en cada release** junto con el changelog. `brain_mcp/updates.py` lo compara con el último release de GitHub (`/releases/latest`), al abrir el dashboard, cada 3 horas mientras está abierto (`autoCheck` en `dashboard.html`, se saltea si hubo un chequeo hace menos de 10 min: `brain-update-checked` en localStorage) y con "Buscar actualizaciones". Es la única salida a internet que brain hace sola (aparte del túnel, si el usuario prende el acceso remoto), y solo pide `/releases/latest`. **Actualizar y reiniciar** (botón verde en Ajustes): `POST /api/update/apply` apaga el server y `main()` sale con código 75 (`RESTART_CODE`); `brain.sh`/`brain.ps1` (que exportan `BRAIN_LAUNCHER=1`) corren `git pull --ff-only` + `uv sync` y vuelven a abrir el dashboard con `--no-browser` en la misma terminal (si falla, `BRAIN_UPDATE_FAILED=1` y abre la versión instalada). Sin lanzador (`python dashboard.py`) devuelve `no_launcher` y el botón no aparece (`can_update` en `/api/version`). La página espera a que el server vuelva y se recarga. En `brain.sh` todo pasa dentro del `case` y siempre termina en `exit`: así bash no lee el script nuevo a mitad de camino después del `git pull`. El verde de "hay versión nueva" es la única excepción al monocromo además del rojo de errores.
- **Licencia**: MIT + Commons Clause, **las dos partes en `LICENSE`** (primero la condición, después el MIT). No es "open source" según la OSI: en README y landing se dice "source available" / "código a la vista". GitHub no la detecta como plantilla (muestra "View license"): es lo esperado, licensee solo reconoce licencias estándar y MIT + Commons Clause no lo es. La condición aplica a todas las versiones; no volver a agregar excepciones para versiones viejas. No volver a separar la condición en otro archivo para que GitHub diga "MIT" (se hizo en v0.02.5 y se revirtió en v0.02.8: el badge "MIT" hacía parecer que se podía vender).
- **Búsqueda híbrida**: cada llamada a `chroma_store.upsert/delete` va acompañada de `keyword_store.upsert/delete` con los mismos argumentos. `server.search_core()` devuelve `(hits, nota, parte caída)` y la usan la tool y el dashboard. El índice por palabra se puede reconstruir siempre desde el vault (`reindex_keyword_core`), así que `data/search.sqlite3` es descartable.
- **Memoria con fecha**: cada viñeta activa termina en `<!-- since:YYYY-MM-DD -->` y lo superado va a `## Historial` al final del mismo archivo. `memory._load()` devuelve `(meta, [{text, since}], historial)`; `list_all()` sigue devolviendo `items` como strings (solo activos).
- **Modelo de chat opcional** (`summarize.py`, `entities.py`): `BRAIN_SUMMARY_MODEL` (o llama3.2, o el primer modelo de chat instalado; `off` apaga), `BRAIN_LLM_TIMEOUT` (45 s). Nunca levantan excepción ni frenan una escritura; sin modelo, simplemente no hay `abstract`/`entities`. Las entidades se calculan en los call sites (`server.py`, `connectors.py`, `memory.py`), no en `vault.py`.
- **Reflect** solo sugiere: no agregar nada que escriba en el vault desde `reflect.py`.
- **Docs del sitio**: `docs/docs/index.html` tiene cada página como `<section class="page" id="…">` con un bloque `.l-en` y otro `.l-es`. `pages.js` renombra los ids a `page-…` al cargar (el hash nombra la página; si coincidiera con un id, el navegador bajaría hasta ella). Al agregar una página, sumar `data-title-en/es` y `data-group-en/es`. Cada release nuevo va arriba en `docs/changelog/index.html`, con la fecha en que se publica.
- **Composio tiene dos tipos de key**: `ck_…` (consumer) va directo a `https://connect.composio.dev/mcp` con el header `x-consumer-api-key` y no puede usar la API de desarrollador (`_composio()` la rechaza con `composio_consumer_key` en vez de dejar que responda 401). `ak_…` (proyecto) usa `backend.composio.dev/api/v3.1` con `x-api-key`: auth configs + `POST /mcp/servers`. `composio_configure()` detecta el tipo por el prefijo. Si falla una conexión por URL, `_http_probe()` repite el `initialize` a mano para mostrar el status HTTP real, que el cliente MCP no expone.
- **Nada de `confirm()`, `alert()` ni `<select>` nativos a la vista**: `brain_mcp/ui.js` (cargado en dashboard y chat) da `BrainUI.confirm(texto, {ok, cancel, danger})` → Promise y `BrainUI.select(select)`, que esconde el `<select>` y dibuja un dropdown propio; el `<select>` sigue siendo la fuente de verdad (`value`, `onchange`). Si el código cambia `.value` a mano, llamar `BrainUI.sync(select)`; opciones nuevas y `hidden` se siguen solos. Texto secundario de una opción: `data-sub`. En el dashboard hay un atajo `ask(texto, {ok, danger})`.
- **Modelo del chat fijo**: el selector se ve solo en un chat nuevo sin mensajes; después se muestra `#model-tag` con el modelo guardado en el meta del chat (`model`). Si ese modelo ya no está instalado, vuelve el selector.
- **Ventana de contexto**: `chat.context_window(model)` = regla de `data/chat_settings.json` (Ajustes → Contexto: `mode` "cap" con `cap` tokens para todos, o "max"; más `models` con un valor propio que nunca pasa el máximo del modelo) sobre el máximo que da `/api/show` (`model_info.*.context_length`, cacheado por modelo+digest en `_MAX`). `BRAIN_CHAT_CTX` (16384) es solo el tope por defecto sin ajustes. API: `GET/POST /api/chat/context`. Se guarda en `data/` y no en localStorage porque lo necesita el server. El evento NDJSON `usage` (`tokens`, `ctx`, `max`) alimenta el círculo; `tokens` = max(lo que reporta Ollama, caracteres/4). Se guarda en el meta del chat (`tokens`, `ctx`).
- **/compact**: no es un modo (no queda en `command`). `_compact()` resume la transcripción (desde el último resumen) sin tools y guarda `/compact` + un mensaje del asistente con `compact: true`. `_split_compact()` hace que los pedidos siguientes lleven ese resumen en el system prompt y solo los mensajes posteriores. Los mensajes viejos no se borran.
- **Comandos del chat**: `chat.COMMANDS` (backend: organize, reflect, compact, add-mcp) y `COMMANDS` + `T.cmds` en `chat.html` (lista y descripciones es/en). Además, cada skill del vault (`skills/**.md`, `chat.skills()`, `GET /api/chat/commands`) se usa con `/<nombre>`: `parse_command` devuelve `skill:<nombre>`, el meta del chat queda con `command = "skill:<nombre>"` y `_command_prompt` suma el contenido del skill (hasta 12.000 caracteres). El menú los muestra en dos grupos con título ("Comandos de brain" / "Tus skills") y una etiqueta *skill*. Un skill que se llame igual que un comando queda tapado. Un comando suma instrucciones al system prompt (`_command_prompt`) y queda en el meta del chat (`command`), así las respuestas siguientes siguen en ese modo. Al modelo le llega el pedido en palabras (`COMMANDS[cmd]`), no "/organize". `/new` es solo del frontend. Para agregar uno: sumarlo en los dos lados y en `CMD_TITLES`.
- **Servicios en el sidebar**: están en el popover del botón `#info-btn` (`#side-svcs`), posicionado con `position: fixed` por `placeInfo()` para que no lo recorte el sidebar (arriba en escritorio, a la derecha plegado, abajo en celular).
- **Multiplataforma (macOS, Linux, Windows)**: nada de `fcntl` ni `os.killpg` directos: los locks van por `locks.locked(path)` y el cierre de procesos por `services._terminate`. Las rutas de config de apps salen de `agents._app_data()` (macOS `~/Library/Application Support`, Windows `%APPDATA%`, Linux `~/.config`). Los ejecutables de servicios se resuelven con `shutil.which` (en Windows `npx` es `npx.cmd`). `install.sh` es para macOS/Linux y `install.ps1` para Windows; mantenerlos equivalentes. El CI (`.github/workflows/ci.yml`) corre `pytest`, el smoke del server y los dos instaladores en los tres sistemas: si tocás algo de plataforma, que siga verde ahí. Para probar el instalador sin Ollama/Chromium: `BRAIN_SKIP_OLLAMA=1 BRAIN_SKIP_CHROMIUM=1`.
- **OAuth de conexiones (`oauth.py`)**: una conexión `http` sin headers usa `OAuthClientProvider` del SDK (`connectors.uses_oauth`). Solo el dashboard pasa `port` y puede abrir el login (`oauth.start` → URL de autorización; el código vuelve por `GET /oauth/callback` y se valida por `state`); sin `port` (agentes, llamadas proxeadas) una conexión sin token levanta `NeedsAuth` y el caché queda con `error = "needs_auth"`, que la UI muestra como "Iniciar sesión". Tokens y registro del cliente en `data/oauth.json` (600, lock `data/.oauth.lock`); la redirect URI incluye el puerto del dashboard, así que si cambia el puerto se registra de nuevo. Para probar: un `MCPServer` con `auth_server_provider` + `AuthSettings` cuyo `authorize()` devuelve el redirect con el código (ver CHANGES.md v0.02.9).
- **Propuestas de conexión**: `propose_connection` (tool) y `/add-mcp` (chat) solo guardan en `data/connection_proposals.json` (600). Crear la conexión es siempre un click del usuario (`POST /api/connections/proposals/<id>/approve`). No agregar ningún camino para que un agente cree o apruebe una conexión: una stdio ejecuta comandos.
- **Backup (`backup.py`)**: el historial se copia y se restaura con la API de backup de SQLite (nunca pisar el archivo: otros procesos lo tienen abierto en WAL, y `with sqlite3.connect()` NO cierra la conexión: usar `closing`). Restaurar valida el zip (`_check`: rutas, tamaño, manifest), hace antes un `before-restore-*.zip` con secretos y reemplaza el vault bajo `vault._lock()`. Credenciales solo con `include_secrets`. La subida (`/api/backup/upload`) es el único POST que no es JSON ni tiene el límite de 5 MB.
- **Fuentes**: `save_url_core` indexa lo nuevo antes de borrar chunks sobrantes y reescribir el .md, así un fallo a mitad de camino no pierde la copia anterior. `refresh_source_core` compara el texto: sin cambios solo actualiza `checked_at`; con error anota `refresh_error` y no toca nada más.
- **Contexto fijo de la app** (`chat.APP_GUIDE`, es/en): va siempre en el system prompt (después de las reglas de las tools y antes del perfil y de las instrucciones del usuario), así el chat sabe cómo funciona brain. Ajustes lo muestra plegado y de solo lectura (`GET /api/chat/app_context`). **Si cambia algo visible de la app (vistas, comandos, agentes, ajustes), actualizarlo en los dos idiomas.**
- **Notificaciones del sistema** (Ajustes → Notificaciones): el dashboard consulta `GET /api/activity?after=<id>` (`history.since`) cada 5 s y usa la Notification API (127.0.0.1 es un contexto seguro). `after=-1` solo devuelve el último id, así al abrir no avisa de lo viejo. Tipos: `create` = cargas, `delete` = eliminaciones, el resto (`update`, `edit`, `append`, `restore`, `move`) = modificaciones; de un `move` se avisa solo el destino. Más de 3 cambios juntos van en una notificación. El `tag` evita duplicados entre pestañas.
- **Acceso remoto (`remote.py`)**: dos servicios del dashboard con `group = "remote"` (`RemoteMCP` = `server.py --http` con `BRAIN_REMOTE=1`, y `Tunnel` = `cloudflared`), que no se muestran en el Panel ni en el popover (`coreSvcs()`), sí en Logs; se manejan con `/api/remote/*` desde Conectar agente. `protect()` envuelve la app del SDK: sin el token (en `/<token>/mcp` o `Authorization: Bearer`) responde 404; lee el token de `data/remote.json` en cada pedido, así regenerarlo corta la URL vieja sin reiniciar. Se apaga la protección de DNS rebinding del SDK porque el `Host` llega con el dominio del túnel; la protección es el token + escuchar solo en 127.0.0.1. `access_log=False` para que el token no vaya a los logs. Solo lectura: `server.READ_ONLY_TOOLS` (sin proxeadas) cuando `BRAIN_REMOTE=1`; el dashboard y los agentes locales nunca quedan en solo lectura. Con `BRAIN_REMOTE=1`, `scrape.scrape()` rechaza direcciones no públicas (SSRF). El token del túnel con nombre va por env (`TUNNEL_TOKEN`), nunca en la línea de comando. `cloudflared` se baja a `data/bin/` del release oficial y se verifica con el SHA256 de las notas del release (API de GitHub o la página del release); sin checksum no se instala. **Ojo con macOS**: para los `.tgz`, Cloudflare publica (con el nombre del `.tgz`) el SHA256 del binario que viene adentro, no el del archivo; se acepta cualquiera de los dos y el binario se verifica antes de reemplazar el instalado. `remote.json` va al backup solo con credenciales. Si el usuario lo dejó prendido, `main()` lo vuelve a prender al arrancar (salvo `--no-autostart`). La URL quick (`*.trycloudflare.com`) sale de los logs de cloudflared; `Tunnel.start()` limpia los logs para no tomar la de la vez anterior.
- **Agentes sin archivo JSON/TOML**: `agents.CLIENTS` acepta `kind: "form"` (Manus Studio): agrega servers locales con su formulario "Run a command" y no tiene un archivo de config documentado. `set_connected` da `form_only`, la tarjeta (`formAgentCard`) muestra el comando y los argumentos de `manual_snippets()["form"]` para copiar, y `status()["detected"]` se prende cuando el handshake MCP anotó a Manus en `data/clients.json`. `kind: "dsh"` (DeepSeek Harness): la capa de parches del usuario `~/.dsh/cordis.patch.yml` (`$DSH_HOME`), que comparten la app de escritorio, el CLI `dsh` y la web, y que dsh recarga solo (no hace falta cerrar la app). brain agrega un bloque propio entre `# >>> brain…` y `# <<< brain` (un `- insert:` con la fila `brain-mcp` de `@deepseek-ai/dsh-mcp-client`, `transport: stdio`, strings citados con `json.dumps` para que las barras de Windows sean YAML válido) y al desconectar saca solo ese bloque: el archivo puede tener `!!js` y comentarios, así que nunca se reescribe con un parser (`_DshLoader` lee los tags propios como texto, solo para validar y detectar la fila). Si el archivo no es una lista YAML que dsh pueda leer, `bad_config`. dsh limpia del entorno del server solo las variables tipo `KEY/TOKEN/SECRET` y las `DSH_*`. OpenMausBot es `json` en `~/.openmausbot/config.json` con `process` (se escribe con la app cerrada); `win_exe` es la ruta del .exe bajo `%LOCALAPPDATA%` para volver a abrirla en Windows.
- **Contexto en el chat**: el chat recarga los modelos (con el `ctx` de Ajustes → Contexto) en cada chat nuevo y cuando el dashboard guarda esos ajustes (evento `storage` sobre `brain-ctx-changed`).
- **Footer y archivos del sitio**: home, docs, changelog y privacidad usan el mismo `<footer class="site-foot">` (licencia; columna Docs + Changelog; columna Política de privacidad + llms.txt + sitemap.xml; GitHub al final). Los estilos están repetidos a propósito en el `<style>` de `docs/index.html` (la landing es autocontenida) y en `docs/assets/pages.css`: si cambiás uno, cambiá el otro. Al agregar o cambiar páginas de las docs, o una versión nueva al changelog, correr `python3 scripts/build_site_files.py`; el CI (`check_site.py`) falla si `llms.txt`, `sitemap.xml` o `robots.txt` quedaron desactualizados. Si se agrega una página nueva al sitio, sumarla a `PAGES` en ese script y al footer de las cuatro páginas.
- **Chat de la landing**: es un widget de DokBot (`<script src="https://www.dokbot.app/widget.js">` al final de `docs/index.html`), no el chat de la app. La política de privacidad lo dice; si se cambia de proveedor, actualizarla.
