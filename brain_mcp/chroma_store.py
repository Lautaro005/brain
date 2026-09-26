"""Vector store con ChromaDB en modo server (HttpClient).

Un solo server de Chroma (./chroma_server.sh) compartido por todos los procesos de server.py
(Claude Code, Claude Desktop, …), así no compiten por el lock del SQLite de chroma_db/.
"""
import logging
import os

import chromadb
import httpx
from chromadb import Documents, EmbeddingFunction, Embeddings

from .embeddings import embed

log = logging.getLogger(__name__)

CHROMA_HOST = "127.0.0.1"
CHROMA_PORT = int(os.environ.get("BRAIN_CHROMA_PORT", "8055"))
COLLECTION = "sources"


class ChromaUnavailable(RuntimeError):
    pass


class OllamaEmbeddingFunction(EmbeddingFunction[Documents]):
    """Embebe documentos con prefijo search_document. Las queries se embeben aparte."""

    def __init__(self) -> None:
        pass

    def __call__(self, input: Documents) -> Embeddings:
        return embed(list(input), task="document")

    @staticmethod
    def name() -> str:
        return "ollama-nomic-embed-text"

    def get_config(self) -> dict:
        return {}

    @staticmethod
    def build_from_config(config: dict) -> "OllamaEmbeddingFunction":
        return OllamaEmbeddingFunction()


_collection = None


def _unavailable(e: Exception) -> ChromaUnavailable:
    global _collection
    _collection = None  # reconectar en la próxima llamada, por si el server vuelve
    return ChromaUnavailable(
        f"El server de Chroma no está corriendo (./chroma_server.sh) en {CHROMA_HOST}:{CHROMA_PORT}."
    )


def _is_connection_error(e: Exception) -> bool:
    return isinstance(e, httpx.TransportError) or (
        isinstance(e, ValueError) and "Could not connect" in str(e)
    )


def get_collection():
    global _collection
    if _collection is None:
        try:
            client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
            _collection = client.get_or_create_collection(
                COLLECTION,
                embedding_function=OllamaEmbeddingFunction(),
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as e:
            if _is_connection_error(e):
                raise _unavailable(e) from e
            raise
    return _collection


def _call(fn):
    """Corre una operación contra Chroma traduciendo errores de conexión a ChromaUnavailable."""
    try:
        return fn(get_collection())
    except ChromaUnavailable:
        raise
    except Exception as e:
        if _is_connection_error(e):
            raise _unavailable(e) from e
        raise


def upsert(ids: list[str], documents: list[str], metadatas: list[dict]) -> None:
    # embeddings calculados acá (no dentro de la llamada HTTP) para que un Ollama caído
    # siga saliendo como OllamaUnavailable y no se confunda con Chroma
    embeddings = embed(documents, task="document")
    _call(lambda c: c.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas))


def delete(ids: list[str]) -> None:
    if ids:
        _call(lambda c: c.delete(ids=ids))


def query(text: str, top_k: int = 5) -> list[dict]:
    count = _call(lambda c: c.count())
    if count == 0:
        return []
    q_emb = embed([text], task="query")
    res = _call(lambda c: c.query(query_embeddings=q_emb, n_results=min(top_k, count)))
    out = []
    for i, doc_id in enumerate(res["ids"][0]):
        out.append(
            {
                "id": doc_id,
                "text": res["documents"][0][i],
                "metadata": res["metadatas"][0][i],
                "distance": res["distances"][0][i],
            }
        )
    return out
