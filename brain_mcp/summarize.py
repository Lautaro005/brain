"""Resúmenes cortos (campo `abstract`) con el modelo de chat local de Ollama.

Best-effort: si Ollama no responde o no hay un modelo de chat instalado, devuelve None y quien
llama sigue igual (la fuente se guarda sin abstract). Nunca levanta excepción.

Ojo: esto usa un modelo de *generación* (ej. llama3.2), distinto de nomic-embed-text, que solo
calcula embeddings y no puede escribir texto. Hace falta `ollama pull llama3.2` (o el que se
configure en BRAIN_SUMMARY_MODEL). BRAIN_SUMMARY_MODEL=off apaga resúmenes y entidades.
"""
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

OLLAMA = "http://localhost:11434"
DEFAULT_MODEL = "llama3.2"
TIMEOUT = float(os.environ.get("BRAIN_LLM_TIMEOUT", "45"))
MAX_INPUT_CHARS = 12000  # el principio del texto alcanza para un resumen y no satura un modelo chico
PROMPT = ("Resumí este texto en un párrafo de no más de {max_words} palabras, en el mismo idioma del "
          "texto. Solo el resumen, sin introducción.\n\nTexto:\n{text}")

_model_cache: dict = {"at": 0.0, "value": None}


def _is_embedding(m: dict) -> bool:
    d = m.get("details") or {}
    fam = " ".join([str(d.get("family") or "")] + list(d.get("families") or []))
    return "embed" in m.get("name", "").lower() or "bert" in fam.lower()


def model() -> str | None:
    """Modelo de chat a usar: BRAIN_SUMMARY_MODEL si está, si no llama3.2 si está instalado, si no
    el primer modelo de chat instalado. None si no hay ninguno (o Ollama no responde)."""
    env = os.environ.get("BRAIN_SUMMARY_MODEL", "").strip()
    if env.lower() in ("off", "0", "none", "false"):
        return None
    if env:
        return env
    if time.time() - _model_cache["at"] < 60:
        return _model_cache["value"]
    value = None
    try:
        r = requests.get(f"{OLLAMA}/api/tags", timeout=3)
        r.raise_for_status()
        names = [m["name"] for m in r.json().get("models", []) if not _is_embedding(m)]
        value = next((n for n in names if n.split(":")[0] == DEFAULT_MODEL), None) or (names[0] if names else None)
    except Exception:
        value = None
    _model_cache.update(at=time.time(), value=value)
    return value


def generate(prompt: str, fmt: str | None = None) -> str | None:
    """Una respuesta del modelo de chat local, o None si no se pudo. fmt="json" pide JSON."""
    name = model()
    if not name:
        return None
    body = {"model": name, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}}
    if fmt:
        body["format"] = fmt
    try:
        r = requests.post(f"{OLLAMA}/api/generate", json=body, timeout=(3, TIMEOUT))
        r.raise_for_status()
        return (r.json().get("response") or "").strip() or None
    except Exception as e:
        log.info("Ollama no generó respuesta (%s): %s", name, e)
        return None


def summarize(text: str, max_words: int = 120) -> str | None:
    """Resumen corto con el modelo de chat local de Ollama. None si Ollama no responde
    o no tiene un modelo de chat instalado — nunca levanta excepción, esto es best-effort."""
    text = (text or "").strip()
    if not text:
        return None
    out = generate(PROMPT.format(max_words=max_words, text=text[:MAX_INPUT_CHARS]))
    if not out:
        return None
    words = out.split()
    # un modelo chico a veces se pasa del largo: se corta con margen
    return " ".join(words[: int(max_words * 1.5)]) + ("…" if len(words) > max_words * 1.5 else "")
