"""Chat del dashboard: un modelo local de Ollama con la memoria del usuario como contexto.

- **Contexto**: cada respuesta arranca con un system prompt que incluye profile.md, todas las
  memorias (memory/), el índice BRAIN.md y los fragmentos de chats anteriores más parecidos a la
  pregunta (búsqueda semántica en Chroma).
- **Acciones**: el modelo recibe las mismas tools MCP que los agentes (leer, escribir, add_memory,
  search_knowledge, save_url, las de las conexiones…) y el dashboard las ejecuta. Todo lo que
  escribe queda en el historial de versiones, así que se puede deshacer.
- **Historial**: cada chat vive en la colección "chats" de Chroma: un registro `<id>:meta` con el
  título y uno por mensaje (`<id>:00000`, …) con su embedding, así los chats viejos también sirven
  de contexto. Sin Chroma el chat funciona igual, pero no se guarda.

Ollama se habla por HTTP (/api/chat con stream y tools). Si el modelo no soporta tools, se reintenta
sin ellas y el chat queda en modo solo lectura del contexto.
"""
import asyncio
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone

import requests

from . import chroma_store, memory, organize, reflect, vault
from .embeddings import OllamaUnavailable, embed

log = logging.getLogger(__name__)

OLLAMA = "http://localhost:11434"
COLL = "chats"
SUGGESTED_MODEL = "llama3.2"
MAX_ROUNDS = 6           # vueltas de tool calls por respuesta
MAX_HISTORY = 24         # mensajes previos del chat que se mandan al modelo
MAX_TOOL_RESULT = 8000   # caracteres de cada resultado de tool que ve el modelo
MAX_MEMORY_CHARS = 12000
EMBED_CHARS = 4000       # nomic-embed-text: alcanza con el principio del mensaje
MAX_SYSTEM_USER = 4000   # instrucciones propias del usuario (Ajustes → Chat)
MAX_TITLE = 80
# Ventana de contexto que se le pide a Ollama (num_ctx). Sin esto Ollama usa su default (2-4k tokens) y
# corta en silencio el system prompt con la memoria. Por defecto se usa el máximo del modelo con este tope
# para no pedir más RAM de la que tiene una Mac común; se cambia en Ajustes → Contexto (ctx_settings).
CHAT_CTX = int(os.environ.get("BRAIN_CHAT_CTX", "16384"))
DEFAULT_CTX = 4096       # si Ollama no dice el máximo del modelo

# Comandos del chat (/organize…): agregan instrucciones al system prompt de ese chat y se recuerdan
# en el chat (meta "command"), así las respuestas del usuario a las preguntas siguen en el mismo modo.
COMMANDS = {
    "organize": "Ayudame a ordenar mi vault y su grafo.",
    "reflect": "Revisá mi memoria y lo que guardé: duplicados, contradicciones y notas sin entidades.",
    "compact": "Resumí la conversación hasta acá.",
    "add-mcp": "Quiero agregar un server MCP a las Conexiones de brain.",
    "factcheck": "Verificá esto:",
}
# /compact no es un modo: resume lo anterior en un mensaje marcado `compact` y, desde ahí, al modelo le
# llega ese resumen en lugar de los mensajes viejos (que siguen guardados y visibles en el chat).
COMPACT_PROMPT = (
    "Resumí la conversación de abajo para poder seguirla sin el historial completo. Incluí: qué pidió el "
    "usuario, qué se decidió o respondió, datos concretos (nombres, números, paths de archivos), qué cambios "
    "se hicieron en el vault con las tools, y qué quedó pendiente. Viñetas, máximo ~300 palabras, sin "
    "introducción. Escribí en el idioma de la conversación.")
CMD_TITLES = {"es": {"organize": "Ordenar el vault", "reflect": "Revisar la memoria", "add-mcp": "Agregar un conector",
                     "factcheck": "Fact check"},
              "en": {"organize": "Organize the vault", "reflect": "Review my memory", "add-mcp": "Add a connector",
                     "factcheck": "Fact check"}}
FACTCHECK_PROMPT = """# Modo /factcheck: verificar afirmaciones
El usuario quiere verificar algo. Llamá fact_check UNA vez con el texto a verificar, tal cual lo escribió (si solo
escribió /factcheck, preguntale qué quiere verificar). Después presentá el resultado:
- por cada afirmación, el veredicto tal como vino (no lo cambies ni lo suavices) y por qué, en una o dos líneas;
- las fuentes principales con su link, tipo y fecha, y la cita que lo justifica;
- qué vino de brain y qué de internet, y las limitaciones;
- si es de salud, derecho, finanzas o seguridad: que es informativo y no reemplaza a un profesional.
Podés usar una tabla HTML o Markdown. No guardes fuentes en el vault: si el usuario quiere, lo hace desde Fact check."""
FACTCHECK_AUTO = ("# Fact check automático (Ajustes del usuario)\nSi el usuario pregunta un dato factual (números, "
                  "noticias, salud, leyes, hechos recientes) y no está en el contexto ni en search_knowledge, usá "
                  "fact_check antes de responder y avisá que consultaste fuentes externas.")
ADD_MCP_PROMPT = """# Modo /add-mcp: agregar un conector
El usuario quiere sumar un server MCP a las Conexiones de brain. Pasos:
1. Si te pasó una URL de documentación (GitHub, npm, PyPI, la web del servicio), leela con read_url. Si te pasó
   directamente la URL de un server MCP remoto (suele terminar en /mcp o /sse), usala tal cual.
2. Armá la config: remoto → url; local → command + args (Node: command "npx", args ["-y", "<paquete>"];
   Python: command "uvx", args ["<paquete>"]). Anotá qué variables de entorno o headers pide (API keys).
3. Si falta una API key u otro dato, pedíselo al usuario en una pregunta corta y esperá. Si el server
   remoto usa login (OAuth), no hace falta key: el usuario inicia sesión después de aprobar.
4. Llamá propose_connection con name, url o command/args, env/headers y una nota de una línea.
5. Contale en una o dos líneas qué propusiste y que tiene que tocar «Agregar» en la tarjeta que aparece
   abajo (o en Conexiones). Vos no podés agregarla solo: la aprobación es del usuario.
No inventes paquetes ni URLs: si la documentación no alcanza para saber el comando, decilo y preguntá."""


