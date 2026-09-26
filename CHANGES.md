# CHANGES

Registro de todos los cambios del proyecto, del más viejo al más nuevo. **Cada cambio nuevo se agrega al final**, con fecha, qué cambió, en qué archivos y por qué.

---

## 2026-09-26 — Construcción inicial según BUILD.md

**Qué se hizo**
- Scaffold completo: `pyproject.toml`, `.gitignore`, `start.sh`, `server.py` y el paquete `brain_mcp/` (`vault.py`, `embeddings.py`, `chroma_store.py`, `chunking.py`, `scrape.py`).
- Vault inicial: `vault/BRAIN.md` más `projects/`, `skills/` y `knowledge/sources/` con `.gitkeep`. `vault/` se inicializó como repo git propio.
- Las 11 tools de la sección 5 de BUILD.md.

**En qué se apartó de BUILD.md y por qué**
- **`mcp` 2.x**: la versión que se instaló (2.2.0) renombró `FastMCP` a `MCPServer`. `server.py` usa `from mcp.server.mcpserver import MCPServer`, y `pyproject.toml` fija `mcp[cli]>=2`.
- **Python 3.12** fijado en `.python-version`, porque el del sistema es 3.9. uv lo instala solo.
- **`[tool.uv] package = false`** en `pyproject.toml`: el proyecto no se instala como paquete, solo se usa su venv.
- **Git solo en `vault/`**, no en la raíz, para no tener un repo anidado dentro de otro.
- **Slug de las fuentes** = dominio y path "slugificados" (máximo 60 caracteres) más los primeros 6 caracteres del sha1 de la URL. Es determinístico, así que guardar de nuevo la misma URL actualiza el mismo `.md`. Antes de insertar se borran de Chroma los `chroma_ids` viejos.
- **Colección Chroma con distancia coseno** (`hnsw:space: cosine`). `search_knowledge` devuelve `score = 1 - distance`.
- **Seguridad de paths** en `vault.py`: rechaza paths vacíos, absolutos, con `..`, que apunten a `.git/`, o que después de resolver symlinks queden fuera de `vault/`.
- **Errores** devueltos como texto `"Error: ..."` en vez de excepciones. Si Ollama está caído, el mensaje es "Ollama no está corriendo…".

**Verificado**
- Cliente MCP real por stdio contra `./start.sh`: aparecen las 11 tools y responden. stdout queda limpio.
- Vault: write, append, replace, delete y sus commits funcionan. Se probó el rechazo de `../`, `/etc/passwd`, `.git/config` y de un symlink hacia `/tmp`.
- `save_url` con la página de Wikipedia sobre estoicismo: 16 chunks. Al guardarla de nuevo se actualizó y siguió en 16, sin duplicar. `search_knowledge` devolvió resultados relevantes. Después esa fuente de prueba se borró del vault y de Chroma.

## 2026-09-26 — Registro en clientes MCP

- **Claude Code**: `claude mcp add -s user brain -- <path absoluto de uv> run --directory <path del proyecto> python server.py`. Se usó scope `user` para que esté en todos los proyectos, y `uv` con path absoluto porque las apps no heredan el PATH del shell.
- **Claude Desktop**: se agregó `mcpServers.brain` a `claude_desktop_config.json`, con backup en `claude_desktop_config.json.bak-brain-mcp`.
- **Problema encontrado**: Claude Desktop reescribe ese archivo al cerrarse y borró la entrada. Solución: `register-desktop.sh`, que se corre con Claude cerrado. El script verifica que Claude no esté abierto, hace backup, agrega la entrada y vuelve a abrir la app. El usuario lo corrió y funcionó.
- `.DS_Store` agregado a `.gitignore` y a `vault/.gitignore`, para que el `git add -A` del vault no los commitee.

## 2026-09-26 — Vista de grafo de conexiones

**Archivos nuevos**: `brain_mcp/graph.py`, `brain_mcp/graph.html`, `graph_server.py`, `graph.sh`.

- `./graph.sh [puerto]` levanta un server HTTP de stdlib en `127.0.0.1:8765` y abre el navegador. Endpoints: `/`, `/api/graph` (nodos y links) y `/api/file?path=` (lee pasando por `vault.read_file`, con la misma validación de paths).
- **Relaciones que detecta `graph.py`**: `[[wikilinks]]`, links Markdown a `.md`, paths entre backticks que existen en el vault, `related:` y `tags:` del frontmatter, y la jerarquía de carpetas. Deja una sola línea por par de nodos, con prioridad link > related > mention > tag > folder.
- **UI** (d3 v7, marked y DOMPurify desde jsdelivr): filtros por tipo, búsqueda, panel lateral con conexiones y contenido renderizado, `[[links]]` clickeables, botón "Centrar", modo oscuro automático, y actualización cada 3 segundos si cambia el vault. El contenido se sanitiza con DOMPurify: se probó que un `<img onerror>` scrapeado queda neutralizado.
- El layout inicial se calcula de forma sincrónica (300 ticks) y se ajusta a la vista. Antes quedaba descentrado cuando la pestaña estaba en segundo plano, porque el navegador pausa la animación.
- Si el puerto está ocupado, el server sale con un mensaje claro. El 8799 lo usaba otra app en la Mac donde se desarrolló.
- **`server.py`**: se agregó a las `instructions` del server que el modelo use `[[nombre]]`, `tags` y `related` al escribir notas, para que el grafo muestre relaciones.

## 2026-09-26 — Documentación

- Se crearon `README.md` (resumen del proyecto, con instrucción de leer `CHANGES.md` y registrar ahí cada cambio) y este `CHANGES.md`.

