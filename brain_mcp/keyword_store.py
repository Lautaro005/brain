"""Índice de texto (BM25) en SQLite FTS5, en data/search.sqlite3.

Complementa a chroma_store: los embeddings captan el sentido, pero fallan con nombres propios,
IDs, números o fechas exactas; un match por palabra los encuentra. search_knowledge fusiona las
dos listas (reciprocal rank fusion). Misma interfaz que chroma_store (upsert, delete, query).

No depende de Ollama ni de Chroma: si alguno está caído, la búsqueda por palabra sigue andando.
Mismo patrón de conexión que history.py (WAL + busy_timeout) para aguantar varios procesos.
"""
import re
import sqlite3
from contextlib import closing
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA / "search.sqlite3"

# unicode61 + remove_diacritics: "Núñez" matchea "nunez" y no importan mayúsculas. No se usa el
# stemmer porter porque es para inglés y buena parte del vault está en español.
_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED, path UNINDEXED, url UNINDEXED, chunk_index UNINDEXED, text,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""
_TOKEN = re.compile(r"\w+", re.U)


def _connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=15000")
    con.executescript(_SCHEMA)
    return con


def upsert(ids: list[str], documents: list[str], metadatas: list[dict]) -> None:
    """Mismos argumentos que chroma_store.upsert. Reemplaza los chunks con esos ids."""
    if not ids:
        return
    with closing(_connect()) as con, con:
        con.executemany("DELETE FROM chunks_fts WHERE chunk_id = ?", [(i,) for i in ids])
        con.executemany(
            "INSERT INTO chunks_fts (chunk_id, path, url, chunk_index, text) VALUES (?, ?, ?, ?, ?)",
            [(i, (m or {}).get("source_md_path"), (m or {}).get("url"), (m or {}).get("chunk_index"), d)
             for i, d, m in zip(ids, documents, metadatas)],
        )


def delete(ids: list[str]) -> None:
    if not ids:
        return
    with closing(_connect()) as con, con:
        con.executemany("DELETE FROM chunks_fts WHERE chunk_id = ?", [(i,) for i in ids])


def delete_path(path: str) -> None:
    with closing(_connect()) as con, con:
        con.execute("DELETE FROM chunks_fts WHERE path = ?", (path,))


def clear() -> None:
    with closing(_connect()) as con, con:
        con.execute("DELETE FROM chunks_fts")


def count() -> int:
    with closing(_connect()) as con:
        return con.execute("SELECT count(*) FROM chunks_fts").fetchone()[0]


def _match_expr(text: str) -> str | None:
    """Texto libre → expresión MATCH segura: cada término entre comillas, unidos con OR (BM25
    igual premia a los chunks que tienen más términos). Nada de la sintaxis de FTS5 pasa crudo."""
    terms = list(dict.fromkeys(t for t in _TOKEN.findall(text.lower()) if len(t) > 1 or t.isdigit()))
    return " OR ".join(f'"{t}"' for t in terms[:32]) or None


def query(text: str, top_k: int = 5) -> list[dict]:
    """Chunks ordenados por BM25 (mejor primero). Mismo formato que chroma_store.query, con
    'rank' (más negativo = mejor, así lo devuelve FTS5) en lugar de 'distance'."""
    expr = _match_expr(text)
    if not expr:
        return []
    with closing(_connect()) as con:
        rows = con.execute(
            "SELECT chunk_id, path, url, chunk_index, text, bm25(chunks_fts) AS rank "
            "FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
            (expr, max(1, int(top_k))),
        ).fetchall()
    return [{"id": r["chunk_id"], "text": r["text"], "rank": r["rank"],
             "metadata": {"url": r["url"], "source_md_path": r["path"], "chunk_index": r["chunk_index"]}}
            for r in rows]