# Contexto fijo sobre cómo funciona brain: va siempre en el system prompt (antes de las instrucciones del
# usuario), así el chat puede explicar la app y guiar al usuario. No se edita: Ajustes → Chat lo muestra
# plegado, de solo lectura. Si cambia algo visible de la app (vistas, comandos, tools), actualizarlo acá.
APP_GUIDE = {
    "es": """# Cómo funciona brain (contexto fijo de la app)
brain es la base de conocimiento local del usuario: corre en su computadora (macOS, Linux o Windows) y le da la
misma memoria a todos sus agentes de IA por MCP. Todo vive en su máquina: el vault (notas en Markdown) en vault/
y los índices, el historial y los ajustes en data/.
- Vault: BRAIN.md (índice corto), profile.md (perfil), memory/<categoría>.md (un hecho por viñeta, con fecha),
  projects/, skills/ (skills reutilizables) y knowledge/sources/ (páginas web guardadas con save_url).
- Cada escritura queda en el historial: file_history + restore_file deshacen cualquier cambio.
- Búsqueda híbrida (search_knowledge): semántica con Ollama + Chroma, y por palabra (SQLite FTS5).
- Vistas del dashboard (http://127.0.0.1:8765): Panel (servicios Chroma, Ollama e Inspector, métricas y salud),
  Chat (este chat), Perfil (datos del usuario, importar memoria de otro chatbot, Reflect), Conectar agente
  (Claude, ChatGPT, Codex, Cursor, VS Code, Windsurf, Gemini CLI, OpenMausBot, DeepSeek Harness, Manus Studio
  (con su formulario «Run a command», valores en la tarjeta), y el acceso
  remoto por URL), Conexiones (servers MCP de otros servicios cuyas tools brain usa y guarda), Grafo,
  Conocimiento (guardar y buscar URLs, frescura de fuentes), Fact check (verificar afirmaciones: busca primero
  en brain y, si no alcanza, en internet; da un veredicto con fuentes, citas y limitaciones; sus preferencias
  —nivel, actualidad, fuentes primarias, evidencia en contra, regiones, idiomas, buscador— están en esa vista)
  y Logs. El engranaje abre Ajustes.
- Ajustes: versión y actualizaciones (con «Actualizar y reiniciar» cuando hay una versión nueva), orden del
  menú, modelo y contexto del chat, instrucciones propias para el chat, notificaciones del sistema, backup y
  restauración, formato de las notas (completar type/created/updated en notas viejas) y colores del grafo.
- Grafo: para conectar o desconectar notas, propose_graph_change muestra una imagen con los cambios marcados
  (verde = nueva, rojo = se saca) y el usuario aprueba, rechaza o deshace. También se ven en la vista Grafo.
- Formato de las notas: Markdown con frontmatter compatible con OKF (type, name, title, description, tags,
  related); brain completa solo type, created (cuándo se creó) y updated (último cambio).
- Acceso remoto: Conectar agente → «Acceso remoto por URL» publica el server MCP con un túnel de Cloudflare.
  La URL lleva un token secreto (es como una contraseña); se puede regenerar y poner en solo lectura. Así se
  conectan agentes en la nube como Manus en la web, Claude.ai o ChatGPT.
- Comandos del chat: /organize (ordenar el vault), /reflect (revisar la memoria), /compact (resumir la
  conversación), /add-mcp (proponer un conector), /factcheck (verificar una afirmación), /new (chat nuevo), y
  /<skill> para usar un skill guardado en skills/.
- Conectores: propose_connection solo propone; el usuario los aprueba con un click. Vos no podés agregarlos.
Si el usuario pregunta cómo hacer algo en brain, explicale dónde está en el dashboard con estos nombres.""",
    "en": """# How brain works (fixed app context)
brain is the user's local knowledge base: it runs on their computer (macOS, Linux or Windows) and gives all
their AI agents the same memory over MCP. Everything stays on their machine: the vault (Markdown notes) in
vault/ and the indexes, history and settings in data/.
- Vault: BRAIN.md (short index), profile.md, memory/<category>.md (one dated fact per bullet), projects/,
  skills/ (reusable skills) and knowledge/sources/ (web pages saved with save_url).
- Every write is kept in history: file_history + restore_file undo any change.
- Hybrid search (search_knowledge): semantic with Ollama + Chroma, and keyword (SQLite FTS5).
- Dashboard views (http://127.0.0.1:8765): Dashboard (Chroma, Ollama and Inspector services, metrics, health),
  Chat (this chat), Profile (user details, import memory from another chatbot, Reflect), Connect agent (Claude,
  ChatGPT, Codex, Cursor, VS Code, Windsurf, Gemini CLI, OpenMausBot, DeepSeek Harness, Manus Studio (through its
  "Run a command" form, values on its card), and remote access by URL),
  Connections (other services' MCP servers whose tools brain uses and saves), Graph, Knowledge (save and
  search URLs, source freshness), Fact check (verify claims: searches brain first and, if that's not enough,
  the web; gives a verdict with sources, quotes and limitations; its preferences —level, freshness, primary
  sources, counter-evidence, regions, languages, search engine— live in that view) and Logs. The gear opens
  Settings.
- Settings: version and updates (with "Update and restart" when a new version is out), menu order, chat model
  and context, custom chat instructions, system notifications, backup and restore, note format (fill in
  type/created/updated on older notes) and graph colors.
- Graph: to link or unlink notes, propose_graph_change shows an image with the changes marked (green = new,
  red = removed) and the user approves, rejects or undoes it. They also show in the Graph view.
- Note format: Markdown with OKF-compatible frontmatter (type, name, title, description, tags, related); brain
  fills in type, created (when it was created) and updated (last change) by itself.
- Remote access: Connect agent → "Remote access by URL" publishes the MCP server through a Cloudflare tunnel.
  The URL carries a secret token (treat it like a password); it can be regenerated and set to read-only. Cloud
  agents such as Manus on the web, Claude.ai or ChatGPT connect this way.
- Chat commands: /organize (tidy the vault), /reflect (review memory), /compact (summarize the conversation),
  /add-mcp (propose a connector), /factcheck (verify a claim), /new (new chat), and /<skill> to use a skill
  saved in skills/.
- Connectors: propose_connection only proposes; the user approves with one click. You can't add them yourself.
If the user asks how to do something in brain, tell them where it is in the dashboard using these names.""",
}
MAX_SKILL_CHARS = 12000