## 2026-09-26 — Scraping con Playwright + Chroma en modo server + lock de git en el vault

**Qué se hizo**
- **Fallback a Playwright** (`brain_mcp/scrape.py`): si trafilatura no saca texto útil, la URL se renderiza en Chromium headless. Se usa `domcontentloaded`, después se espera `networkidle` hasta 15 s (si no llega, sigue igual), se toma `page.content()` y ese HTML renderizado se le pasa a `trafilatura.extract`. Si tampoco sale texto, se levanta `ScrapeError`. El resultado de `scrape()` suma el campo `rendered_js`.
- **Parámetro `render_js: bool = False`** en `scrape()` y en la tool `save_url`, para ir directo a Playwright. Cuando se usó Playwright, `save_url` lo indica en su respuesta ("…, renderizado con Playwright").
- **Un solo Chromium reusado**, lazy, que vive en un thread daemon dedicado con cola de trabajos. Se cierra prolijo con `atexit`: probado, no quedan procesos `headless_shell` huérfanos.
- **`playwright`** agregado a `pyproject.toml`. Instalación única: `uv run playwright install chromium`.
- **Chroma en modo server**: `chroma_server.sh` corre `uv run chroma run --path ./chroma_db --host 127.0.0.1 --port 8055`. `chroma_store.py` pasó a `chromadb.HttpClient(host="127.0.0.1", port=8055)` y mantiene la misma API pública (`get_collection`, `upsert`, `delete`, `query`).
- **`ChromaUnavailable`**, con el mismo patrón que `OllamaUnavailable`: "El server de Chroma no está corriendo (./chroma_server.sh) en 127.0.0.1:8055." `server.py` lo captura en `save_url` y `search_knowledge`. Si el server se cae a mitad de sesión, se resetea la colección cacheada y se reconecta en la próxima llamada.
- **Lock de git en el vault** (`brain_mcp/vault.py`): `_git_commit(message, rel_path)` corre dentro de un `fcntl.flock` sobre `vault/.brain-git.lock` (ignorado en `vault/.gitignore`) y commitea solo `rel_path` (`git add -A -- path` + `git commit -- path`).
- **Documentación**: BUILD.md (prerrequisitos, estructura, stack, pasos 5 y 6, tabla de tools y gotchas; se sacaron los dos puntos de la sección 9) y README.md.

**En qué se apartó del pedido / de BUILD.md y por qué**
- **El fallback se activa con menos de 30 palabras (`MIN_WORDS`), no solo con texto vacío.** Se probaron 6 sitios con JS (quotes.toscrape.com/js, excalidraw, tldraw, entre otros) y trafilatura nunca devolvió `None`: devuelve el esqueleto ("You need to enable JavaScript…", menú, footer) con 5 a 16 palabras. Con la regla literal, el fallback no se habría activado nunca. Si Playwright saca menos texto que trafilatura, se queda el de trafilatura.
- **El fallback también se activa si falla la descarga** (`fetch_url` devuelve `None`, por ejemplo un 403 anti-bot). Excalidraw le devuelve 403 a trafilatura y con Chromium real entra bien.
- **Si Playwright falla pero trafilatura ya había sacado algo de texto**, se usa ese texto (con warning en el log) en vez de devolver error. Con `render_js=True` el error de Playwright sí se propaga.
- **Thread daemon propio y no `ThreadPoolExecutor`**: la API sync de Playwright queda atada al thread que la creó, y MCPServer 2.x corre las tools sync con `anyio.to_thread.run_sync`, cada vez en un thread distinto. Hace falta un thread fijo. No se usa `ThreadPoolExecutor` porque `concurrent.futures` apaga sus executors antes de los handlers de `atexit` y no se podría cerrar Chromium (pasó en la primera versión: "cannot schedule new futures after shutdown").
- **Puerto 8055**: `lsof` mostró libres el 8001 y el 8055. Se eligió 8055 porque el rango 8000/8001 es el default de muchos servidores de desarrollo. Se puede cambiar con `BRAIN_CHROMA_PORT`, que leen tanto `chroma_server.sh` como `chroma_store.py`. El server escucha solo en `127.0.0.1`.
- **Embeddings calculados antes de la llamada HTTP** en `upsert()`: se pasan con `embeddings=` en vez de dejar que Chroma llame a la embedding function. Así, si Ollama está caído, sigue saliendo `OllamaUnavailable` y no se confunde con un error de Chroma.
- **Lock de git (no estaba pedido)**: la prueba de concurrencia mostró que el problema de dos procesos también existía en el git del vault. Stress test con 2 procesos × 8 escrituras simultáneas: **1 commit de 16** (todos los archivos en un commit con el mensaje de uno solo) más varios errores de `index.lock`. El rollback que promete BUILD.md quedaba roto. Con el lock: 16 commits de 16, cada uno con su archivo.

