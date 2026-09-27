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
import secrets
import time
from datetime import datetime, timezone

import requests

from . import chroma_store, memory, vault
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
    out = [{"name": m["name"], "size": m.get("size", 0), "params": (m.get("details") or {}).get("parameter_size", "")}
           for m in r.json().get("models", []) if not _is_embedding(m)]
    return sorted(out, key=lambda m: m["name"])


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
            "created_at": m.get("created_at"), "count": m.get("count", 0), "model": m.get("model", "")}
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
            "created_at": meta.get("created_at"), "messages": msgs}


def delete_chat(chat_id: str) -> None:
    _call(lambda c: c.delete(where={"chat_id": chat_id}))


def _save(chat_id: str, title: str, model: str, created_at: str, new: list[dict], start_idx: int) -> None:
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
                  "updated_at": _now(), "count": start_idx + len(new)})
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

def _context(text: str, chat_id: str, lang: str) -> str:
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
        "buscar en lo guardado (search_knowledge). Usalas cuando el usuario pida consultar, agregar o cambiar algo, "
        "y contá en una línea qué hiciste. Cada cambio queda en el historial y se puede deshacer con file_history + "
        "restore_file. Si el usuario te cuenta algo duradero sobre sí mismo, guardalo con add_memory. "
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
    parts.append(f"Fecha de hoy: {datetime.now().strftime('%Y-%m-%d')}")
    return "\n\n".join(parts)


# ---------- tools ----------

def _tools() -> list[dict]:
    import server  # las mismas tools que ven los agentes, incluidas las de conexiones

    return [{"type": "function", "function": {"name": t.name, "description": t.description or "",
                                              "parameters": t.input_schema}}
            for t in asyncio.run(server.mcp.list_tools())]


def _run_tool(name: str, args: dict) -> tuple[str, bool]:
    import server

    try:
        r = asyncio.run(server.mcp.call_tool(name, args or {}))
    except Exception as e:
        return f"Error: {e}", False
    texts = [getattr(c, "text", "") for c in (getattr(r, "content", None) or []) if getattr(c, "text", None)]
    out = "\n".join(texts)
    if not out and getattr(r, "structured_content", None) is not None:
        out = json.dumps(r.structured_content, ensure_ascii=False, default=str)
    ok = not getattr(r, "is_error", False) and not out.startswith("Error")
    return (out or "OK")[:MAX_TOOL_RESULT], ok


# ---------- conversación ----------

def _stream(model: str, messages: list[dict], tools: list[dict] | None):
    """Llama a /api/chat con stream. Devuelve un iterador de chunks (dicts)."""
    body = {"model": model, "messages": messages, "stream": True}
    if tools:
        body["tools"] = tools
    try:
        r = requests.post(f"{OLLAMA}/api/chat", json=body, stream=True, timeout=(5, 600))
    except requests.ConnectionError as e:
        raise ChatError("ollama") from e
    if r.status_code != 200:
        text = r.text[:400]
        if "support" in text.lower() and "tool" in text.lower():
            raise ChatError("no_tools", text)
        if r.status_code == 404 or "not found" in text.lower():
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


def run(text: str, model: str, chat_id: str | None = None, lang: str = "es"):
    """Genera los eventos de una respuesta (para mandar como NDJSON):
    start · token · tool · tool_result · notice · done · error."""
    text = (text or "").strip()
    if not text:
        yield {"type": "error", "code": "empty"}
        return
    if not model:
        yield {"type": "error", "code": "no_model"}
        return

    history, title, created, saved_ok = [], text.splitlines()[0][:60], _now(), True
    if chat_id:
        try:
            prev = get_chat(chat_id)
            history, title = prev["messages"], prev["title"] or title
            created = prev.get("created_at") or created
        except ChatError:
            pass  # id desconocido: se arranca un chat nuevo con ese id
        except Exception as e:
            log.info("no se pudo leer el chat %s: %s", chat_id, e)
            saved_ok = False
    chat_id = chat_id or _new_id()
    yield {"type": "start", "chat_id": chat_id, "title": title}

    user_msg = {"role": "user", "content": text, "ts": _now()}
    try:
        system = _context(text, chat_id, lang)
    except Exception as e:
        log.exception("contexto del chat")
        system = f"(no se pudo cargar la memoria del usuario: {e})"
    msgs = [{"role": "system", "content": system}]
    msgs += [{"role": m["role"], "content": m["content"]} for m in history[-MAX_HISTORY:]]
    msgs.append({"role": "user", "content": user_msg["content"]})

    try:
        tools = _tools()
    except Exception as e:
        log.warning("sin tools para el chat: %s", e)
        tools = None

    answer, used, failed = "", [], False
    try:
        for _ in range(MAX_ROUNDS):
            content, calls = "", []
            try:
                for chunk in _stream(model, msgs, tools):
                    m = chunk.get("message") or {}
                    if m.get("content"):
                        content += m["content"]
                        yield {"type": "token", "text": m["content"]}
                    calls += m.get("tool_calls") or []
            except ChatError as e:
                if e.code == "no_tools" and tools:
                    tools = None
                    yield {"type": "notice", "code": "no_tools"}
                    continue
                raise
            answer += content
            if not calls:
                break
            msgs.append({"role": "assistant", "content": content, "tool_calls": calls})
            for call in calls:
                fn = call.get("function") or {}
                name, args = fn.get("name", ""), fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                yield {"type": "tool", "name": name, "args": args}
                result, ok = _run_tool(name, args)
                used.append({"name": name, "args": args, "ok": ok})
                yield {"type": "tool_result", "name": name, "ok": ok, "preview": result[:300]}
                msgs.append({"role": "tool", "content": result, "tool_name": name})
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
                _save(chat_id, title, model, created, new, len(history))
            else:
                raise RuntimeError("historial no disponible")
        except Exception as e:
            log.info("no se pudo guardar el chat: %s", e)
            saved_ok = False
    yield {"type": "done", "chat_id": chat_id, "title": title, "saved": saved_ok}