# Reglas de trabajo del chat (el "harness"): entender el pedido, resolverlo con las tools justas, verificar y
# responder directo. Van primero en el system prompt. Si cambian las tools que modifican cosas, revisar acá.
HARNESS = """Sos el asistente personal del usuario dentro de brain, su base de conocimiento local. Conocés al usuario por su perfil y su memoria (abajo): usalos para personalizar cada respuesta.

# Cómo trabajar
1. Entendé el pedido antes de actuar: qué resultado quiere el usuario al final (una respuesta, un cambio en el vault, una explicación). Si el pedido es claro, hacelo sin preguntar. Preguntá solo si falta un dato que cambia el resultado y que no está en el contexto ni en el vault; en ese caso, una sola pregunta corta.
2. Usá las tools justas. Si sabés el archivo, leelo directo (read_file) en vez de listar todo; para buscar en lo guardado, search_knowledge; para saber qué hay, list_vault. No repitas una tool con los mismos argumentos: el resultado no cambia.
3. Para cambiar cosas: a) antes de decir que algo "ya está", leelo; b) metadatos (description, tags, name) con set_frontmatter, sin reescribir el archivo; c) texto con str_replace_file o write_file; d) nunca digas que hiciste un cambio si la tool no respondió OK; si respondió Error, contá el error y probá de otra forma.
4. Conexiones del grafo (conectar o desconectar notas, el `related`): usá propose_graph_change, nunca set_frontmatter(related). Eso no cambia nada todavía: el usuario ve una imagen con los cambios marcados y aprueba o rechaza con un click. Para mirar una zona del grafo antes, inspect_graph_region.
5. Conectores (servers MCP): leé su documentación con read_url y proponelo con propose_connection; el usuario lo aprueba, vos no podés agregarlo solo.
6. Si el usuario te cuenta algo duradero sobre sí mismo, guardalo con add_memory. Cada cambio queda en el historial y se deshace con file_history + restore_file.
7. No inventes datos: si algo no está en el contexto, en el vault ni en el resultado de una tool, decilo.

# Cómo responder
- Empezá por la respuesta o el resultado, sin introducción ("¡Claro!", repetir la pregunta). Después, solo el detalle que sirve.
- Si cambiaste algo, terminá con una línea de qué cambiaste y en qué archivo.
- Formato: Markdown. Cuando ayuda a que se entienda mejor, podés usar HTML dentro de la respuesta: tablas con celdas combinadas, <details><summary> para lo largo, <mark>, <kbd>, colores con style="…" o un diagrama simple en <svg>. Sin <script>, formularios, iframes ni imágenes de otros sitios: se borran antes de mostrarse.
- {reply_lang}, salvo que el usuario escriba en otro idioma."""
REPEAT_NOTE = ("Ya llamaste a {name} con estos mismos argumentos en esta respuesta; el resultado está arriba. "
               "No la repitas: respondé con lo que tenés o probá otra cosa.")
FINAL_NUDGE = ("Respondé ahora el pedido del usuario con lo que ya encontraste, sin llamar más tools. Si quedó algo "
               "sin hacer, decí qué y por qué.")


def app_guide() -> dict:
    return {"es": APP_GUIDE["es"], "en": APP_GUIDE["en"]}


class ChatError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code, self.detail = code, detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- modelos ----------

def _is_embedding(m: dict) -> bool:
    fam = " ".join([str((m.get("details") or {}).get("family") or "")] + list((m.get("details") or {}).get("families") or []))
    return "embed" in m.get("name", "").lower() or "bert" in fam.lower()


def models() -> list[dict]:
    """Modelos de chat instalados en Ollama (sin los de embeddings)."""
    try:
        r = requests.get(f"{OLLAMA}/api/tags", timeout=5)
        r.raise_for_status()
    except requests.ConnectionError as e:
        raise ChatError("ollama") from e
    except requests.RequestException as e:
        raise ChatError("ollama", str(e)) from e
    out, s = [], ctx_settings()
    for m in r.json().get("models", []):
        if _is_embedding(m):
            continue
        ctx = context_window(m["name"], m.get("digest", ""), s)
        out.append({"name": m["name"], "size": m.get("size", 0), "params": (m.get("details") or {}).get("parameter_size", ""),
                    "ctx": ctx["ctx"], "ctx_max": ctx["max"], "ctx_source": ctx["source"]})
    return sorted(out, key=lambda m: m["name"])


_MAX: dict[str, int] = {}  # "modelo@digest" → context_length que reporta Ollama


def _model_max(model: str, digest: str = "") -> int:
    """Máximo de contexto que soporta el modelo (/api/show → model_info.<arch>.context_length). 0 si no se sabe."""
    key = f"{model}@{digest}"
    if key in _MAX:
        return _MAX[key]
    mx = 0
    try:
        r = requests.post(f"{OLLAMA}/api/show", json={"model": model}, timeout=5)
        if r.ok:
            info = r.json().get("model_info") or {}
            mx = next((int(v) for k, v in info.items() if k.endswith(".context_length") and v), 0)
    except (requests.RequestException, ValueError):
        pass
    if mx:  # sin respuesta de Ollama no se cachea: se vuelve a preguntar
        _MAX[key] = mx
    return mx


# ---------- ajustes de contexto (Ajustes → Contexto) ----------
# data/chat_settings.json: {"mode": "cap" | "max", "cap": tokens, "models": {modelo: tokens}}
# - "cap": cada modelo usa su máximo, con un tope para todos (por defecto BRAIN_CHAT_CTX o 16k).
# - "max": cada modelo usa todo lo que soporta.
# - "models": un valor propio por modelo, que gana sobre los dos anteriores (y nunca pasa su máximo).
# Vive en data/ (no en el navegador) porque lo usa el server al armar cada pedido a Ollama.
MIN_CTX, MAX_CTX_SETTING = 2048, 1_048_576


def _settings_path():
    from . import history
    return history.DATA / "chat_settings.json"


def ctx_settings() -> dict:
    out = {"mode": "cap", "cap": CHAT_CTX, "models": {}}
    try:
        raw = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if raw.get("mode") in ("cap", "max"):
        out["mode"] = raw["mode"]
    if isinstance(raw.get("cap"), int) and MIN_CTX <= raw["cap"] <= MAX_CTX_SETTING:
        out["cap"] = raw["cap"]
    out["models"] = {str(k): v for k, v in (raw.get("models") or {}).items()
                     if isinstance(v, int) and MIN_CTX <= v <= MAX_CTX_SETTING}
    return out