**Verificado**
- **Sitio server-rendered** (`quotes.toscrape.com`): 212 palabras en 0,7 s sin tocar Playwright (`rendered_js: False`), igual que antes.
- **Sitio con JS** (`quotes.toscrape.com/js/`): trafilatura sacó 6 palabras y Playwright 192. `render_js=True` sobre `/js/page/2/`: 382 palabras en 1,5 s, reusando el Chromium ya abierto (el log "Chromium headless iniciado" aparece una sola vez).
- **URL inexistente**: `ScrapeError` legible (`net::ERR_NAME_NOT_RESOLVED`).
- **Chroma apagado**: `save_url` y `search_knowledge` devuelven el error claro, y `save_url` no deja un `.md` a medio escribir en el vault.
- **Concurrencia real**: dos procesos de `server.py` por stdio (simulando Claude Code y Claude Desktop) arrancaron a la vez, con 2 `save_url` en paralelo cada uno (Wikipedia de estoicismo y epicureísmo, quotes y quotes/js, esta última por Playwright dentro del proceso MCP). Resultado: sin errores de lock ni de conexión, 35 chunks (16+1+17+1) y búsqueda OK desde los dos. Se repitió con las mismas URLs (updates concurrentes): siguieron siendo 35 chunks, sin duplicados, y un commit por archivo.
- **`curl http://127.0.0.1:8055/api/v2/heartbeat`** responde (es el chequeo que quedó documentado en BUILD.md).
- Las fuentes de prueba se borraron del vault y de Chroma (0 chunks). Quedan en el historial de git.

## 2026-09-26 — Dashboard unificado (`./brain.sh`)

**Qué se hizo**
- **Un solo comando, `./brain.sh`**, abre un dashboard en `http://127.0.0.1:8765` y reemplaza a `graph.sh`, `graph_server.py` y las terminales sueltas. Opciones: `--port`, `--no-autostart` y `--no-browser`.
- **Archivos nuevos**:
  - `dashboard.py`: server HTTP de stdlib; sirve las páginas, la API y las acciones.
  - `brain_mcp/services.py`: administrador de procesos.
  - `brain_mcp/stats.py`: estadísticas y chequeos de salud.
  - `brain_mcp/dashboard.html`: la UI.
- **Archivos borrados**: `graph_server.py` y `graph.sh`. Sus endpoints (`/api/graph`, `/api/file`) pasaron a `dashboard.py`, y `graph.html` se sirve en `/graph`, embebido en la pestaña Grafo.
- **Servicios con switch**: Chroma, Ollama e Inspector MCP. Cada uno corre como subproceso en su propio grupo de procesos, y su salida se guarda en memoria (últimas 500 líneas) para la pestaña Logs. Estados: Corriendo, Iniciando, Externo, Apagado y Error.
  - Si un servicio ya estaba corriendo por fuera (por ejemplo la app Ollama), aparece como "Externo" con el switch bloqueado, y el dashboard nunca lo mata.
  - Al arrancar, el dashboard prende Ollama y Chroma si no están corriendo. Con Ctrl+C o SIGTERM apaga solo lo que prendió él (SIGTERM al grupo, y SIGKILL si no responde en 6 s).
- **Pestaña Panel**:
  - Tarjetas de servicios con puerto, tiempo encendido y botones "Abrir" (el Inspector, con su token) y "Logs".
  - Métricas: archivos y palabras, proyectos, skills, fuentes, chunks en Chroma, conexiones y tags.
  - Gráficos: cambios por día en los últimos 30 días (con vista de tabla), fuentes por dominio (top 8) y operaciones del git (creados, actualizados, editados, agregados, borrados).
  - Salud del sistema: Ollama, modelo `nomic-embed-text`, Chroma, Chromium de Playwright y registro en Claude Code y Claude Desktop, cada uno con el comando para arreglarlo.
  - Últimos 12 commits del vault.
- **Otras pestañas**:
  - Grafo: el de antes, embebido.
  - Conocimiento: guardar URL (con opción de forzar render JS), búsqueda semántica con barra de score y tabla de fuentes. Usa las mismas funciones que las tools MCP de `server.py`.
  - Logs: salida en vivo por servicio.
- **Diseño**:
  - Tokens claros y oscuros, con selector de tema (sistema, claro, oscuro) que también se aplica al grafo embebido vía `?theme=`. Tipografías Inter y JetBrains Mono.
  - Gráficos en SVG a mano: una sola serie en el azul de la paleta de referencia, barras con el extremo de datos redondeado y apoyadas en la base, tooltips al pasar el mouse, grilla discreta. Los estados usan ícono y texto, nunca solo color.
  - Responsive: debajo de 760 px el menú lateral pasa arriba.

**En qué se apartó de BUILD.md / de lo anterior y por qué**
- **Seguridad**: el dashboard puede lanzar procesos y escribir en el vault, así que tiene dos defensas.
  - Rechaza todo pedido cuyo `Host` no sea `127.0.0.1:<puerto>` o `localhost:<puerto>`, contra DNS rebinding.
  - Los POST exigen el header `X-Brain: 1`. Un header propio obliga al navegador a hacer un preflight CORS que este server nunca aprueba, así que otra página abierta no puede disparar acciones.
- **Chroma lanzado con `RUST_LOG=warn`**: sin eso loguea una línea INFO por cada request, y como el panel consulta cada pocos segundos, los logs se llenaban de ruido. Además, el conteo de chunks se cachea 10 s y se invalida al guardar una URL.
- **URL del Inspector**: la versión actual imprime `http://127.0.0.1:6274?MCP_INSPECTOR_API_TOKEN=…`, no el `localhost:6274/?MCP_PROXY_AUTH_TOKEN=…` documentado en versiones anteriores. El regex acepta los dos formatos. Se lanza con `MCP_AUTO_OPEN_ENABLED=false` para que no abra su propia pestaña; se abre con el botón "Abrir".
- **Ejes con números redondos y pares** (`niceMax` con 1, 2, 4, 6, 8, 10 × 10ⁿ): son conteos, y la primera versión mostraba "12,5" en la línea del medio.
- **Los gráficos se redibujan al mostrar el Panel**: se dibujan con el ancho real del contenedor, y si se dibujaban con la pestaña oculta (ancho 0) quedaban escalados, con texto enorme.
- **Tradeoff**: si cerrás el dashboard, Chroma se apaga (si lo prendió él), y las tools `save_url` y `search_knowledge` de Claude devuelven "El server de Chroma no está corriendo…" hasta que lo vuelvas a abrir. Para un Chroma que sobreviva al dashboard, sigue existiendo `./chroma_server.sh` (el dashboard lo detecta como "Externo").

