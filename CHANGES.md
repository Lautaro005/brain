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

## 2026-09-27 — Ajustes con colores del grafo, sidebar plegable, "Mis conexiones" con apps por formulario, Chat con Ollama, widget en la landing y licencia sin reventa

**Qué se hizo**
- **Ajustes (colores del grafo)** (`dashboard.html`, `graph.html`): ícono de engranaje al final de la fila de tema e idioma. Abre un modal con un selector de color por tipo de nodo (Brain, Perfil, Memoria, Proyectos, Skills, Fuentes, Notas, Carpetas, Tags), un botón "Usar colores distintos" (paleta predefinida) y "Volver a blanco y negro". Se guarda en localStorage (`brain-graph-colors`). El grafo lo lee al cargar y escucha el evento `storage`, así el cambio se ve al instante sin recargar. Los tipos huecos (perfil, memoria, tags) llevan un relleno suave del color para no perder la diferencia de forma.
- **Sidebar plegable** (`dashboard.html`): botón en la fila del logo, entre el ícono y el nombre, como pidió el usuario. Plegado, el sidebar queda en un riel de 64 px con los íconos de cada vista (con tooltip), y los botones de tema, idioma y ajustes. El estado se recuerda (`brain-side-min`). En celular no aplica: ahí el menú ya va arriba.
- **"Mis conexiones" muestra las apps agregadas por formulario** (`brain_mcp/clients.py` nuevo, `server.py`, `dashboard.py`, `stats.py`):
  - Antes solo listaba los agentes cuya config lee `agents.py`. Las apps con formulario "Add MCP server" guardan la config donde quieren y nunca aparecían.
  - Ahora `BrainServer` anota el `clientInfo` (nombre y versión) que manda cada cliente en el handshake MCP, en `data/clients.json`, con la fecha del último uso. Cualquier app que use brain aparece sola.
  - Si el nombre corresponde a un agente conocido (claude-code, claude-ai, cursor…), se muestra con su nombre y se suma a su fila.
  - Card nueva "¿Lo agregaste en otra app?" para anotar una app a mano antes de usarla. Botón "Quitar de la lista" (no toca la app; si vuelve a usar brain, reaparece).
  - El chequeo de salud "Al menos un agente conectado" cuenta también los detectados y los anotados.
  - Endpoints: `GET /api/agents` suma `mine`; `POST /api/clients/add` y `/api/clients/remove`.
- **Chat** (`brain_mcp/chat.py` y `brain_mcp/chat.html` nuevos, vista "Chat" en el sidebar, página `/chat`):
  - Habla con un modelo de chat de Ollama por `/api/chat` con streaming. El system prompt incluye `profile.md`, todas las memorias (hasta ~12k caracteres), `BRAIN.md` y los fragmentos de chats anteriores más parecidos a la pregunta.
  - Recibe las mismas tools MCP que los agentes (`server.mcp.list_tools()`/`call_tool()`, incluidas las de conexiones), así puede consultar, agregar, modificar y borrar notas, guardar memorias, buscar y guardar URLs. Cada acción aparece como un chip con su estado. Todo queda en el historial de versiones. Hasta 6 vueltas de tools por respuesta.
  - Si el modelo no soporta tools, reintenta sin ellas y avisa.
  - Selector con los modelos instalados (sin los de embeddings). Si no hay ninguno, botón "Descargar llama3.2" (`/api/pull`) y el comando para la terminal.
  - **Historial en ChromaDB**, colección `chats`: un registro `<id>:meta` con título y fechas, y uno por mensaje (`<id>:00000`…) con su embedding. Lista con búsqueda por título, abrir y borrar. Sin Chroma el chat funciona igual y avisa que no se guarda. Si la respuesta falla sin generar nada, no se guarda el mensaje suelto; si el usuario corta la respuesta, se guarda lo parcial.
  - **Ventana flotante**: botón "Ventana flotante". En Chrome, Edge y Arc usa Document Picture-in-Picture (queda arriba de todas las apps). En Safari y Firefox abre una ventana emergente común (`/chat?mode=pop`, con el historial como panel lateral).
  - Endpoints: `GET /api/chat/models|list|get`, `POST /api/chat/send` (NDJSON), `/api/chat/delete` y `/api/chat/pull`. Todos pasan por la misma validación de Host y de `X-Brain` que el resto.
  - `chroma_store.py`: `get_collection(name)` y `call(fn, name)` ahora sirven para cualquier colección (antes solo "sources").