def save_ctx_settings(data: dict) -> dict:
    """Valida y guarda. Un modelo con valor vacío, 0 o null vuelve a la regla general."""
    mode = data.get("mode", "cap")
    if mode not in ("cap", "max"):
        raise ChatError("bad_ctx", "mode")

    def tokens(v, field):
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise ChatError("bad_ctx", field) from None
        if not MIN_CTX <= n <= MAX_CTX_SETTING:
            raise ChatError("bad_ctx", field)
        return n

    cap = tokens(data.get("cap", CHAT_CTX), "cap")
    per = {}
    for name, v in (data.get("models") or {}).items():
        if v in (None, "", 0, "0"):
            continue
        per[str(name)] = tokens(v, str(name))
    s = {"mode": mode, "cap": cap, "models": per}
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return s


def context_window(model: str, digest: str = "", settings: dict | None = None) -> dict:
    """{"max": lo que soporta el modelo, "ctx": lo que usa brain (num_ctx), "source": model | max | cap}."""
    s = settings or ctx_settings()
    mx = _model_max(model, digest)
    own = s["models"].get(model)
    if own:
        ctx, src = (min(own, mx) if mx else own), "model"
    elif s["mode"] == "max":
        ctx, src = mx or DEFAULT_CTX, "max"
    else:
        ctx, src = min(mx or DEFAULT_CTX, s["cap"]), "cap"
    return {"max": mx or ctx, "ctx": ctx, "source": src}


def pull(model: str) -> None:
    """Descarga un modelo (puede tardar varios minutos)."""
    try:
        r = requests.post(f"{OLLAMA}/api/pull", json={"model": model, "stream": False}, timeout=None)
    except requests.ConnectionError as e:
        raise ChatError("ollama") from e
    if r.status_code != 200 or (r.json() or {}).get("error"):
        raise ChatError("pull_failed", r.text[:300])


# ---------- historial en Chroma ----------

def _call(fn):
    return chroma_store.call(fn, COLL)


def _emb(texts: list[str]) -> list[list[float]]:
    return embed([t[:EMBED_CHARS] or " " for t in texts], task="document")


def list_chats() -> list[dict]:
    res = _call(lambda c: c.get(where={"kind": "chat"}, include=["metadatas"]))
    out = [{"id": m["chat_id"], "title": m.get("title") or "…", "updated_at": m.get("updated_at"),
            "created_at": m.get("created_at"), "count": m.get("count", 0), "model": m.get("model", ""),
            "command": m.get("command", "")}
           for m in res["metadatas"]]
    return sorted(out, key=lambda c: c["updated_at"] or "", reverse=True)


def get_chat(chat_id: str) -> dict:
    res = _call(lambda c: c.get(where={"$and": [{"chat_id": chat_id}, {"kind": {"$in": ["chat", "msg"]}}]},
                                include=["documents", "metadatas"]))
    meta, msgs = None, []
    for doc, m in zip(res["documents"], res["metadatas"]):
        if m.get("kind") == "chat":
            meta = m
        else:
            msgs.append({"role": m["role"], "content": doc, "ts": m.get("ts"), "idx": m.get("idx", 0),
                         "tools": json.loads(m.get("tools") or "[]"), "compact": bool(m.get("compact"))})
    if meta is None:
        raise ChatError("not_found", chat_id)
    msgs.sort(key=lambda x: x["idx"])
    return {"id": chat_id, "title": meta.get("title"), "model": meta.get("model", ""),
            "created_at": meta.get("created_at"), "messages": msgs, "command": meta.get("command", ""),
            "tokens": meta.get("tokens", 0), "ctx": meta.get("ctx", 0)}


def rename_chat(chat_id: str, title: str) -> str:
    """Cambia el título de un chat (el que se ve en el historial)."""
    title = " ".join((title or "").split())[:MAX_TITLE]
    if not title:
        raise ChatError("empty_title")
    res = _call(lambda c: c.get(ids=[f"{chat_id}:meta"], include=["metadatas"]))
    if not res["ids"]:
        raise ChatError("not_found", chat_id)
    meta = {**res["metadatas"][0], "title": title, "renamed": True}
    embs = _emb([title])
    _call(lambda c: c.update(ids=[f"{chat_id}:meta"], documents=[title], embeddings=embs, metadatas=[meta]))
    return title


def delete_chat(chat_id: str) -> None:
    _call(lambda c: c.delete(where={"chat_id": chat_id}))


def _save(chat_id: str, title: str, model: str, created_at: str, new: list[dict], start_idx: int,
          extra: dict | None = None) -> None:
    """Guarda los mensajes nuevos y actualiza el registro del chat."""
    ids, docs, metas = [], [], []
    for i, m in enumerate(new):
        idx = start_idx + i
        ids.append(f"{chat_id}:{idx:05d}")
        docs.append(m["content"] or " ")
        metas.append({"kind": "msg", "chat_id": chat_id, "role": m["role"], "idx": idx, "ts": m.get("ts") or _now(),
                      "tools": json.dumps(m.get("tools") or [], ensure_ascii=False), "compact": bool(m.get("compact"))})
    ids.append(f"{chat_id}:meta")
    docs.append(title)
    metas.append({"kind": "chat", "chat_id": chat_id, "title": title, "model": model, "created_at": created_at,
                  "updated_at": _now(), "count": start_idx + len(new), **(extra or {})})
    embs = _emb(docs)
    _call(lambda c: c.upsert(ids=ids, documents=docs, embeddings=embs, metadatas=metas))


def _related_past(text: str, chat_id: str, k: int = 4) -> list[str]:
    """Fragmentos de otros chats parecidos a la pregunta (para que el chat "se acuerde")."""
    try:
        count = _call(lambda c: c.count())
        if not count:
            return []
        q = embed([text[:EMBED_CHARS]], task="query")
        res = _call(lambda c: c.query(query_embeddings=q, n_results=min(k * 3, count),
                                      where={"$and": [{"kind": "msg"}, {"chat_id": {"$ne": chat_id}}]}))
    except Exception as e:  # sin Chroma o sin chats: se sigue sin este contexto
        log.info("sin contexto de chats anteriores: %s", e)
        return []
    out = []
    for doc, m, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        if dist < 0.6 and len(out) < k:
            who = "usuario" if m.get("role") == "user" else "asistente"
            out.append(f"[{str(m.get('ts', ''))[:10]} · {who}] {doc[:600]}")
    return out


