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
# corta en silencio el system prompt con la memoria. Se usa el máximo del modelo, con este tope para no
# pedir más RAM de la que tiene una Mac común (el caché de 128k tokens de un modelo grande no entra).
CHAT_CTX = int(os.environ.get("BRAIN_CHAT_CTX", "16384"))
DEFAULT_CTX = 4096       # si Ollama no dice el máximo del modelo

# Comandos del chat (/organize…): agregan instrucciones al system prompt de ese chat y se recuerdan
# en el chat (meta "command"), así las respuestas del usuario a las preguntas siguen en el mismo modo.
COMMANDS = {
    "organize": "Ayudame a ordenar mi vault y su grafo.",
    "reflect": "Revisá mi memoria y lo que guardé: duplicados, contradicciones y notas sin entidades.",
}
CMD_TITLES = {"es": {"organize": "Ordenar el vault", "reflect": "Revisar la memoria"},
              "en": {"organize": "Organize the vault", "reflect": "Review my memory"}}


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
    out = []
    for m in r.json().get("models", []):
        if _is_embedding(m):
            continue
        ctx = context_window(m["name"], m.get("digest", ""))
        out.append({"name": m["name"], "size": m.get("size", 0), "params": (m.get("details") or {}).get("parameter_size", ""),
                    "ctx": ctx["ctx"], "ctx_max": ctx["max"]})
    return sorted(out, key=lambda m: m["name"])


_CTX: dict[str, dict] = {}  # "modelo@digest" → {"ctx", "max"}


def context_window(model: str, digest: str = "") -> dict:
    """Ventana de contexto de un modelo: {"max": lo que soporta, "ctx": lo que usa brain (num_ctx)}.
    Sale de /api/show (model_info.<arch>.context_length y el num_ctx del Modelfile, si lo tiene)."""
    key = f"{model}@{digest}"
    if key in _CTX:
        return _CTX[key]
    mx, fixed = 0, 0
    try:
        r = requests.post(f"{OLLAMA}/api/show", json={"model": model}, timeout=5)
        if r.ok:
            j = r.json()
            mx = next((int(v) for k, v in (j.get("model_info") or {}).items() if k.endswith(".context_length") and v), 0)
            m = re.search(r"^num_ctx\s+(\d+)", j.get("parameters") or "", re.M)
            fixed = int(m.group(1)) if m else 0
    except (requests.RequestException, ValueError):
        pass
    ctx = fixed or min(mx or DEFAULT_CTX, CHAT_CTX)
    out = {"max": mx or ctx, "ctx": min(ctx, mx) if mx else ctx}
    if mx:  # sin respuesta de Ollama no se cachea: se vuelve a preguntar
        _CTX[key] = out
    return out


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
                         "tools": json.loads(m.get("tools") or "[]")})
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
                      "tools": json.dumps(m.get("tools") or [], ensure_ascii=False)})
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

def parse_command(text: str) -> tuple[str, str]:
    """"/organize y los proyectos" → ("organize", "y los proyectos"). Sin comando: ("", text)."""
    m = re.match(r"^/([a-z]+)\b\s*(.*)$", text.strip(), re.S)
    if m and m.group(1) in COMMANDS:
        return m.group(1), m.group(2).strip()
    return "", text


def _command_prompt(cmd: str) -> str:
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
        "Sos el asistente personal del usuario dentro de brain, su base de conocimiento local. "
        "Conocés al usuario por su perfil y su memoria, que están abajo: usalos para personalizar cada respuesta. "
        "Tenés tools para leer, buscar, crear y editar notas del vault, guardar memorias nuevas (add_memory) y "
        "buscar en lo guardado (search_knowledge). Usalas cuando el usuario pida consultar, agregar o cambiar algo. "
        "Reglas para cambiar cosas: 1) antes de decir que algo 'ya está', leelo con read_file o list_vault; "
        "2) para cambiar un metadato (description, tags, related, name) usá set_frontmatter, no reescribas el archivo; "
        "3) para cambiar texto usá str_replace_file o write_file; 4) nunca digas que hiciste un cambio si la tool no "
        "respondió 'OK'; si respondió 'Error', contá el error y probá de otra forma. "
        "Después de actuar, contá en una línea qué hiciste. Cada cambio queda en el historial y se puede deshacer con "
        "file_history + restore_file. Si el usuario te cuenta algo duradero sobre sí mismo, guardalo con add_memory. "
        "No inventes datos: si algo no está en el contexto ni en el vault, decilo. "
        f"{reply_lang}, salvo que el usuario escriba en otro idioma.",
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

    cmd, rest = parse_command(text)
    title = (CMD_TITLES[lang if lang in ("es", "en") else "es"].get(cmd) if cmd else None) or text.splitlines()[0][:60]
    history, created, saved_ok, command = [], _now(), True, cmd
    if chat_id:
        try:
            prev = get_chat(chat_id)
            history, title = prev["messages"], prev["title"] or title
            created = prev.get("created_at") or created
            command = cmd or prev.get("command") or ""
        except ChatError:
            pass  # id desconocido: se arranca un chat nuevo con ese id
        except Exception as e:
            log.info("no se pudo leer el chat %s: %s", chat_id, e)
            saved_ok = False
    chat_id = chat_id or _new_id()
    yield {"type": "start", "chat_id": chat_id, "title": title, "command": command}

    user_msg = {"role": "user", "content": text, "ts": _now()}
    # lo que ve el modelo: un comando solo ("/organize") se traduce a un pedido en palabras
    to_model = (COMMANDS[cmd] + (f" {rest}" if rest else "")) if cmd else text
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

    def build(mode: str) -> list[dict]:
        sys_ = system + ("\n\n" + _text_tools_prompt(mcp_tools) if mode == "text" else "")
        ms = [{"role": "system", "content": sys_}]
        ms += [{"role": m["role"], "content": (COMMANDS[parse_command(m["content"])[0]] if m["role"] == "user"
                                               and parse_command(m["content"])[0] else m["content"])}
               for m in history[-MAX_HISTORY:]]
        ms.append({"role": "user", "content": to_model})
        return ms

    tokens = 0

    def estimate(msgs: list[dict], native: list | None) -> int:
        # ~4 caracteres por token: alcanza para el indicador y cubre el caso en que Ollama reusa el caché
        # y reporta menos tokens de los que ocupa la conversación
        chars = sum(len(str(m.get("content") or "")) for m in msgs) + (len(json.dumps(native)) if native else 0)
        return chars // 4

    msgs = build(mode)
    answer, used, failed = "", [], False
    try:
        rounds = 0
        while rounds < MAX_ROUNDS:
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
            yield {"type": "notice", "code": "max_rounds"}
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