- **Landing** (`docs/index.html`): el script del widget de DokBot antes de `</body>`, con el `data-bot-id` que pasó el usuario. El Dashboard de la landing menciona el chat (texto, mock del sidebar y sello "Chat local"), en EN y ES.
- **Licencia** (`LICENSE`): de MIT a **MIT + Commons Clause**. Se puede usar, modificar y compartir gratis, también en el trabajo, pero no vender: no se puede cobrar por un producto o servicio (incluidos hosting, soporte o consultoría) cuyo valor venga entera o sustancialmente de brain. README, PRODUCT.md, CLAUDE.md y la landing (EN/ES) pasaron de "open source · MIT" a "source available" / "código a la vista".
- README: sección de la pestaña Chat, "Mis conexiones", Ajustes, sidebar plegable y dos filas nuevas de troubleshooting del chat.

**En qué se apartó del pedido y por qué**
- **Licencia**: se eligió Commons Clause sobre MIT y no una licencia "no comercial" como PolyForm Noncommercial. El pedido era que no se pueda vender la herramienta, y PolyForm también prohíbe usarla en un trabajo pago, cosa que el pedido no incluía. Las versiones publicadas antes (por ejemplo `v0.01.0`) siguen bajo MIT para quien ya las tenga: una licencia no se puede revocar hacia atrás.
- **Detección de apps por formulario**: no hay forma de leer la config de cualquier app. Por eso se detectan por uso (handshake MCP) y aparecen la primera vez que usan brain, no apenas se guarda el formulario. Para ese hueco existe la opción de anotarlas a mano.
- **Modelo del chat**: no se fija ninguno, porque depende de lo que el usuario tenga instalado. Se sugiere `llama3.2` (liviano y con soporte de tools).
- **Registro en el vault del usuario**: el pedido incluía anotar los cambios en el conector `brain` (proyecto brain). Esta sesión corre en la nube sin acceso al vault local ni a ese conector, así que queda para hacerlo desde un agente conectado.

**Verificado**
- **Detección de clientes**: con clientes MCP reales por stdio (`ClientSession` con `client_info` "LM Studio" y "claude-code"), `data/clients.json` registró ambos. `/api/agents` los devuelve en `mine` ("Claude Code" con su nombre conocido), junto con uno anotado a mano.
- **Chat** contra un Ollama falso (mismo protocolo NDJSON de `/api/chat`, `/api/tags` y `/api/embeddings`) y un Chroma real:
  - el system prompt trae el perfil, la memoria y, en un chat nuevo, fragmentos de chats anteriores;
  - un tool call `add_memory` se ejecutó y escribió `memory/preferencias.md`;
  - continuar un chat manda el historial;
  - un modelo sin tools cae al modo sin tools con aviso;
  - un modelo inexistente da `no_model` y no deja un chat vacío;
  - listar, abrir y borrar funcionan;
  - con Chroma apagado responde igual con `saved: false`.
- **UI con Playwright**, sin errores de consola: sidebar desplegado y plegado; modal de ajustes; "Usar colores distintos" cambia el relleno del nodo Brain del grafo en vivo (`#e11d48`); "Mis conexiones" con detectados y manuales; chat con streaming y chip de tool; cambio a EN y a tema oscuro propagado al iframe del chat; modo `pop` a 440 px; dashboard a 390 px sin desborde horizontal.
- **Landing** a 1440 y 390 px: sin desborde y con el script de DokBot presente.
- **No probado**: un modelo real de Ollama (no hay Ollama en esta máquina) y la ventana Picture-in-Picture en un Chrome con interfaz, porque Playwright headless no la abre. El código cae a `window.open` si la API falla.

## 2026-09-27 — Release v0.02.0 (borrador de notas)