**Verificado**
- Arranque: prendió Chroma solo, detectó Ollama como "Externo" y las 6 comprobaciones de salud dieron OK.
- Desde la UI: guardar `quotes.toscrape.com/js/` (entró solo por Playwright), buscar ("thinking and changing the world" dio 62 %), ver la fuente en la tabla, y prender el Inspector con el switch (el link sale con el token). Se revisaron Panel, Grafo y Logs en claro y en oscuro.
- Seguridad: POST sin `X-Brain` da 403, `Host: evil.com` da 403, y `/api/file?path=../BUILD.md` es rechazado por la validación del vault.
- Ctrl+C: 8055, 6274 y 8765 quedan libres, sin procesos huérfanos, y Ollama (externo) sigue vivo.
- La fuente de prueba se borró del vault y de Chroma (0 chunks).

## 2026-09-26 — Perfil y memoria, ES/EN, historial sin git, repo listo para GitHub

**Qué se hizo**
- **Sidebar**: el texto "Tema: sistema" pasó a ser un botón con ícono (monitor, sol o luna según el tema), y al lado hay uno de idioma (globo + ES/EN). Ambos tienen tooltip y `aria-label`.
- **Traducción ES/EN de toda la interfaz**: diccionarios `I18N` en `dashboard.html` y `graph.html`; el dashboard le pasa el idioma al grafo con `?lang=`. El idioma por defecto es español y la elección queda guardada en `localStorage`.
  - Para poder traducir, la API ahora devuelve claves y datos en vez de textos: salud (`key` + `cmd`), operaciones (`key`) y errores de `save_url` y `search` (`code`: `bad_url`, `ollama`, `chroma`, `scrape`, `other`, más `detail`).
  - `server.py` separa `save_url_core()` (devuelve un dict), que usan tanto la tool como el dashboard.
- **Vista Perfil** (`brain_mcp/memory.py` + `/api/profile`, `/api/memory/*`):
  - Formulario "Sobre vos" (nombre, en una línea, sobre mí) que se guarda en `vault/profile.md`.
  - "Importar memoria" en 3 pasos: elegir el origen (Claude, ChatGPT, Gemini u Otro), copiar un prompt que le pide al chatbot su memoria en formato `## Categoría` / `- dato`, y pegar la respuesta o subir un `.txt`, `.md` o `.json`. Hay previsualización, donde se pueden sacar ítems antes de importar.
  - "Tu memoria": lista filtrable, con botón para borrar cada ítem.
- **Parser de memorias** (`memory.parse`): acepta encabezados markdown, líneas `**Etiqueta**` o `Etiqueta:`, viñetas `-`, `*`, `•` o numeradas, líneas planas y JSON (listas, dicts con `content`/`memory`/`text`/`fact`, categorías por clave). Quita prefijos de fecha tipo `[2025-03-02] -`, deduplica sin importar mayúsculas ni acentos, y descarta la línea de introducción del chatbot cuando termina en ":".
- **Memoria en el vault**: un archivo por categoría (`memory/<categoría>.md`, una viñeta por hecho) con `sources` (de qué chatbot vino) y `related`. Este último se calcula solo: si una memoria menciona por nombre un proyecto, skill o fuente del vault, la categoría queda relacionada con ese nodo.
- **Grafo**:
  - Tipos nuevos `profile` (rosa) y `memory` (verde azulado). El perfil muestra el nombre del usuario, cuelga de `BRAIN.md`, y cada categoría de memoria cuelga del perfil (tipo de relación `memory`).
  - Controles fijos abajo a la derecha: acercar, alejar y **volver al centro**. También tecla `0`, teclas `+`/`-` y doble click en el fondo. Se sacó el chip "Centrar" de la barra.
- **Tool nueva `add_memory(fact, category)`**, para que Claude guarde hechos nuevos del usuario. Las `instructions` del server ahora le piden leer `profile.md` y `memory/` y usar `add_memory`.
- **Historial sin git** (`brain_mcp/history.py`): SQLite en `data/history.sqlite3` (tabla `changes`: id, ts, op, path, before, after).
  - `vault.py` ya no usa git: cada operación registra el contenido anterior y el nuevo.
  - Tools nuevas `file_history(path)` y `restore_file(path, version_id)`. La restauración también queda en el historial, así que se puede deshacer, y recupera archivos borrados.
  - El panel toma de ahí la actividad, las operaciones y los últimos cambios.
- **Repo listo para GitHub**:
  - `.gitignore` excluye `vault/` y `data/`.
  - El vault se crea solo desde una plantilla (`vault.ensure_vault()`: `BRAIN.md` genérico + carpetas), que llaman `server.py` y `dashboard.py` al arrancar.
  - Chroma pasó de `./chroma_db` a `./data/chroma`.
  - `register-desktop.sh` calcula solo el path del proyecto y de `uv`, sin rutas personales.
  - Se inicializó git en la raíz, **sin commitear**: `git add --dry-run .` lista solo código y docs.