# ---------- contexto ----------

def _split_compact(history: list[dict]) -> tuple[str, list[dict]]:
    """(resumen del último /compact, mensajes posteriores). Sin /compact: ("", todo)."""
    last = max((i for i, m in enumerate(history) if m.get("compact")), default=None)
    if last is None:
        return "", history
    return history[last]["content"], history[last + 1:]


def _transcript(summary: str, msgs: list[dict]) -> str:
    out = [f"(Resumen anterior)\n{summary}"] if summary else []
    for m in msgs:
        who = "Usuario" if m["role"] == "user" else "Asistente"
        tools = ", ".join(t.get("name", "") for t in m.get("tools") or [])
        out.append(f"{who}: {m['content']}" + (f"\n[tools usadas: {tools}]" if tools else ""))
    return "\n\n".join(out)


# nombre válido para /<skill>: ASCII (el regex del front y el del back tienen que coincidir), sin espacios,
# y sin "." ni "/" al final (así "/organize." sigue siendo el comando /organize seguido de un punto)
SKILL_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_./-]*[A-Za-z0-9_-])?$")


def skills() -> list[dict]:
    """Skills del vault (skills/**.md) para usar con /<nombre>: los que guardaron los agentes (distill_skill +
    write_file) o el usuario. Un skill que se llame igual que un comando de brain queda tapado por el comando;
    uno cuyo nombre tenga espacios o letras fuera de ASCII no se puede invocar y no se lista."""
    out = []
    for f in vault.list_files("skills"):
        name = f["path"][len("skills/"):-len(".md")]
        if SKILL_NAME.match(name) and name not in COMMANDS and name != "new":
            out.append({"name": name, "description": f["description"], "path": f["path"]})
    return out


def parse_command(text: str, skill_names: set[str] | None = None) -> tuple[str, str]:
    """"/organize y los proyectos" → ("organize", "y los proyectos"); "/mi-skill algo" → ("skill:mi-skill",
    "algo") si existe skills/mi-skill.md. Sin comando: ("", text). skill_names evita releer skills/ (run lo
    calcula una vez por mensaje)."""
    m = re.match(r"^/([A-Za-z0-9][\w./-]*)(.*)$", text.strip(), re.S | re.A)
    if not m:
        return "", text
    name = m.group(1).rstrip("./")  # puntuación pegada al comando ("/compact.") no es parte del nombre
    rest = text.strip()[1 + len(name):].lstrip(".,;:!?/").strip()
    if name in COMMANDS:
        return name, rest
    if skill_names is None:
        skill_names = {sk["name"] for sk in skills()}
    if name in skill_names:
        return f"skill:{name}", rest
    return "", text


def _command_request(cmd: str, rest: str = "") -> str:
    """Lo que le llega al modelo en lugar de "/comando": el pedido en palabras."""
    if cmd.startswith("skill:"):
        name = cmd[len("skill:"):]
        return (f"Aplicá mi skill «{name}» a esto: {rest}" if rest else
                f"Quiero usar mi skill «{name}». Decime qué necesitás para aplicarlo.")
    return COMMANDS[cmd] + (f" {rest}" if rest else "")


def _command_prompt(cmd: str) -> str:
    if cmd.startswith("skill:"):
        name = cmd[len("skill:"):]
        body = vault.read_file(f"skills/{name}.md")
        return (f"# Skill del usuario: {name}\nEl usuario eligió este skill, guardado en su vault (skills/{name}.md). "
                "Seguí sus instrucciones en esta conversación; si necesita datos que no tenés, preguntá.\n\n"
                + body[:MAX_SKILL_CHARS])
    if cmd == "add-mcp":
        return ADD_MCP_PROMPT
    if cmd == "factcheck":
        return FACTCHECK_PROMPT
    if cmd == "organize":
        return organize.skill() + "\n\n" + organize.overview_text()
    if cmd == "reflect":
        r = reflect.run(use_llm=False)
        items = "\n".join(f"- [{s['tipo']}] {s['descripcion']} → {s['accion_sugerida']}" for s in r["suggestions"]) or "- (no hay sugerencias)"
        return (
            "# Modo /reflect\nEl usuario pidió revisar lo que tiene guardado. Abajo están las sugerencias de Reflect "
            "(solo sugerencias: nada se aplicó). Presentalas agrupadas y en lenguaje simple, preguntá cuáles quiere "
            "aplicar y aplicá SOLO las que confirme, con la tool indicada. Si no hay sugerencias, decilo. "
            + (f"Hay {r['entities_pending']} notas sin entidades: se completan desde Perfil → Reflect. " if r["entities_pending"] else "")
            + "\n\n## Sugerencias\n" + items
        )
    return ""


def _context(text: str, chat_id: str, lang: str, user_system: str = "", command: str = "") -> str:
    p = memory.get_profile()
    mem_lines, used = [], 0
    for c in memory.list_all():
        block = f"## {c['category']}\n" + "\n".join(f"- {i}" for i in c["items"])
        if used + len(block) > MAX_MEMORY_CHARS:
            mem_lines.append("(hay más memorias: usá list_vault('memory') y read_file para verlas)")
            break
        mem_lines.append(block)
        used += len(block)
    try:
        index = vault.read_file("BRAIN.md")[:3000]
    except vault.VaultError:
        index = ""
    past = _related_past(text, chat_id)
    reply_lang = "Respondé en español rioplatense" if lang == "es" else "Answer in English"
    parts = [
        HARNESS.format(reply_lang=reply_lang),
        APP_GUIDE["es"],
        "# Perfil (profile.md)\n" + (f"Nombre: {p['name']}\nEn una línea: {p['headline']}\n\n{p['about']}"
                                     if p["exists"] else "(todavía no cargó su perfil)"),
        "# Memoria del usuario (memory/)\n" + ("\n\n".join(mem_lines) or "(todavía no hay memorias)"),
    ]
    if index:
        parts.append("# Índice del vault (BRAIN.md)\n" + index)
    if past:
        parts.append("# De chats anteriores (pueden servir de contexto)\n" + "\n".join(past))
    if user_system.strip():
        parts.append("# Instrucciones del usuario para el chat (Ajustes)\nRespetalas en el tono, el formato y el "
                     "enfoque de cada respuesta; no reemplazan las reglas de arriba sobre las tools.\n"
                     + user_system.strip()[:MAX_SYSTEM_USER])
    try:
        from . import factcheck
        if factcheck.prefs()["chat_auto"]:
            parts.append(FACTCHECK_AUTO)
    except Exception:
        pass
    if command:
        try:
            parts.append(_command_prompt(command))
        except Exception as e:
            log.warning("comando /%s: %s", command, e)
    parts.append(f"Fecha de hoy: {datetime.now().strftime('%Y-%m-%d')}")
    return "\n\n".join(parts)