**Qué se hizo**
- Se escribieron las notas del release **`v0.02.0`**, con el mismo formato que `v0.01.0`: instalación, novedades (Chat, Ajustes con colores del grafo, sidebar plegable, apps por formulario en "Mis conexiones", widget en la web) y el cambio de licencia a MIT + Commons Clause, aclarando que `v0.01.0` y anteriores siguen bajo MIT. El release apunta a `main`, así que el tag se crea al publicarlo, después de mergear el PR.

**En qué se apartó del pedido y por qué**
- El pedido era dejar el release como borrador en GitHub, pero la API respondió "Creating, editing, or deleting releases is not permitted for this session type". Por eso las notas quedaron listas para pegar y el borrador lo crea el usuario en GitHub (Releases → Draft a new release, tag `v0.02.0` sobre `main`).

## 2026-09-27 — v0.02.1: perfil que no cargaba, frontmatter validado, tools del chat para cualquier modelo y chat rediseñado

**Qué se hizo**
- **Perfil que no cargaba ("list index out of range")** (`memory.py`): `get_profile()` suponía que el cuerpo de `profile.md` era siempre "# Nombre", una línea en blanco y el "Sobre mí". Si un agente reescribía el archivo sin esa línea (o solo con el título), `split("\n", 2)[2]` rompía. Eso tiraba abajo `/api/profile` (el Perfil y las memorias no cargaban) y el contexto del chat. Ahora saca el título sin asumir cuántas líneas hay.
- **Frontmatter inválido** (`vault.py`, más los lectores en `graph.py`, `stats.py`, `memory.py`, `server.py` y `dashboard.py`):
  - En los logs aparecía `yaml.scanner.ScannerError: mapping values are not allowed` en `/api/file`: un agente había escrito una description sin comillas con ": " adentro.
  - El grafo tragaba ese error en silencio y mostraba el nodo sin description. Por eso el chat "ya lo había cambiado" pero el grafo no lo mostraba.
  - Ahora toda escritura de un `.md` pasa por `vault.check_frontmatter()`: repara el YAML (pone entre comillas los valores problemáticos) o rechaza la escritura con un error claro que el agente ve.
  - Todas las lecturas usan `vault.parse()`, que nunca se rompe y también repara al leer, así los archivos ya rotos vuelven a mostrar su description.
- **Tool nueva `set_frontmatter(path, fields)`** (`vault.py`, `server.py`): cambia metadatos sin reescribir el archivo (`null` borra un campo). El chat tiene la instrucción de usarla para metadatos.
- **Tools del chat con cualquier modelo** (`chat.py`):
  - El error del modelo bajado de Hugging Face (`HTTP 400 … Unable to generate parser for this template … properties must be an object`) viene de Ollama/llama.cpp cuando el template del GGUF no puede convertir las tools nativas.
  - Ahora los esquemas se limpian (`_clean_schema`: sin `anyOf`/`title`/`default: null`, todo objeto con `properties`).
  - Si Ollama igual rechaza las tools, el chat pasa al **modo texto**: las tools van descritas en el system prompt y el modelo las pide con `<tool_call>{…}</tool_call>`. Esos bloques no se muestran en la respuesta, y los resultados vuelven como un mensaje. El modo se recuerda por modelo.
  - En cualquier modo también se aceptan `<tool_call>` o bloques ```json``` escritos en el texto.
  - Si el modelo inventa una tool que no existe, recibe la lista de tools válidas.
  - El system prompt pide verificar con `read_file` antes de decir que algo "ya está" y no dar por hecho un cambio si la tool no respondió OK.
- **Todas las tool calls a la vista** (`chat.html`): cada llamada es una fila con estado, nombre y argumentos resumidos, y se despliega para ver los argumentos completos y el resultado. El resultado (hasta 1500 caracteres) también se guarda en el historial, así se ve al reabrir el chat.
- **Chat rediseñado** (`chat.html`, `dashboard.html`, DESIGN.md), con el sistema de diseño del proyecto (ver abajo):
  - El chat ocupa toda el área de trabajo, sin encabezado ni tarjeta gris, en una sola superficie con el sidebar.
  - Controles en píldora y contenedores con radio blando: burbuja del usuario a la derecha, compositor flotante con botón redondo de enviar/parar, historial sin panel gris.
  - Nombres de modelos de Hugging Face abreviados en el selector, y placeholder corto en la ventana flotante y en pantallas angostas.
  - La excepción de forma quedó documentada en DESIGN.md.