- **Datos borrados**: el vault anterior con su `.git` (el historial de commits de las pruebas), `chroma_db/` y el contexto personal. `BRAIN.md` ahora es una plantilla genérica. Se sacaron nombres, rutas `/Users/…` y referencias personales de BUILD.md, README.md, CHANGES.md y `server.py`.
- Se silenció el logger de `httpx` en `dashboard.py` y `server.py`, que escribía una línea por cada request a Chroma.

**En qué se apartó de BUILD.md y por qué**
- **Historial en SQLite, no en git**: BUILD.md pedía `git commit` automático en `vault/`. Pero un repo git anidado dentro del repo de la app rompe el push a GitHub (queda como submódulo sin URL) y además es el lugar donde viven los datos personales. SQLite da el mismo rollback, por archivo y con restauración explícita, sin depender de `git` instalado. Para concurrencia: un `flock` sobre `data/.vault.lock` hace atómico leer, escribir y registrar, y SQLite corre en WAL con `busy_timeout`.
- **Una categoría por archivo, no una memoria por archivo**: con cientos de memorias el grafo quedaría ilegible, y para el modelo es más útil `read_file("memory/trabajo.md")` que 40 archivos sueltos.
- **Importar con un prompt en lugar de leer exports propietarios**: ni ChatGPT ni Gemini exportan la memoria en un archivo estable, y los formatos cambian. Pedirle al propio chatbot la lista con un formato fijo funciona igual con Claude, ChatGPT, Gemini u otro. Para quien ya tenga un archivo, el parser acepta además texto libre y JSON.
- **Relaciones memoria → proyecto por coincidencia de nombre**, no por embeddings: es determinístico, no depende de que Ollama esté prendido y no agrega latencia al importar.
- **Mensajes de error de detalle**: los errores con código (`ollama`, `chroma`, `bad_url`) se muestran traducidos. El detalle técnico de un scrape fallido queda en el idioma original, porque viene de la excepción.

**Verificado**
- **Parser** con 4 formatos: markdown que responde al prompt (con intro y un duplicado), lista plana con prefijo de fecha, `**Etiqueta**` con numeradas, y JSON. Categorías, deduplicado y limpieza correctos.
- **API**: guardar perfil; importar 3 memorias; reimportar la misma (0 nuevas); `add_memory` por tool MCP. En el grafo: perfil → 2 categorías, `BRAIN.md` → perfil, y "Trabajo" relacionada sola con el proyecto `tienda-online`, que la memoria mencionaba.
- **Historial**: crear, editar y borrar dan versiones 6, 7 y 8; `restore_file` a la 6 recupera el archivo borrado con "versión 1"; una versión ajena y `../x` se rechazan. Concurrencia: 2 procesos × 10 appends simultáneos dan 20 líneas y 20 entradas de historial, sin pérdidas.
- **UI** (en otro puerto, porque había un dashboard viejo abierto en el 8765):
  - Perfil en ES y EN; importar desde la UI (origen Gemini, previsualizar, sacar un ítem con ×, importar) funciona.
  - Grafo: se arrastró fuera de la vista y el botón de centrar lo trajo de vuelta entero; el cambio de idioma llega al grafo (filtros, contador, tooltips).
  - El Panel muestra el KPI de memorias y la actividad desde el historial.
- **Server MCP por stdio**: 14 tools; `read_file("BRAIN.md")` devuelve la plantilla nueva.
- Los datos de prueba se borraron de nuevo: el vault quedó con solo la plantilla y `data/` no existe hasta el primer cambio.

## 2026-09-26 — Conectar agente, instalador de un comando y README para GitHub

**Qué se hizo**
- **`README.md` → `CLAUDE.md`** (con `git mv`): la guía técnica pasó a ser el contexto para agentes que trabajen en el repo, con un encabezado nuevo que apunta al README y a CHANGES.
- **`README.md` nuevo, para GitHub**: qué es, instalación de un comando, primeros pasos, cada pestaña del dashboard, tabla de agentes con sus archivos de config, arquitectura (diagrama mermaid), el flujo de `save_url` y del import de memoria, referencia de tools y del comando `brain`, privacidad y seguridad, solución de problemas, desinstalación y desarrollo.
- **Vista "Conectar agente"** (sidebar, `brain_mcp/agents.py`, `/api/agents`, `/api/agents/<key>/connect|disconnect`):
  - Pestaña **Conectar**: "Apps de escritorio" (Claude Desktop, ChatGPT), "Otros agentes e IAs" (Claude Code, Codex CLI, Cursor, VS Code/Copilot, Windsurf, Gemini CLI) y "Cualquier otro cliente MCP", con la config lista para copiar en JSON, TOML y como comando.
  - Pestaña **Mis conexiones**: los agentes conectados, su archivo de config, si apuntan a esta instalación o a otra carpeta (en ese caso se puede reconectar), y botón para desconectar.
  - Cada tarjeta muestra el estado (Conectado / No conectado / No instalado / Apunta a otra carpeta), la descripción, el path de config y, después de actuar, qué hacer (reiniciar la app, etc.). Todo traducido ES/EN.
- **`agents.py`**:
  - Escritura por tipo de archivo: JSON (`mcpServers`, o `servers` con `"type": "stdio"` para VS Code), TOML (`[mcp_servers.brain]` en `~/.codex/config.toml`) y CLI (`claude mcp add/remove -s user`).
  - Hace backup `.bak-brain`, conserva el resto del archivo y valida el TOML antes de escribirlo.
  - Detecta si el cliente está instalado (app en /Applications, CLI en el PATH o config existente) y si la app está abierta.
