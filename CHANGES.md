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