- **Botón de plegar el sidebar** (`dashboard.html`): ahora va después del logo y el nombre, alineado a la derecha, como pidió el usuario. Plegado queda debajo del logo.
- README (tabla de tools, Chat, troubleshooting, validación de frontmatter) y CLAUDE.md actualizados.

**En qué se apartó del pedido y por qué**
- **Skill de diseño**: el usuario pidió usar la skill `impeccable`, pero no está instalada en esta sesión. Se usó `design-taste-frontend` sobre el sistema que impeccable había dejado en el repo (DESIGN.md, `.impeccable/`).
- **Registro en el conector `brain` y release `v0.02.1`**: esta sesión en la nube no tiene acceso al vault local ni al conector, y la API de GitHub no permite crear releases desde este tipo de sesión. Quedaron las notas del release para pegar.

**Verificado**
- Contra un Ollama falso que reproduce el error exacto del modelo de Hugging Face, con un Chroma real:
  - el modelo `hf.co/…` pasa solo al modo texto y cambia la description con `set_frontmatter`;
  - `llama3.2` con tools nativas hace 3 llamadas (una a una tool inexistente, que falla con la lista de tools válidas) y las tres aparecen;
  - un modelo que pide la tool con un bloque ```json``` la ejecuta y el bloque se saca de la respuesta.
- Un `profile.md` como el del usuario (solo "# Lautaro", sin línea en blanco) y un proyecto con `description: Proyecto: brain` sin comillas: `/api/profile` y `/api/file` responden bien, y el grafo muestra la description.
- UI con Playwright, sin errores de consola: Perfil carga; Chat en claro y oscuro con la traza desplegada; sidebar plegado; ventana flotante a 440 px; celular a 390 px sin desborde.

## 2026-09-28 — v0.02.5: 6 mejoras de memoria, sidebar sin tagline, licencia detectable, SECURITY.md, Docs y Changelog en la web

**Qué se hizo** (según `MEMORY_UPGRADES_SPEC.md`, en el orden que pedía)
1. **Búsqueda híbrida** (`brain_mcp/keyword_store.py` nuevo, `server.py`, `connectors.py`, `dashboard.py`):
   - Índice FTS5 en `data/search.sqlite3` (tabla `chunks_fts`, BM25 nativo), mismo patrón de conexión que `history.py` (WAL + busy_timeout) y misma interfaz que `chroma_store` (`upsert`, `delete`, `query`).
   - Cada `chroma_store.upsert/delete` de `save_url_core()` y de la auto-captura de conexiones tiene al lado su `keyword_store.upsert/delete` con los mismos argumentos.
   - `server.search_core()` pide 3×`top_k` a cada lado y fusiona con reciprocal rank fusion (`1/(60+rank)`). Cada hit trae `score` (similitud semántica o `None`), `rrf` y `match` (`both`/`semantic`/`keyword`). Con Chroma u Ollama caídos devuelve solo lo de palabra y una nota; solo falla si fallan los dos. La usan la tool `search_knowledge` y `/api/search`.
   - Tool `reindex_keyword_search()` (+ botón "Reindexar búsqueda por palabra" en Conocimiento, `/api/search/reindex`): reconstruye el índice desde los `.md` de `knowledge/` sin re-scrapear ni embeber.
   - Dashboard: los resultados que solo matchean por palabra muestran un sello "por palabra" en vez de la barra de score, y un aviso si la semántica está caída.