- **Salud del sistema**: los chequeos "Registrado en Claude Code/Desktop" se reemplazaron por "Al menos un agente conectado", calculado con `agents.py`.
- **Instalador `install.sh`** (`curl -fsSL https://raw.githubusercontent.com/Lautaro005/brain/main/install.sh | bash`):
  - Instala uv si falta, clona en `~/.brain` (o `git pull` si ya existe), corre `uv sync` e instala Chromium.
  - Instala Ollama con Homebrew si falta, levanta Ollama temporalmente para bajar `nomic-embed-text` si no está, y crea `~/.local/bin/brain`, agregándolo al PATH en `~/.zshrc` si hace falta.
  - Es idempotente. Variables: `BRAIN_HOME`, `BRAIN_REPO`, `BRAIN_BRANCH`, `BRAIN_BIN`.
- **`brain.sh` con subcomandos**: `update` (git pull + uv sync), `path`, `uninstall` (saca solo el comando generado por el instalador y avisa que los datos siguen en la carpeta) y `help`. Sin argumento, o con flags, abre el dashboard. Busca uv también en `~/.local/bin`, porque el wrapper puede correr con un PATH mínimo.

**En qué se apartó y por qué**
- **ChatGPT se conecta por `~/.codex/config.toml`**, no por un archivo propio: la documentación de OpenAI indica que la app de escritorio solo usa servers MCP locales (stdio) en los modos Codex y ChatGPT Work, y que comparte esa config con Codex CLI. En el chat común de ChatGPT un server local no funciona sin un túnel; la UI lo aclara en vez de prometer algo que no anda.
- **Claude Desktop con la app abierta**: como reescribe su config al salir, el dashboard pide confirmación, la cierra con `osascript`, espera a que termine, escribe y la vuelve a abrir. La confirmación avisa que, si brain corre desde la terminal de Claude, se corta.
- **Claude Code usa su CLI** en vez de editar `~/.claude.json` directamente: ese archivo guarda mucho más que los servers MCP y lo maneja el propio CLI.
- **URL del repo sin `.git`** (`https://github.com/Lautaro005/brain`), a pedido del usuario. `git clone` funciona igual.
- **`uninstall` no borra la carpeta**: ahí viven `vault/` y `data/`. Borrar datos del usuario tiene que ser un paso explícito (`rm -rf ~/.brain`), que se indica.

**Verificado**
- **`agents.py` contra un HOME falso** (sin tocar las configs reales): conectar ChatGPT, Cursor y Gemini; Codex figura conectado por compartir el TOML; se conservan las otras entradas (`otro`, `profiles`, `model`, `theme`); un `mcp.json` de VS Code con comentarios devuelve `bad_config` sin escribir nada; desconectar deja los archivos como estaban; se generan los backups.
- **Instalador** con un remoto local (`BRAIN_REPO=file://…`), en una carpeta y un bin temporales: instala, `brain help` y `brain path` funcionan, el dashboard instalado responde, detecta 8 agentes, la config manual apunta a la carpeta de la instalación y el vault se crea desde la plantilla. `brain update` trae un commit nuevo, reinstalar encima actualiza sin error, y `brain uninstall` saca solo el comando.
- **UI "Conectar agente"** contra las configs reales, en modo solo lectura: Claude Desktop y Claude Code aparecen conectados ("apuntan a esta instalación"), ChatGPT instalado sin conectar, Cursor/Windsurf/Gemini no instalados. "Mis conexiones" lista los 2. La config manual se ve correcta.
- **No probado en vivo**: conectar Claude Desktop con la app abierta (cierre y reapertura), porque cerraría la app donde se estaba trabajando. La lógica usa `osascript quit` + `pgrep`, igual que la detección que sí se probó.

## 2026-09-26 — README en inglés, licencia MIT, apps con formulario "Add MCP server" y landing page

