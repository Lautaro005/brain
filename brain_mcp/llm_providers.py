"""Proveedores de chat local: Ollama y LM Studio (API OpenAI-compatible)."""
import json
import logging
import re
from typing import Iterator

import requests

from . import lmstudio

log = logging.getLogger(__name__)

OLLAMA = "http://localhost:11434"
PROVIDER_OLLAMA = "ollama"
PROVIDER_LMSTUDIO = "lmstudio"
LABELS = {PROVIDER_OLLAMA: "Ollama", PROVIDER_LMSTUDIO: "LM Studio"}


def parse_ref(ref: str) -> tuple[str, str]:
    ref = (ref or "").strip()
    if ref.startswith(f"{PROVIDER_LMSTUDIO}:"):
        return PROVIDER_LMSTUDIO, ref.split(":", 1)[1]
    if ref.startswith(f"{PROVIDER_OLLAMA}:"):
        return PROVIDER_OLLAMA, ref.split(":", 1)[1]
    return PROVIDER_OLLAMA, ref


def canonical(provider: str, model_id: str) -> str:
    return f"{provider}:{model_id}"


def display_label(provider: str, model_id: str) -> str:
    short = model_id.replace(":latest", "")
    return f"{LABELS.get(provider, provider)} · {short}"


def _is_embedding(m: dict) -> bool:
    d = m.get("details") or {}
    fam = " ".join([str(d.get("family") or "")] + list(d.get("families") or []))
    name = m.get("name", "")
    return "embed" in name.lower() or "bert" in fam.lower()


def ollama_tags() -> list[dict]:
    r = requests.get(f"{OLLAMA}/api/tags", timeout=5)
    r.raise_for_status()
    return r.json().get("models") or []


def ollama_healthy() -> bool:
    try:
        r = requests.get(f"{OLLAMA}/api/version", timeout=1)
        return r.status_code == 200
    except requests.RequestException:
        return False


def list_chat_models() -> list[dict]:
    """Modelos para el chat: name = proveedor:id, label para la UI, provider, ctx…"""
    out: list[dict] = []
    if ollama_healthy():
        try:
            from . import chat

            s = chat.ctx_settings()
            for m in ollama_tags():
                if _is_embedding(m):
                    continue
                mid = m["name"]
                ref = canonical(PROVIDER_OLLAMA, mid)
                win = chat.context_window(ref, m.get("digest", ""), s)
                out.append({
                    "provider": PROVIDER_OLLAMA,
                    "name": ref,
                    "label": display_label(PROVIDER_OLLAMA, mid),
                    "size": m.get("size", 0),
                    "params": (m.get("details") or {}).get("parameter_size", ""),
                    "ctx": win["ctx"],
                    "ctx_max": win["max"],
                    "ctx_source": win["source"],
                    "digest": m.get("digest", ""),
                })
        except Exception as e:
            log.info("no se listaron modelos de Ollama: %s", e)
    if lmstudio.base_url():
        try:
            from . import chat

            s = chat.ctx_settings()
            for m in lmstudio.list_models():
                ref = canonical(PROVIDER_LMSTUDIO, m["id"])
                win = chat.context_window(ref, "", s)
                mx = m.get("ctx_max") or win["max"]
                out.append({
                    "provider": PROVIDER_LMSTUDIO,
                    "name": ref,
                    "label": display_label(PROVIDER_LMSTUDIO, m["id"]),
                    "size": 0,
                    "params": "",
                    "ctx": win["ctx"],
                    "ctx_max": mx,
                    "ctx_source": win["source"],
                    "digest": "",
                })
        except Exception as e:
            log.info("no se listaron modelos de LM Studio: %s", e)
    return sorted(out, key=lambda x: (x["provider"], x["name"]))


def model_max(provider: str, model_id: str, digest: str = "") -> int:
    if provider == PROVIDER_LMSTUDIO:
        return lmstudio.DEFAULT_CTX
    from . import chat

    return chat._model_max(model_id, digest)  # noqa: SLF001 — mismo cálculo que antes


