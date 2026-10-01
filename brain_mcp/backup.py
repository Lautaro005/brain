"""Backup y restauración de todo lo local (Ajustes → Backup).

Un backup es un .zip con:
- `vault/`: todas las notas, el perfil y la memoria.
- `data/history.sqlite3`: el historial de versiones (copiado con la API de backup de SQLite, así
  sale consistente aunque otro proceso esté escribiendo).
- `data/*.json`: ajustes y conexiones (sin secretos: esos van como `$env:CLAVE`).
- `chroma/<colección>.jsonl`: lo indexado en Chroma (fuentes y chats) con sus embeddings, así la
  restauración no necesita a Ollama. Si Chroma estaba apagado, no va y al restaurar se reconstruye
  desde el vault (las fuentes; los chats sin exportar no se pueden reconstruir).
- `secrets/` (solo si se pide): `.env`, tokens OAuth, propuestas de conexión y el acceso remoto (token de la
  URL y del túnel de Cloudflare). Por defecto NO van.
- `manifest.json`: versión de brain, fecha, qué incluye.

El índice por palabra (data/search.sqlite3) no se guarda: se reconstruye desde el vault.
Restaurar primero hace un backup de seguridad del estado actual (con secretos) en data/backups/.
"""
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from . import chroma_store, history, updates, vault

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
COLLECTIONS = ("sources", "chats")
CONFIG_FILES = ("chat_settings.json", "connections.json", "connections_tools.json", "clients.json")
SECRET_FILES = {"secrets/.env": lambda: ROOT / ".env",
                "secrets/oauth.json": lambda: history.DATA / "oauth.json",
                "secrets/connection_proposals.json": lambda: history.DATA / "connection_proposals.json",
                "secrets/remote.json": lambda: history.DATA / "remote.json"}
NAME = re.compile(r"^brain-backup-[\w.-]+\.zip$")
MAX_UNZIPPED = 20 * 1024 ** 3  # contra zips "bomba": 20 GB descomprimido como máximo
BATCH = 500


class BackupError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code, self.detail = code, detail


def _sqlite_copy(src: Path, dst: Path) -> None:
    """Copia consistente de una base SQLite a otra (reemplaza el contenido de dst)."""
    with closing(sqlite3.connect(src, timeout=15)) as a, closing(sqlite3.connect(dst, timeout=15)) as b:
        a.backup(b)


def backups_dir() -> Path:
    d = history.DATA / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# ---------- crear ----------

def _export_collection(name: str) -> list[dict] | None:
    """Todos los registros de una colección de Chroma, con embeddings. None si Chroma no está."""
    try:
        total = chroma_store.call(lambda c: c.count(), name)
        out = []
        for off in range(0, total, BATCH):
            res = chroma_store.call(lambda c: c.get(include=["documents", "metadatas", "embeddings"],
                                                    limit=BATCH, offset=off), name)
            embs = res.get("embeddings")
            for i, id_ in enumerate(res["ids"]):
                e = embs[i] if embs is not None else None
                out.append({"id": id_, "document": res["documents"][i], "metadata": res["metadatas"][i],
                            "embedding": [float(x) for x in e] if e is not None else None})
        return out
    except Exception as e:  # Chroma apagado: se reconstruye al restaurar
        log.info("sin Chroma para el backup de %s: %s", name, e)
        return None