2. **Hechos con vigencia** (`memory.py`, `server.py`): cada viñeta activa lleva `<!-- since:YYYY-MM-DD -->`. `add(..., supersede=)` pasa el hecho viejo a `## Historial` al final de su archivo con `(desde …, superado el …, ver: "…")`. `_load()` devuelve `(meta, [{text, since}], historial)`; `list_all()` sigue dando solo los activos. Tool `add_memory(fact, category, supersede)` y tool nueva `memory_history(category)`.
3. **Resúmenes** (`brain_mcp/summarize.py` nuevo): `summarize(text, max_words=120)` con `/api/generate` de Ollama; `None` si no hay Ollama o modelo de chat. Fuentes de más de 800 palabras (y capturas largas) guardan `abstract` en el frontmatter (no se toca `description`); `list_sources()` lo devuelve.
4. **Entidades** (`brain_mcp/entities.py` nuevo, `graph.py`, `graph.html`, `dashboard.html`): `extract(text)` pide una lista JSON y parsea con tolerancia (JSON, array dentro de texto, objeto con lista, líneas con guión). Se llama en los call sites de `server.py`, `connectors.py` y `memory.py` (no en `vault.py`) y va a `entities`. `graph.py` crea nodos `entity:<slug>` con relación `entity` (rango entre `related` y `mention`). En el grafo: nodo chico con anillo de puntos, filtro "Entidades", y color propio en Ajustes.
5. **`distill_skill(topic)`** (`server.py`): devuelve `chunks` de la búsqueda híbrida, `memory` (hechos cuyo texto o categoría contiene el tema), `sources`, `skill_path`, `existing_skill` e `instructions`. No escribe nada.
6. **Reflect** (`brain_mcp/reflect.py` nuevo, `/api/reflect`, card "Reflect" en Perfil): sugiere `posible_duplicado`, `posible_contradiccion` y `entidades_faltantes` (archivos de `knowledge/`/`memory/` de los últimos 200 cambios sin `entities`), cada una con `accion_sugerida`. Nunca escribe.

**Además**
- **Sidebar** (`dashboard.html`): se sacó "knowledge server" (y su clave `tagline` de ES/EN); logo y nombre quedan en una fila, centrados en vertical (verificado: mismo centro en y).
- **Licencia que GitHub detecta**: `LICENSE` tenía la Commons Clause arriba del MIT, y licensee (lo que usa GitHub) no lo reconoce. Ahora `LICENSE` es el MIT tal cual y la condición está en `COMMONS-CLAUSE.md`, referenciada desde README, la landing y las docs.
- **`SECURITY.md`**: versiones soportadas, reporte privado por GitHub Security Advisories, qué entra en el alcance (dashboard, validación de paths, secretos, configs de agentes, instalador, renderizado) y qué no.
- **Web**: `docs/docs/index.html` (documentación con la estructura de las docs de Vercel: sidebar agrupado con números de ficha y filtro con `/`, migas, "En esta página" con resaltado al scrollear, anterior/siguiente, "Editar en GitHub"; 19 páginas en EN y ES) y `docs/changelog/index.html` (v0.02.5, v0.02.1, v0.02.0 y v0.01.0 con fecha y cambios). Comparten `docs/assets/pages.css` y `pages.js`. La landing suma los links Docs y Changelog arriba y en el pie.
- **`install.sh`**: al final, si no hay un modelo de chat en Ollama, avisa que `ollama pull llama3.2` es opcional y qué activa. No lo baja solo.
- README (tools, flujo de `save_url`, búsqueda, memoria en el tiempo, entidades, Reflect, troubleshooting, licencia, seguridad) y CLAUDE.md actualizados.