# ---------- tools ----------
# Dos modos:
# - "native": las tools van en el campo `tools` de /api/chat y el modelo devuelve `tool_calls`.
# - "text": para modelos cuyo template no soporta tools (o que Ollama no puede convertir, como muchos GGUF
#   bajados de Hugging Face): las tools se describen en el system prompt y el modelo las pide con bloques
#   <tool_call>{"name": …, "arguments": {…}}</tool_call>. Así cualquier modelo puede actuar sobre el vault.
# En los dos modos también se aceptan <tool_call> escritos en el texto, que algunos modelos mandan igual.

_MODE: dict[str, str] = {}  # modelo → "text" si ya sabemos que el modo nativo falla con él
TOOL_TAG = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.S)
FENCED = re.compile(r"```(?:json|tool_call|tool)?\s*(\{.*?\})\s*```", re.S)


def _clean_schema(node):
    """Esquema JSON simple que entienden Ollama y llama.cpp: sin title/default null, anyOf [X, null] → X,
    y todo objeto con `properties` (Ollama manda `properties: null` si falta, y eso rompe algunos templates)."""
    if isinstance(node, list):
        return [_clean_schema(x) for x in node]
    if not isinstance(node, dict):
        return node
    node = {k: v for k, v in node.items() if k not in ("title", "$schema") and not (k == "default" and v is None)}
    for key in ("anyOf", "oneOf"):
        opts = [o for o in node.pop(key, []) or [] if not (isinstance(o, dict) and o.get("type") == "null")]
        if opts:
            node = {**_clean_schema(opts[0]), **{k: v for k, v in node.items()}}
    if "properties" in node or node.get("type") == "object":
        node["type"] = "object"
        node["properties"] = {k: _clean_schema(v) for k, v in (node.get("properties") or {}).items()}
    if "items" in node:
        node["items"] = _clean_schema(node["items"])
    if "type" not in node and "enum" not in node:
        node["type"] = "string"
    return node


def _mcp_tools() -> list:
    import server  # las mismas tools que ven los agentes, incluidas las de conexiones

    return asyncio.run(server.mcp.list_tools())


def _native_tools(mcp_tools: list) -> list[dict]:
    return [{"type": "function", "function": {"name": t.name, "description": (t.description or "").strip(),
                                              "parameters": _clean_schema(t.input_schema or {})}}
            for t in mcp_tools]


def _text_tools_prompt(mcp_tools: list) -> str:
    lines = []
    for t in mcp_tools:
        sch = _clean_schema(t.input_schema or {})
        req = set(sch.get("required") or [])
        args = ", ".join(f"{k}{'' if k in req else '?'}: {v.get('type', 'string')}" for k, v in sch["properties"].items())
        desc = (t.description or "").strip().splitlines()[0] if t.description else ""
        lines.append(f"- {t.name}({args}): {desc}")
    return (
        "# Tools\nPodés usar estas tools. Para llamar una, escribí SOLO un bloque así (podés poner varios seguidos):\n"
        '<tool_call>{"name": "read_file", "arguments": {"path": "BRAIN.md"}}</tool_call>\n'
        "Después vas a recibir los resultados en un mensaje que empieza con 'Resultados de las tools'. "
        "No inventes resultados: esperá a recibirlos. Cuando no necesites más tools, respondé normalmente.\n"
        + "\n".join(lines)
    )


def _parse_text_calls(content: str) -> tuple[list[dict], str]:
    """Busca pedidos de tools escritos en el texto. Devuelve (calls, texto sin esos bloques)."""
    calls, spans = [], []
    for rx in (TOOL_TAG, FENCED):
        for m in rx.finditer(content):
            try:
                obj = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            for o in obj if isinstance(obj, list) else [obj]:
                if isinstance(o, dict) and isinstance(o.get("name"), str):
                    args = o.get("arguments", o.get("parameters", {}))
                    calls.append({"function": {"name": o["name"], "arguments": args}})
                    spans.append(m.span())
        if calls:
            break
    clean = content
    for a, b in sorted(set(spans), reverse=True):
        clean = clean[:a] + clean[b:]
    return calls, clean.strip()


def _run_tool(name: str, args: dict, valid: set[str]) -> tuple[str, bool]:
    import server

    if name not in valid:
        return (f"Error: la tool '{name}' no existe. Tools disponibles: {', '.join(sorted(valid))}.", False)
    try:
        r = asyncio.run(server.mcp.call_tool(name, args or {}))
    except Exception as e:
        return f"Error: {e}", False
    texts = [getattr(c, "text", "") for c in (getattr(r, "content", None) or []) if getattr(c, "text", None)]
    out = "\n".join(texts)
    if not out and getattr(r, "structured_content", None) is not None:
        out = json.dumps(r.structured_content, ensure_ascii=False, default=str)
    ok = not getattr(r, "is_error", False) and not out.lstrip().startswith("Error")
    return (out or "OK")[:MAX_TOOL_RESULT], ok


# ---------- conversación ----------

def _stream(model: str, messages: list[dict], tools: list[dict] | None, num_ctx: int = 0):
    """Llama a /api/chat con stream. Devuelve un iterador de chunks (dicts)."""
    body = {"model": model, "messages": messages, "stream": True}
    if num_ctx:
        body["options"] = {"num_ctx": num_ctx}
    if tools:
        body["tools"] = tools
    try:
        r = requests.post(f"{OLLAMA}/api/chat", json=body, stream=True, timeout=(5, 600))
    except requests.ConnectionError as e:
        raise ChatError("ollama") from e
    if r.status_code != 200:
        text = r.text[:600]
        low = text.lower()
        if tools and any(w in low for w in ("tool", "template", "schema", "parser", "properties")):
            raise ChatError("no_tools", text)
        if r.status_code == 404 or "not found" in low:
            raise ChatError("no_model", model)
        raise ChatError("ollama_http", f"HTTP {r.status_code}: {text}")
    for line in r.iter_lines():
        if line:
            chunk = json.loads(line)
            if chunk.get("error"):
                raise ChatError("ollama_http", chunk["error"])
            yield chunk