def create(include_secrets: bool = False, prefix: str = "brain-backup") -> dict:
    """Arma el .zip en data/backups/ y devuelve {name, size, manifest}."""
    name = f"{prefix}-{_stamp()}.zip"
    dest = backups_dir() / name
    manifest = {"app": "brain", "version": updates.current(), "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "includes_secrets": include_secrets, "chroma": {}, "files": 0}
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        with vault._lock():  # nadie escribe el vault ni el historial mientras se copia
            for f in sorted(vault.VAULT.rglob("*")):
                if f.is_file() and not f.is_symlink():
                    z.write(f, "vault/" + f.relative_to(vault.VAULT).as_posix())
                    manifest["files"] += 1
            if history.DB_PATH.exists():
                snap = Path(tmp) / "history.sqlite3"
                _sqlite_copy(history.DB_PATH, snap)
                z.write(snap, "data/history.sqlite3")
        for fn in CONFIG_FILES:
            p = history.DATA / fn
            if p.exists():
                z.write(p, f"data/{fn}")
        for coll in COLLECTIONS:
            rows = _export_collection(coll)
            manifest["chroma"][coll] = None if rows is None else len(rows)
            if rows is not None:
                z.writestr(f"chroma/{coll}.jsonl", "\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
        if include_secrets:
            for arc, path in SECRET_FILES.items():
                if path().exists():
                    z.write(path(), arc)
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    if os.name != "nt":
        os.chmod(dest, 0o600)  # tiene notas personales (y quizás secretos)
    return {"name": name, "size": dest.stat().st_size, "manifest": manifest}


def listing() -> list[dict]:
    out = []
    for f in sorted(backups_dir().glob("*.zip"), reverse=True):
        try:
            with zipfile.ZipFile(f) as z:
                man = json.loads(z.read("manifest.json"))
        except (zipfile.BadZipFile, KeyError, ValueError, OSError):
            continue
        out.append({"name": f.name, "size": f.stat().st_size, "manifest": man})
    return out


def path_of(name: str) -> Path:
    """Path de un backup por nombre (validado: nada de rutas)."""
    if not re.fullmatch(r"[\w.-]+\.zip", name or ""):
        raise BackupError("bad_name", name)
    p = backups_dir() / name
    if not p.is_file():
        raise BackupError("not_found", name)
    return p


def delete(name: str) -> None:
    path_of(name).unlink()


# ---------- restaurar ----------

def _check(z: zipfile.ZipFile) -> dict:
    try:
        man = json.loads(z.read("manifest.json"))
    except (KeyError, ValueError) as e:
        raise BackupError("not_a_backup", "Falta manifest.json: no es un backup de brain.") from e
    if man.get("app") != "brain":
        raise BackupError("not_a_backup", "El manifest no es de brain.")
    total = 0
    for info in z.infolist():
        n = info.filename
        if n.startswith("/") or "\\" in n or ".." in n.split("/") or ":" in n:
            raise BackupError("unsafe_path", n)
        if not (n == "manifest.json" or n.startswith(("vault/", "data/", "chroma/", "secrets/"))):
            raise BackupError("unsafe_path", n)
        total += info.file_size
    if total > MAX_UNZIPPED:
        raise BackupError("too_large", str(total))
    return man


def _import_collection(name: str, rows: list[dict]) -> int:
    def wipe(c):
        ids = c.get(include=[])["ids"]
        for i in range(0, len(ids), BATCH):
            c.delete(ids=ids[i:i + BATCH])
    chroma_store.call(wipe, name)
    for i in range(0, len(rows), BATCH):
        part = rows[i:i + BATCH]
        kw = {"ids": [r["id"] for r in part], "documents": [r["document"] for r in part],
              "metadatas": [r["metadata"] or None for r in part]}
        if all(r.get("embedding") for r in part):
            kw["embeddings"] = [r["embedding"] for r in part]  # sin Ollama: los vectores vienen en el backup
        chroma_store.call(lambda c: c.upsert(**kw), name)
    return len(rows)


def rebuild_indexes() -> dict:
    """Reconstruye desde el vault lo que se puede: el índice por palabra (siempre) y los chunks de
    knowledge/ en Chroma (necesita Ollama para los embeddings)."""
    import server  # las mismas funciones que usan las tools

    out = {"keyword": server.reindex_keyword_core(), "chroma": None, "chroma_error": None}
    try:
        n = 0
        for f in vault.list_files("knowledge"):
            meta, body = vault.parse(vault.read_file(f["path"]))
            chunks = server.chunk_text(body)
            if not chunks:
                continue
            ids = server._chunk_ids(f["path"], meta, len(chunks))
            url = meta.get("url") or (f"mcp://{meta['connection']}/{meta.get('tool', '')}" if meta.get("connection") else "")
            chroma_store.upsert(ids, chunks, [{"url": url, "source_md_path": f["path"], "chunk_index": i} for i in range(len(chunks))])
            n += len(chunks)
        out["chroma"] = n
    except Exception as e:  # Ollama o Chroma apagados: queda el índice por palabra
        out["chroma_error"] = str(e)[:300]
    return out


def restore(zip_path: Path, include_secrets: bool = True) -> dict:
    """Restaura un backup. Antes guarda el estado actual en data/backups/before-restore-*.zip."""
    zip_path = Path(zip_path)
    try:
        z = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise BackupError("not_a_backup", "El archivo no es un .zip válido.") from e
    with z:
        man = _check(z)
        safety = create(include_secrets=True, prefix="before-restore")
        report = {"manifest": man, "safety_backup": safety["name"], "files": 0, "chroma": {}, "secrets": False}
        with tempfile.TemporaryDirectory(dir=history.DATA) as tmp:
            z.extractall(tmp)
            t = Path(tmp)
            with vault._lock():
                # vault: se reemplaza entero
                for child in list(vault.VAULT.iterdir()) if vault.VAULT.exists() else []:
                    shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
                vault.VAULT.mkdir(parents=True, exist_ok=True)
                src_vault = t / "vault"
                if src_vault.exists():
                    for child in src_vault.iterdir():
                        shutil.move(str(child), vault.VAULT / child.name)
                report["files"] = sum(1 for f in vault.VAULT.rglob("*") if f.is_file())
                # historial: con la API de backup de SQLite sobre la base viva (no se pisa el archivo,
                # que otros procesos pueden tener abierto en modo WAL)
                if (t / "data/history.sqlite3").exists():
                    history.DATA.mkdir(parents=True, exist_ok=True)
                    _sqlite_copy(t / "data/history.sqlite3", history.DB_PATH)
            for fn in CONFIG_FILES:
                if (t / "data" / fn).exists():
                    shutil.copyfile(t / "data" / fn, history.DATA / fn)
            if include_secrets and (t / "secrets").exists():
                for arc, path in SECRET_FILES.items():
                    if (t / arc).exists():
                        shutil.copyfile(t / arc, path())
                        if os.name != "nt":
                            os.chmod(path(), 0o600)
                        report["secrets"] = True
            # Chroma: lo exportado se carga tal cual; lo que falta se reconstruye desde el vault
            missing_sources = True
            for coll in COLLECTIONS:
                f = t / "chroma" / f"{coll}.jsonl"
                if not f.exists():
                    report["chroma"][coll] = None
                    continue
                rows = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
                try:
                    report["chroma"][coll] = _import_collection(coll, rows)
                    if coll == "sources":
                        missing_sources = False
                except Exception as e:
                    report["chroma"][coll] = f"error: {str(e)[:200]}"
        rb = rebuild_indexes() if missing_sources else {"keyword": _keyword_only()}
        report["rebuild"] = rb
    return report


def _keyword_only() -> dict:
    import server
    return server.reindex_keyword_core()