**En qué se apartó del spec y por qué**
- **Tokenizer `unicode61 remove_diacritics 2`, sin `porter`**: porter es un stemmer para inglés y el vault es mayormente en español; así "nunez" encuentra "Núñez" sin romper palabras en español. Columna extra `chunk_index` (UNINDEXED) para devolver lo mismo que Chroma.
- **`INSERT OR REPLACE` → `DELETE` + `INSERT`**: una tabla FTS5 no tiene clave única sobre `chunk_id`, así que `OR REPLACE` duplicaría.
- **La captura indexa por palabra aunque Chroma falle** (antes de intentar Chroma): la búsqueda por palabra es justamente lo que tiene que andar cuando Chroma no está.
- **Timeout de 45 s en vez de ~20 s** (`BRAIN_LLM_TIMEOUT`): con un modelo local en CPU, resumir ~12k caracteres suele pasar los 20 s y el resumen se perdería siempre. Sigue siendo best-effort y sin reintentos.
- **Modelo**: `BRAIN_SUMMARY_MODEL`; si no está, `llama3.2` si está instalado, si no el primer modelo de chat instalado (así funciona con el que ya use el Chat). `off` apaga resúmenes y entidades. No se agregó el pull al instalador (~2 GB): queda documentado como opcional y el instalador lo sugiere.
- **`supersede` busca también en otras categorías** si no está en la misma, y el hecho viejo va al historial de *su* archivo. Si no hay coincidencia exacta, se acepta el único hecho que contiene el texto (o está contenido en él).
- **Reflect detecta duplicados y contradicciones por texto** (difflib: ≥0,9 por caracteres = duplicado; mismas dos primeras palabras y ≥0,6 por palabras = contradicción) en vez de con `search_knowledge`: las memorias no están en el índice de fuentes, y así no depende de Ollama. Procesa hasta 20 archivos sin entidades por corrida (cada uno es una llamada al modelo). Las sugerencias traen `tipo` + `data` para que el dashboard las traduzca (regla de i18n), además de `descripcion`/`accion_sugerida` en español para agentes.
- **`search_core` en `server.py`** en vez de meter la fusión adentro de la tool: la usan la tool, `distill_skill` y el dashboard.
- **Release `v0.02.5`**: la sesión no tiene una tool para crear releases en GitHub (igual que en v0.02.0/v0.02.1); quedaron las notas listas para pegar. La fecha del changelog para v0.02.5 es la de hoy: si se publica otro día, hay que corregirla. Las fechas de las versiones anteriores son las de `published_at` en GitHub (UTC): v0.01.0 figura el 2026-09-27 porque se publicó a las 00:30 UTC (26/9 a la noche en Argentina).

**Verificado**
- Script contra un vault, `data/` y Chroma (puerto 8056) temporales y un Ollama falso (mismo protocolo de `/api/tags`, `/api/embeddings` y `/api/generate`): 33 chequeos OK. Entre ellos: FTS ignora acentos y encuentra `INC-4821`; `supersede` mueve el hecho al historial (también entre categorías), `list_all` ya no lo muestra, el dedupe sigue igual y sin coincidencia guarda con aviso; `abstract` solo en la fuente de >800 palabras y `list_sources` lo devuelve; dos fuentes que nombran a "Ana Ruiz" quedan unidas por `entity:ana-ruiz` en `build_graph()`; búsqueda por término exacto (`match: both`) y por tema; con Chroma apagado devuelve lo de palabra con la nota; `reindex` reconstruye con los mismos ids; `distill_skill` devuelve material y no escribe; Reflect detecta duplicado, contradicción y nota sin entidades sin agregar ninguna entrada al historial; sin Ollama `save_url` guarda igual sin `abstract` ni `entities`.
- Captura de conexión: `.md` con `abstract` y `entities`, y el ID `FAC-2291` encontrable por palabra.
- Server real por stdio: 20 tools, con `distill_skill`, `memory_history`, `reindex_keyword_search` y `supersede` en el esquema de `add_memory`.
- Dashboard con Playwright: logo y nombre en una fila; Reflect muestra una contradicción y un duplicado; el grafo dibuja los nodos de entidad y el chip "Entidades"; textos en ES y EN; POST sin `X-Brain` sigue dando 403.
- Web a 1440 y 390 px: landing, docs y changelog sin desborde horizontal (se corrigieron un desborde de 25 px en el changelog, el menú de docs en celular que quedaba bajo la barra, y los links Docs/Changelog de la landing que desaparecían en celular); cambio de idioma, filtro, "En esta página" y anterior/siguiente funcionan.
- **No probado**: un modelo real de Ollama (no hay Ollama en esta máquina; la calidad de resúmenes y entidades depende del modelo) ni la mejora de la búsqueda sobre embeddings reales de `nomic-embed-text` (el Ollama falso usa bolsa de palabras). Que GitHub muestre "MIT" se ve recién después de mergear a `main`.

## 2026-09-28 — v0.02.5 (sigue): header de la web más limpio y Ajustes como vista, con versión y actualizaciones

