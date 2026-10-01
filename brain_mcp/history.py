"""Historial de versiones del vault en SQLite (data/history.sqlite3), en lugar de git.

Cada escritura o borrado guarda el contenido anterior y el nuevo, así cualquier archivo se puede
volver a una versión previa. Vive en data/ (ignorado por el .gitignore del proyecto), de modo que
el repo de la app se puede subir a GitHub sin arrastrar datos personales ni repos anidados.
SQLite en modo WAL + busy_timeout aguanta varios procesos de server.py escribiendo a la vez.
"""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA / "history.sqlite3"

OPS = ("create", "update", "edit", "append", "delete", "restore")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS changes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    op      TEXT NOT NULL,
    path    TEXT NOT NULL,
    before  TEXT,
    after   TEXT
);
CREATE INDEX IF NOT EXISTS idx_changes_path ON changes(path, id);
"""


def _connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=15000")
    con.executescript(_SCHEMA)
    return con


def record(op: str, path: str, before: str | None, after: str | None) -> int:
    with closing(_connect()) as con, con:
        cur = con.execute(
            "INSERT INTO changes (ts, op, path, before, after) VALUES (?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), op, path, before, after),
        )
        return cur.lastrowid


def list_changes(path: str | None = None, limit: int = 50) -> list[dict]:
    """Cambios más nuevos primero, sin el contenido (solo metadatos + tamaño)."""
    sql = ("SELECT id, ts, op, path, length(before) AS size_before, length(after) AS size_after "
           "FROM changes {} ORDER BY id DESC LIMIT ?")
    with closing(_connect()) as con:
        if path:
            rows = con.execute(sql.format("WHERE path = ?"), (path, limit)).fetchall()
        else:
            rows = con.execute(sql.format(""), (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_change(change_id: int) -> dict | None:
    with closing(_connect()) as con:
        row = con.execute("SELECT * FROM changes WHERE id = ?", (change_id,)).fetchone()
    return dict(row) if row else None


def all_meta() -> list[dict]:
    """Todos los cambios (id, ts, op, path) para las estadísticas del dashboard."""
    with closing(_connect()) as con:
        return [dict(r) for r in con.execute("SELECT id, ts, op, path FROM changes ORDER BY id DESC")]


def since(after_id: int, limit: int = 50) -> dict:
    """Cambios posteriores a `after_id` (para las notificaciones del dashboard). after_id < 0: solo el
    último id, así una pestaña recién abierta no avisa de todo lo viejo."""
    with closing(_connect()) as con:
        last = con.execute("SELECT COALESCE(MAX(id), 0) FROM changes").fetchone()[0]
        if after_id < 0:
            return {"last_id": last, "changes": []}
        rows = con.execute("SELECT id, ts, op, path, before IS NULL AS was_new FROM changes WHERE id > ? "
                           "ORDER BY id LIMIT ?", (after_id, limit)).fetchall()
    return {"last_id": max([last] + [r["id"] for r in rows]), "changes": [dict(r) for r in rows]}