def stream_chat(ref: str, messages: list[dict], tools: list[dict] | None, num_ctx: int = 0,
                options: dict | None = None) -> Iterator[dict]:
    provider, model_id = parse_ref(ref)
    if provider == PROVIDER_LMSTUDIO:
        yield from _stream_openai(model_id, messages, tools, num_ctx, options)
    else:
        yield from _stream_ollama(model_id, messages, tools, num_ctx, options)


def _stream_ollama(model: str, messages: list[dict], tools: list[dict] | None, num_ctx: int,
                   options: dict | None) -> Iterator[dict]:
    from .chat import ChatError, KEEP_ALIVE

    body = {"model": model, "messages": messages, "stream": True, "keep_alive": KEEP_ALIVE}
    opts = {**({"num_ctx": num_ctx} if num_ctx else {}), **(options or {})}
    if opts:
        body["options"] = opts
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


def _openai_messages(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({"role": "tool", "content": m.get("content", ""), "tool_call_id": m.get("tool_name", "call")})
            continue
        msg = {"role": role, "content": m.get("content") or ""}
        if m.get("tool_calls"):
            tcs = []
            for i, tc in enumerate(m["tool_calls"]):
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, dict):
                    args = json.dumps(args, ensure_ascii=False)
                tcs.append({
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {"name": fn.get("name", ""), "arguments": args or "{}"},
                })
            msg["tool_calls"] = tcs
        out.append(msg)
    return out


def _stream_openai(model: str, messages: list[dict], tools: list[dict] | None, num_ctx: int,
                   options: dict | None) -> Iterator[dict]:
    from .chat import ChatError

    base = lmstudio.base_url()
    if not base:
        raise ChatError("lmstudio_off")
    body = {
        "model": model,
        "messages": _openai_messages(messages),
        "stream": True,
        "temperature": (options or {}).get("temperature", 0.7),
    }
    if num_ctx:
        body["max_tokens"] = min(4096, max(256, num_ctx // 4))
    if tools:
        body["tools"] = tools
    try:
        r = requests.post(f"{base}/v1/chat/completions", json=body, stream=True, timeout=(5, 600))
    except requests.ConnectionError as e:
        raise ChatError("lmstudio_down") from e
    if r.status_code != 200:
        text = r.text[:600]
        low = text.lower()
        if tools and any(w in low for w in ("tool", "function")):
            raise ChatError("no_tools", text)
        if r.status_code == 404 or "not found" in low:
            raise ChatError("no_model", model)
        raise ChatError("lmstudio_http", f"HTTP {r.status_code}: {text}")

    tool_buf: list[dict] = []
    content_acc = ""
    for raw in r.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data:"):
            continue
        payload = raw[5:].strip()
        if payload == "[DONE]":
            yield {"done": True, "message": {"content": ""}}
            break
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choice = (data.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        piece = delta.get("content") or ""
        if piece:
            content_acc += piece
            yield {"message": {"content": piece}}
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            while len(tool_buf) <= idx:
                tool_buf.append({"function": {"name": "", "arguments": ""}})
            fn = tc.get("function") or {}
            if fn.get("name"):
                tool_buf[idx]["function"]["name"] = fn["name"]
            if fn.get("arguments"):
                tool_buf[idx]["function"]["arguments"] += fn["arguments"]
        if choice.get("finish_reason"):
            calls = []
            for tc in tool_buf:
                name = (tc.get("function") or {}).get("name", "")
                if not name:
                    continue
                args_raw = (tc.get("function") or {}).get("arguments") or "{}"
                try:
                    args = json.loads(args_raw)
                except json.JSONDecodeError:
                    args = {}
                calls.append({"function": {"name": name, "arguments": args}})
            if calls:
                yield {"message": {"content": "", "tool_calls": calls}}
            yield {"done": True, "message": {"content": ""}, "eval_count": len(content_acc) // 4}
            return