**Qué se hizo**
- **Landing (`docs/index.html`)**:
  - El header deja solo el logo de GitHub (sin texto, con `aria-label`) al lado de EN/ES. Docs y Changelog salieron del header: corrían las pestañas y no parecían centradas.
  - El header pasó de `flex` con `space-between` a una grilla `1fr auto 1fr`, así las pestañas quedan centradas en la página sin importar el ancho del logo o del lado derecho (verificado: centro de las pestañas = centro de la página a 1440 y 1000 px). En celular, donde las pestañas se ocultan, la grilla pasa a `1fr auto`.
  - El pie tiene Docs, Changelog y **GitHub con el logo y el texto** (antes era el link `github.com/Lautaro005/brain`).
- **Ajustes es una vista del dashboard** (`#ajustes`, antes un modal). El engranaje del sidebar lleva ahí y queda marcado como activo. Secciones:
  1. **Versión**: archivo `VERSION` nuevo (`v0.02.5`) y `brain_mcp/updates.py`. El botón "Buscar actualizaciones" (`POST /api/version/check`) le pregunta a GitHub por `/releases/latest` y compara números. Si hay una versión nueva, Ajustes muestra un aviso en verde con `brain update` y un link a las novedades, y **el engranaje del sidebar se pone en verde** (con un punto). El resultado queda en `localStorage` (`brain-update`), así el verde sigue después de recargar y se apaga solo cuando la versión instalada la alcanza. `GET /api/version` da la versión instalada.
  2. **Sidebar**: orden con flechas ↑/↓ y un check "Visible" por vista, más "Restablecer orden". Se guarda en `brain-nav` (`{order, hidden}`) y se aplica moviendo los botones del sidebar. Al menos una vista tiene que quedar visible. Ajustes no está en la lista: es el engranaje, siempre visible.
  3. **Chat**: modelo por defecto (`brain-chat-default`), de los modelos instalados en Ollama, o "El último que usé" (el comportamiento de antes). `chat.html` lo elige al cargar y en cada chat nuevo.
  4. **Colores del grafo**: lo mismo que había en el modal.
- **Celular**: el sidebar de arriba ahora muestra la fila de tema, idioma y Ajustes (antes se ocultaba, y Ajustes no se podía abrir en celular).
- README, CLAUDE.md, las docs (Dashboard → Ajustes, EN/ES) y el changelog de v0.02.5 actualizados.