def _new_id() -> str:
    return time.strftime("c%Y%m%d%H%M%S") + secrets.token_hex(2)


class _TagFilter:
    """Deja pasar el texto del stream pero se guarda los bloques <tool_call>…</tool_call>
    (no tienen que aparecer en la respuesta que ve el usuario)."""
    OPEN, CLOSE = "<tool_call>", "</tool_call>"

    def __init__(self):
        self.buf, self.inside = "", False

    def feed(self, text: str) -> str:
        self.buf += text
        out = ""
        while self.buf:
            if self.inside:
                j = self.buf.find(self.CLOSE)
                if j < 0:
                    return out
                self.buf, self.inside = self.buf[j + len(self.CLOSE):], False
                continue
            j = self.buf.find(self.OPEN)
            if j >= 0:
                out += self.buf[:j]
                self.buf, self.inside = self.buf[j + len(self.OPEN):], True
                continue
            # retener un posible comienzo de "<tool_call>" partido entre chunks
            k = self.buf.rfind("<")
            if k >= 0 and self.OPEN.startswith(self.buf[k:]):
                out, self.buf = out + self.buf[:k], self.buf[k:]
                return out
            out, self.buf = out + self.buf, ""
        return out

    def flush(self) -> str:
        rest = "" if self.inside else self.buf
        self.buf, self.inside = "", False
        return rest


def _compact(chat_id, title, model, created, history, summary, recent, user_msg, num_ctx, win, system, native,
             saved_ok, command):
    """Resume la conversación (con streaming) y la guarda como un mensaje `compact`."""
    if not recent:
        yield {"type": "error", "code": "nothing_to_compact"}
        yield {"type": "done", "chat_id": chat_id, "title": title, "saved": None}
        return
    # si la conversación no entra en la ventana, se manda lo más nuevo (~3 caracteres por token de margen)
    text = _transcript(summary, recent)[-max(4000, num_ctx * 3):]
    msgs = [{"role": "system", "content": COMPACT_PROMPT}, {"role": "user", "content": text}]
    answer, failed = "", False
    try:
        for chunk in _stream(model, msgs, None, num_ctx):
            piece = (chunk.get("message") or {}).get("content") or ""
            if piece:
                answer += piece
                yield {"type": "token", "text": piece}
    except ChatError as e:
        failed = True
        yield {"type": "error", "code": e.code, "detail": e.detail}
    except Exception as e:
        failed = True
        log.exception("compact")
        yield {"type": "error", "code": "other", "detail": str(e)}
    answer = answer.strip()
    # lo que va a ocupar el próximo pedido: system + tools + el resumen
    tokens = (len(system) + len(answer) + (len(json.dumps(native)) if native else 0)) // 4
    if answer and not failed:
        yield {"type": "usage", "tokens": tokens, "ctx": num_ctx, "max": win["max"]}
        try:
            if not saved_ok:
                raise RuntimeError("historial no disponible")
            _save(chat_id, title, model, created,
                  [user_msg, {"role": "assistant", "content": answer, "ts": _now(), "compact": True}], len(history),
                  {"command": command, "tokens": tokens, "ctx": num_ctx})
        except Exception as e:
            log.info("no se pudo guardar el resumen: %s", e)
            saved_ok = False
    else:
        saved_ok = None
    yield {"type": "done", "chat_id": chat_id, "title": title, "saved": saved_ok}


