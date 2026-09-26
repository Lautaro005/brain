"""Embeddings vía Ollama (nomic-embed-text) por HTTP."""
import logging
import time

import requests

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/embeddings"
MODEL = "nomic-embed-text"
# nomic-embed-text espera prefijos de instrucción
PREFIXES = {"document": "search_document: ", "query": "search_query: "}


class OllamaUnavailable(RuntimeError):
    pass


def _embed_one(text: str, retries: int = 3) -> list[float]:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.post(OLLAMA_URL, json={"model": MODEL, "prompt": text}, timeout=60)
            r.raise_for_status()
            emb = r.json().get("embedding")
            if not emb:
                raise RuntimeError(f"Ollama devolvió un embedding vacío: {r.text[:200]}")
            return emb
        except requests.ConnectionError as e:
            raise OllamaUnavailable(
                "Ollama no está corriendo en localhost:11434 (levantalo con `ollama serve` "
                "o abrí la app Ollama, y verificá `ollama pull nomic-embed-text`)."
            ) from e
        except (requests.Timeout, requests.HTTPError, RuntimeError) as e:
            last_err = e
            log.warning("embed intento %d falló: %s", attempt + 1, e)
            time.sleep(0.5 * (attempt + 1))
    raise OllamaUnavailable(f"Ollama no pudo generar el embedding: {last_err}")


def embed(texts: list[str], task: str = "document") -> list[list[float]]:
    """task: 'document' para indexar, 'query' para buscar."""
    prefix = PREFIXES[task]
    return [_embed_one(prefix + t) for t in texts]