**En qué se apartó del pedido y por qué**
- **El chequeo de actualizaciones es manual**, no automático al abrir: brain promete no salir a internet solo (README, SECURITY.md, docs). El botón en verde se mantiene entre sesiones con el último resultado guardado.
- **Verde**: el sistema del dashboard es monocromo con rojo solo para errores; el verde se agregó solo para "hay una versión nueva" (variables `--update`/`--update-soft`, con versión oscura), como pidió el usuario.
- **La vista no instala la actualización**: muestra `brain update`. Actualizar desde el dashboard implicaría reiniciar el proceso que lo sirve (y los servers MCP de los agentes siguen con el código viejo hasta reiniciarlos).
- **Una clase `.links` rompía el ícono de GitHub del pie**: la landing ya usa `.links path` para las líneas punteadas del hero, así que el contenedor del pie se llama `foot-links`.
- La rama se rehízo desde `main` porque el PR anterior (#5) ya estaba mergeado; estos cambios van en un PR nuevo.

**Verificado**
- `updates.check()` contra GitHub real: con `VERSION` = v0.02.5 da "al día" (el último publicado es v0.02.1); con `VERSION` = v0.02.0 da `update_available: true` con v0.02.1.
- Playwright (dashboard con un Ollama falso): el engranaje abre `#ajustes`; bajar Panel y ocultar Logs cambia el sidebar y persiste al recargar; elegir `llama3.2` como default hace que el chat lo use al abrir; con v0.02.0, "Buscar actualizaciones" muestra el aviso y pone el engranaje en verde, y sigue verde después de recargar y con el sidebar plegado; textos en EN; a 390 px sin desborde y con Ajustes accesible; sin errores de JS.
- Landing a 1440, 1000 y 390 px sin desborde; pie con el logo de GitHub bien dibujado.

## 2026-09-29 — v0.02.8: chat configurable, comandos, /organize, diálogos propios y licencia en un archivo

**Qué cambió**
- **System prompt del usuario** (Ajustes → System prompt, `dashboard.html`): textarea de hasta 4000 caracteres guardado en `localStorage` (`brain-chat-system`). `chat.html` lo manda en cada mensaje y `chat._context()` lo suma como "Instrucciones del usuario" al final, sin reemplazar las reglas de las tools.
- **Ventanas propias** (`brain_mcp/ui.js`, nuevo, servido en `/ui.js` por `dashboard.py`): `BrainUI.confirm` reemplaza los 6 `confirm()` (borrar memoria, quitar cliente, reiniciar app, borrar conexión, desconectar Composio, borrar chat); lo destructivo va en rojo y con el foco en Cancelar. `BrainUI.select` reemplaza los dos `<select>` (modelo del chat y modelo por defecto) con un dropdown propio con teclado.
- **Modelo fijo por chat** (`chat.html`): el selector aparece solo en un chat nuevo; al enviar el primer mensaje pasa a una etiqueta con candado. Al reabrir un chat se usa su modelo (ya se guardaba en el meta).
- **Medidor de contexto** (`chat.py`, `chat.html`): `models()` devuelve `ctx`/`ctx_max` por modelo desde `/api/show`; el chat manda `num_ctx` y emite el evento `usage`; el círculo (una "O" que se llena) a la izquierda de enviar muestra el uso, con tooltip y rojo desde 90%. `BRAIN_CHAT_CTX` (16384) topea la ventana.
- **Renombrar chats**: lápiz al lado del título → input inline; `POST /api/chat/rename` → `chat.rename_chat()` (actualiza el meta en Chroma).
- **Comandos** (`/`): lista arriba del compositor con descripción en es/en; `/organize`, `/reflect`, `/new`. `chat.parse_command()`, `COMMANDS`, `CMD_TITLES`, `_command_prompt()`; el comando queda en el meta del chat. `reflect.run(use_llm=False)` para no llamar al modelo local mientras ya hay uno respondiendo.
- **/organize**: skill integrada `brain_mcp/skills/organize.md`, adaptada de *file-organizer* de davila7/claude-code-templates (MIT, atribución y copyright en su frontmatter) para el vault: preguntar alcance → analizar → plan → ejecutar solo lo aprobado → resumen. `brain_mcp/organize.py` arma el panorama (sueltas, sin conexiones, sin descripción, variantes de tags, nombres parecidos). Tools nuevas `move_file` (`vault.move_file`, historial `move` en origen y destino, borra carpetas vacías) y `vault_overview`.
- **Sidebar**: Chroma, Ollama e Inspector pasaron a un popover del botón **i** a la izquierda del engranaje (punto rojo si algún servicio falla, link a "Administrar en el Panel").
- **Licencia**: `LICENSE` vuelve a tener la Commons Clause arriba del MIT; se borró `COMMONS-CLAUSE.md`. README, docs, landing, changelog y CLAUDE.md actualizados.
- `VERSION` → `v0.02.8`; changelog, docs (Dashboard, Chat, Tools, Configuración) y README.

**Por qué**
- Pedido del usuario (8 puntos). Sobre la licencia: el usuario vio "MIT" en GitHub y entendió que se había perdido la restricción de venta. Los términos nunca cambiaron, pero el badge "MIT" comunica lo contrario, y eso importa más que el autodetectado de GitHub: ahora aparece "Other" / "View license", que es lo correcto para MIT + Commons Clause.
- `num_ctx`: sin él Ollama usa su default (2-4k tokens) y el system prompt con memoria + tools lo supera, así que se cortaba en silencio. El tope de 16k evita pedir 128k de caché en una Mac.
- `/organize` no borra ni mueve sin confirmación y no toca lo que maneja brain (`memory/`, `knowledge/sources/`: moverlas rompería los ids de Chroma/FTS).

**Verificado**: 24 pruebas nuevas de backend (panorama, move_file, restauración, protecciones, reflect sin LLM, ventana de contexto, rename) + las 35 anteriores; 38 chequeos en Chromium con Ollama falso y Chroma real (dropdown con teclado, comandos, organize/system prompt llegan al modelo con `num_ctx`, anillo, modelo fijo al reabrir, renombrar persiste, confirmaciones propias sin `confirm()` nativo, popover de servicios arriba/plegado/celular, 390 px sin scroll horizontal, sin errores JS). No probado con un Ollama real.