**Qué se hizo**
- **`README.md` traducido al inglés** (es la cara del repo en GitHub). Suma: link a la web, la sección "Apps with an 'Add MCP server' form", una fila de troubleshooting para el `403`, la sección License, y la aclaración de que las docs internas siguen en español.
- **`LICENSE`: MIT**, a nombre de Lautaro Silva, 2026. Es la licencia más usada en open source y permite usar, modificar y redistribuir manteniendo el aviso de copyright.
- **Apps con formulario "Add MCP server"** ("Run a command" / "Connect to a URL"): el usuario probó "Connect to a URL" con la dirección del dashboard y recibió `403`. brain es un server stdio y no expone una URL MCP; el dashboard rechaza cualquier POST sin su header propio, que es lo esperado. `agents.manual_snippets()` suma `form` (nombre, comando y argumentos uno por línea), y la vista "Conectar agente" muestra esos valores campo por campo, con botón de copiar y la indicación de usar "Run a command". Traducido ES/EN.
- **Landing page `docs/index.html`**: un solo HTML sin build, blanco y negro, EN/ES (se detecta del navegador y queda guardado), servido por GitHub Pages desde `main /docs` (Pages activado con `gh api`: https://lautaro005.github.io/brain/). `docs/.nojekyll` hace que se sirva sin Jekyll.
  - Dirección: fichero Zettelkasten. Fichas tipeadas con numeración de referencia (1, 1a, 3.1…); cada agente es una ficha que apunta a la ficha 1, la memoria.
  - Hero: comando de instalación copiable y una disposición de fichas donde se entinta, por turnos o al pasar el mouse, la referencia de cada agente y las memorias que lee.
  - Secciones: por qué (memorias aisladas vs compartida), qué hay en el fichero (pestañas con perfil, memoria, notas, páginas guardadas e historial), tres pasos, catálogo de agentes, mockup del dashboard, privacidad ("Reglas del fichero") y cierre con la instalación.
  - Todo el producto está recreado en HTML/SVG, sin imágenes. Las demos (Ana Ruiz, tienda online) están marcadas como ejemplo.
- **`PRODUCT.md`** (contexto de producto para el skill de diseño) y el brief de la superficie en `.impeccable/surfaces/`. `.gitignore` excluye las capturas y el trabajo temporal de `.impeccable/`.

**Cómo se diseñó**
- **Skill impeccable, en modo code-led** (no hay generación de imágenes). Público y tipo de visual se decidieron con el usuario. La dirección se sorteó con `concept-seed` (seed f9010b46) y el usuario eligió el "Fichero de notas" (la elección propia del skill) por sobre la asignada ("láminas de tinta" de Cajal).
- **Detector**: se corrigieron sombras de borde fino con blur ancho y textos de menos de 11 px. El resto fueron falsos positivos del análisis estático.
- **Revisión final** con el revisor independiente (disposición `fix`, 8 correcciones), aplicadas en una sola tanda:
  - líneas de referencia también en celular;
  - pestañas de ficha en el menú, la activa según la sección;
  - fichas divisorias con número en cada sección;
  - grafo propio para pantallas angostas;
  - pestañas del fichero como divisorias escalonadas;
  - marca de lápiz con tilde en lugar de bloques negros;
  - Courier solo para texto tipeado;
  - privacidad como ficha tipeada y demos marcadas como ejemplo.

- **Segunda ronda del revisor**: las 8 correcciones quedaron resueltas y aparecieron 2 regresiones, que se corrigieron: en celular la leyenda "sample memory" quedaba tachada por una línea de referencia (ahora va arriba de la ficha), y al menú le faltaba la pestaña 6 (Dashboard). Con eso se cerraron las dos rondas del presupuesto de revisión.
- **`DESIGN.md` y `.impeccable/design.json`**, escritos por el documentador del skill a partir de la página construida: paleta monocromo, rampa tipográfica, fichas, sellos, pestañas y reglas (Ink-Only, Pencil Floor, Typed-Only-When-Typed…). El dashboard de la app queda fuera de ese sistema.

**Verificado**
- **Capturas de página completa** a 1440 y 390 px (Playwright): sin desborde horizontal; el cambio EN/ES funciona en todas las secciones; el hero entinta referencias y memorias.
- **Pages** responde el alta con `html_url` https://lautaro005.github.io/brain/, fuente `main /docs`. Publica después del push.

## 2026-09-26 — Conexiones (brain como cliente MCP, con Composio opcional), dashboard con el estilo de la landing y rutas por instalación

**Qué se hizo**
- **Conexiones** (`brain_mcp/connectors.py`, vista "Conexiones" en el sidebar, endpoints `/api/connections*` y `/api/composio/*`):
  - brain ahora también es cliente MCP. Se conecta a otros servers MCP y re-expone sus tools a todos los agentes conectados, con el prefijo de la conexión (`<prefijo>__<tool>`).
  - Tres tipos: **comando local** (stdio; los tokens quedan en la máquina), **URL** (server remoto, con headers) y **Composio** (opcional).
  - Cada conexión tiene dos switches, **Activa** y **Guardar en memoria**, más los botones "Actualizar tools", "Editar" y "Eliminar".
  - `server.py` pasa a usar `BrainServer`, una subclase de `MCPServer` que sobrescribe `list_tools`/`call_tool`. Las tools proxeadas se leen en cada pedido, así que desactivar una conexión la saca de los agentes sin reiniciar; llamarla desactivada devuelve un error claro.
  - Tools nuevas: `list_connections` y `refresh_connectors`.
- **Auto-captura**: el resultado de cada tool proxeada se guarda en `vault/knowledge/connections/<conexión>/<tool>-<fecha>-<hash>.md` (o `knowledge/composio/<app>/…` para Composio), con frontmatter (`tool`, `connection`, `args`, `fetched_at`, `chroma_ids`, `indexed`), y se indexa en Chroma con `source_md_path`. Queda buscable con `search_knowledge` y aparece en el grafo. Corre en un thread para no trabar el event loop y nunca rompe la llamada: sin Chroma u Ollama, el `.md` se guarda igual con `indexed: false`.
- **Secretos**: los valores de env y headers, la API key y el user_id de Composio van a `ROOT/.env` (permisos 600, en `.gitignore`), y la config guarda solo referencias `$env:CLAVE`. El dashboard nunca devuelve secretos: muestra qué claves están cargadas y la URL con los parámetros enmascarados. Al editar, un valor vacío conserva el guardado; al borrar una conexión, se limpian sus secretos. La lista de conexiones está en `data/connections.json` y el caché de tools en `data/connections_tools.json`.
- **Composio (opcional)**: se carga la API key y el user ID, "Elegir apps" lista las auth configs (`GET /api/v3.1/auth_configs`) y "Crear conexión" crea el server MCP (`POST /api/v3.1/mcp/servers` con `auth_config_ids`). Después arma la URL con `user_id` y la guarda como conexión `composio` con el header `x-api-key`.
- **Dashboard con el sistema de la landing**, en versión de trabajo:
  - Monocromo, Archivo + Courier Prime, esquinas de 3–4 px, encabezados de tarjeta con regla de tinta y títulos en Courier caps, estados como sellos, switches y navegación activa en tinta, KPIs en Archivo condensado y logo de ficha.
  - Tema oscuro que invierte tinta y papel. Rojo solo para errores y acciones destructivas.
  - Grafo monocromo: cada tipo de nodo se distingue por relleno, contorno y trazo (perfil con anillo grueso, memoria hueca, fuentes punteadas…).
  - DESIGN.md amplía su alcance al dashboard.
- **Rutas por instalación**: los valores de "Conectar agente" (comando `uv`, `--directory`) siempre se calcularon en cada Mac a partir de la carpeta instalada y del `uv` del usuario. Ahora la vista lo dice explícitamente y muestra la carpeta de esta instalación (`install_path`, con `~`).
- La métrica "Conexiones" del Panel pasó a llamarse "Enlaces" (cuenta enlaces del grafo) para no confundirse con la vista nueva.
- `httpx` agregado a `pyproject.toml`, porque la API de Composio lo usa directamente.

**En qué se apartó del pedido y por qué**
- **Composio no se puede hostear local** en sus planes self-serve: guarda los tokens OAuth de las cuentas conectadas en su nube, y la versión self-hosted es una oferta paga para empresas, sin código abierto. La spec que pasó el usuario usaba igualmente `backend.composio.dev`. Con el acuerdo del usuario se implementó un **hub local** (conexiones por comando local, con tokens en la máquina) donde Composio es un tipo de conexión opcional, y la UI y el README avisan dónde quedan sus tokens.
- **Una sesión por llamada** en lugar de sesiones persistentes: es más simple y robusto con varios procesos de `server.py` (uno por agente). El costo es que un server stdio se relanza en cada llamada.
- **El grafo no usa color**, ni siquiera por tipo de nodo, para respetar la regla Ink-Only del sistema; lo reemplazan relleno, contorno y trazo.

**Verificado**
- **Server MCP de prueba por stdio** (vault, `data/` y `.env` temporales):
  - alta con un secreto: queda solo en `.env` (600), nunca en la config;
  - descubrimiento de 2 tools, que aparecen como `mail__fetch_emails` y `mail__send_email` en `list_tools` de brain;
  - llamada proxeada con el resultado real, auto-captura en `knowledge/connections/mail/…md`, y `search_knowledge` la encuentra;
  - desactivada: desaparece de la lista y llamarla da error;
  - borrada: el secreto sale del `.env`.
- **El mismo server por HTTP** (`run_streamable_http_async`) con header `Authorization`: descubre, llama y muestra la URL enmascarada.
- **UI con Playwright**: alta desde la API y tarjeta con tools; el switch "Activa" apaga y atenúa la tarjeta; "Editar" precarga las claves sin valores y muestra "Cancelar"; "Elegir apps" sin API key da el error claro; "Eliminar" borra. Capturas del Panel, Conexiones, Conectar agente y Grafo en claro y oscuro, y de Conexiones a 390 px sin desborde.
- **Server real por stdio**: 16 tools, con `list_connections` y `refresh_connectors`.
- **No probado en vivo**: el provisioning contra la API real de Composio, porque no hay API key en esta máquina. El código sigue la documentación de `/api/v3.1/auth_configs` y `/api/v3.1/mcp/servers` y maneja los errores HTTP con mensajes claros.

## 2026-09-26 — Composio con consumer keys (`ck_`), errores HTTP legibles y release v0.01.0

**Qué se hizo**
- **Soporte de consumer keys de Composio** (`ck_…`): el usuario cargó una y recibió `401 Invalid API key`, porque el código mandaba cualquier key como `x-api-key` a la API de desarrollador (`backend.composio.dev/api/v3.1`), que solo acepta keys de proyecto (`ak_…`).
  - Ahora `composio_configure()` detecta el tipo por el prefijo. Con `ck_` crea directamente la conexión `composio` contra `https://connect.composio.dev/mcp` con el header `x-consumer-api-key`: no hace falta user ID ni elegir apps, porque vienen las que el usuario vinculó en Composio.
  - Con `ak_` sigue el flujo anterior (auth configs + `POST /api/v3.1/mcp/servers` con `x-api-key`).
  - `_composio()` rechaza una consumer key con `composio_consumer_key` y un mensaje claro, en vez de dejar que la API devuelva 401.
- **Dashboard (Composio)**: "Guardar y conectar" con una `ck_` conecta y descubre las tools en un paso. "Elegir apps" se oculta con consumer keys. Hay un botón nuevo "Desconectar" (con confirmación) que borra la conexión y la key/user ID del `.env`, más un texto que explica los dos tipos de key. Todo en ES/EN. Endpoint nuevo: `POST /api/composio/disconnect`.
- **Errores de conexión legibles**: el cliente MCP solo devolvía "Server returned an error response", sin el status HTTP. Si falla una conexión por URL, `_http_probe()` repite el `initialize` a mano y muestra, por ejemplo, `HTTP 401 de connect.composio.dev: {…}`.
- **`.env` vacío**: a pedido del usuario se borraron del `.env` la API key y el user ID que había cargado para probar; quedó solo el comentario de cabecera. El archivo sigue en `.gitignore` y nunca se subió.
- **Release `v0.01.0`** en GitHub, con el número exacto que pidió el usuario.
- README (Composio, troubleshooting del 401) y CLAUDE.md actualizados.

**Verificado**
- Con una `ck_` falsa, en carpetas temporales:
  - la conexión se crea con la URL `connect.composio.dev/mcp` y el header `x-consumer-api-key`, y la key no queda en la config;
  - la API de desarrollador se rechaza con `composio_consumer_key`;
  - el descubrimiento contra el endpoint real devuelve un error legible (`HTTP 401 de connect.composio.dev: {"error":"Authorization required",…}`);
  - "Desconectar" deja el `.env` vacío y sin conexiones.
- No se probó con una key real válida, porque no hay ninguna en esta máquina.