def run(text: str, model: str, chat_id: str | None = None, lang: str = "es", user_system: str = ""):
    """Genera los eventos de una respuesta (para mandar como NDJSON):
    start · token · rewrite · tool · tool_result · usage · notice · done · error."""
    text = (text or "").strip()
    if not text:
        yield {"type": "error", "code": "empty"}
        return
    if not model:
        yield {"type": "error", "code": "no_model"}
        return

    _names: list = []

    def names() -> set[str]:  # los skills se listan una sola vez por mensaje, y solo si hace falta
        if not _names:
            _names.append({sk["name"] for sk in skills()})
        return _names[0]

    cmd, rest = parse_command(text, names() if text.lstrip().startswith("/") else set())
    title = (CMD_TITLES[lang if lang in ("es", "en") else "es"].get(cmd) if cmd else None) \
        or (f"Skill: {cmd[6:]}" if cmd.startswith("skill:") else None) or text.splitlines()[0][:60]
    mode_cmd = "" if cmd == "compact" else cmd  # /compact no deja el chat en un modo
    history, created, saved_ok, command = [], _now(), True, mode_cmd
    if chat_id:
        try:
            prev = get_chat(chat_id)
            history, title = prev["messages"], prev["title"] or title
            created = prev.get("created_at") or created
            command = mode_cmd or prev.get("command") or ""
        except ChatError:
            pass  # id desconocido: se arranca un chat nuevo con ese id
        except Exception as e:
            log.info("no se pudo leer el chat %s: %s", chat_id, e)
            saved_ok = False
    chat_id = chat_id or _new_id()
    yield {"type": "start", "chat_id": chat_id, "title": title, "command": command}

    user_msg = {"role": "user", "content": text, "ts": _now()}
    # lo que ve el modelo: un comando solo ("/organize") se traduce a un pedido en palabras
    to_model = _command_request(cmd, rest) if cmd else text
    try:
        system = _context(text, chat_id, lang, user_system, command)
    except Exception as e:
        log.exception("contexto del chat")
        system = f"(no se pudo cargar la memoria del usuario: {e})"
    win = context_window(model)
    num_ctx = win["ctx"]

    try:
        mcp_tools = _mcp_tools()
    except Exception as e:
        log.warning("sin tools para el chat: %s", e)
        mcp_tools = []
    valid = {t.name for t in mcp_tools}
    mode = _MODE.get(model, "native") if mcp_tools else "none"

    summary, recent = _split_compact(history)

    def build(mode: str) -> list[dict]:
        sys_ = system + ("\n\n" + _text_tools_prompt(mcp_tools) if mode == "text" else "")
        if summary:
            sys_ += "\n\n# Resumen de la conversación hasta ahora (se compactó con /compact)\n" + summary
        ms = [{"role": "system", "content": sys_}]
        for m in recent[-MAX_HISTORY:]:
            c = m["content"]
            if m["role"] == "user" and c.lstrip().startswith("/"):
                pc = parse_command(c, names())
                c = _command_request(*pc) if pc[0] else c
            ms.append({"role": m["role"], "content": c})
        ms.append({"role": "user", "content": to_model})
        return ms

    tokens = 0

    def estimate(msgs: list[dict], native: list | None) -> int:
        # ~4 caracteres por token: alcanza para el indicador y cubre el caso en que Ollama reusa el caché
        # y reporta menos tokens de los que ocupa la conversación
        chars = sum(len(str(m.get("content") or "")) for m in msgs) + (len(json.dumps(native)) if native else 0)
        return chars // 4

    if cmd == "compact":
        yield from _compact(chat_id, title, model, created, history, summary, recent, user_msg, num_ctx, win,
                            system, _native_tools(mcp_tools) if mode == "native" else None, saved_ok, command)
        return

    msgs = build(mode)
    answer, used, failed = "", [], False
    seen_calls, repeats, out_of_rounds = set(), 0, False
    try:
        rounds = 0
        while rounds < MAX_ROUNDS and repeats < 3:
            rounds += 1
            content, calls, flt, round_start = "", [], _TagFilter(), len(answer)
            native = _native_tools(mcp_tools) if mode == "native" else None
            try:
                for chunk in _stream(model, msgs, native, num_ctx):
                    if chunk.get("done"):
                        used_now = int(chunk.get("prompt_eval_count") or 0) + int(chunk.get("eval_count") or 0)
                        tokens = max(used_now, estimate(msgs, native) + len(content) // 4)
                        yield {"type": "usage", "tokens": tokens, "ctx": num_ctx, "max": win["max"]}
                    m = chunk.get("message") or {}
                    if m.get("content"):
                        content += m["content"]
                        shown = flt.feed(m["content"])
                        if shown:
                            answer += shown
                            yield {"type": "token", "text": shown}
                    calls += m.get("tool_calls") or []
                tail = flt.flush()
                if tail:
                    answer += tail
                    yield {"type": "token", "text": tail}
            except ChatError as e:
                if e.code == "no_tools" and mode == "native":
                    # el modelo o su template no acepta tools nativas: se pasa al modo texto
                    log.info("modo texto para %s: %s", model, e.detail[:200])
                    _MODE[model] = mode = "text"
                    msgs = build(mode)
                    rounds -= 1
                    yield {"type": "notice", "code": "text_tools"}
                    continue
                raise
            if not calls and mcp_tools:
                calls, _ = _parse_text_calls(content)
                shown = answer[round_start:]
                _, visible = _parse_text_calls(shown)
                if calls and visible != shown.strip():
                    # el pedido vino como bloque ```json``` que el usuario ya vio: se saca de la respuesta
                    answer = answer[:round_start] + visible
                    yield {"type": "rewrite", "text": answer}
            if not calls:
                break
            if mode == "native":
                msgs.append({"role": "assistant", "content": content, "tool_calls": calls})
            else:
                msgs.append({"role": "assistant", "content": content})
            results = []
            for call in calls:
                fn = call.get("function") or {}
                name, args = str(fn.get("name", "")), fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                if not isinstance(args, dict):
                    args = {}
                idx = len(used)
                yield {"type": "tool", "i": idx, "name": name, "args": args}
                key = name + json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
                if key in seen_calls:  # el modelo da vueltas: no se ejecuta otra vez
                    result, ok = REPEAT_NOTE.format(name=name), False
                    repeats += 1
                else:
                    seen_calls.add(key)
                    result, ok = _run_tool(name, args, valid)
                used.append({"name": name, "args": args, "ok": ok, "result": result[:1500]})
                yield {"type": "tool_result", "i": idx, "name": name, "ok": ok, "result": result[:1500]}
                if mode == "native":
                    msgs.append({"role": "tool", "content": result, "tool_name": name})
                else:
                    results.append(f'<tool_result name="{name}">\n{result}\n</tool_result>')
            if results:
                msgs.append({"role": "user", "content": "Resultados de las tools:\n" + "\n".join(results)})
            if answer and not answer.endswith("\n"):
                answer += "\n\n"
                yield {"type": "token", "text": "\n\n"}
        else:
            out_of_rounds = True
        if out_of_rounds or (used and not answer.strip()):
            # se acabaron las vueltas (o el modelo usó tools y no dijo nada): una vuelta más, sin tools, para
            # que el usuario reciba una respuesta en vez de un corte
            if out_of_rounds:
                yield {"type": "notice", "code": "max_rounds"}
            msgs.append({"role": "user", "content": FINAL_NUDGE})
            flt = _TagFilter()
            for chunk in _stream(model, msgs, None, num_ctx):
                if chunk.get("done"):
                    tokens = max(tokens, estimate(msgs, None))
                    yield {"type": "usage", "tokens": tokens, "ctx": num_ctx, "max": win["max"]}
                piece = (chunk.get("message") or {}).get("content") or ""
                shown = flt.feed(piece) if piece else ""
                if shown:
                    answer += shown
                    yield {"type": "token", "text": shown}
            tail = flt.flush()
            if tail:
                answer += tail
                yield {"type": "token", "text": tail}
    except ChatError as e:
        failed = True
        yield {"type": "error", "code": e.code, "detail": e.detail}
    except OllamaUnavailable as e:
        failed = True
        yield {"type": "error", "code": "ollama", "detail": str(e)}
    except Exception as e:
        failed = True
        log.exception("chat")
        yield {"type": "error", "code": "other", "detail": str(e)}
    finally:
        # se guarda también lo parcial (si el usuario cortó la respuesta o hubo un error)
        new = [user_msg]
        if answer.strip() or used:
            new.append({"role": "assistant", "content": answer.strip(), "ts": _now(), "tools": used})
        try:
            if failed and len(new) == 1:
                saved_ok = None  # no hubo respuesta: no se guarda un mensaje suelto
            elif saved_ok:
                _save(chat_id, title, model, created, new, len(history),
                      {"command": command, "tokens": tokens, "ctx": num_ctx})
            else:
                raise RuntimeError("historial no disponible")
        except Exception as e:
            log.info("no se pudo guardar el chat: %s", e)
            saved_ok = False
    yield {"type": "done", "chat_id": chat_id, "title": title, "saved": saved_ok}
