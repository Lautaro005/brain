"""Pruebas rápidas del núcleo (corren en el CI de macOS, Linux y Windows)."""
import asyncio
import json
import threading
import zipfile

import pytest


# ---------- lock entre procesos ----------

def test_lock_serializes(tmp_path):
    from brain_mcp import locks

    counter = tmp_path / "n.txt"
    counter.write_text("0")

    def bump():
        for _ in range(50):
            with locks.locked(tmp_path / ".lock"):
                counter.write_text(str(int(counter.read_text()) + 1))

    ts = [threading.Thread(target=bump) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert counter.read_text() == "200"


# ---------- vault + historial ----------

def test_vault_write_move_restore():
    from brain_mcp import vault

    vault.write_file("idea.md", "---\nname: idea\ndescription: algo\n---\nTexto")
    assert "Texto" in vault.read_file("idea.md")
    vault.move_file("idea.md", "projects/idea.md")
    assert vault.list_files("projects")[0]["path"] == "projects/idea.md"
    h = vault.file_history("idea.md")
    assert h[0]["op"] == "move"
    vault.restore_version("idea.md", h[1]["id"])
    assert "Texto" in vault.read_file("idea.md")
    with pytest.raises(vault.VaultError):
        vault.write_file("../fuera.md", "x")


def test_frontmatter_repair():
    from brain_mcp import vault

    vault.write_file("n.md", "---\nname: n\ndescription: dice: algo\n---\nx")
    meta, _ = vault.parse(vault.read_file("n.md"))
    assert meta["description"] == "dice: algo"


def test_memory_supersede():
    from brain_mcp import memory

    memory.add(["Vive en Palermo"], "Sobre mí")
    r = memory.add(["Vive en Núñez"], "Sobre mí", supersede="Vive en Palermo")
    assert r["superseded"]["text"] == "Vive en Palermo"
    items = {c["category"]: c["items"] for c in memory.list_all()}
    assert items["Sobre mí"] == ["Vive en Núñez"]


def test_keyword_search():
    from brain_mcp import keyword_store

    keyword_store.upsert(["a-0"], ["Vive en Núñez. Ticket INC-4821"], [{"url": "u", "source_md_path": "p.md", "chunk_index": 0}])
    assert keyword_store.query("nunez")[0]["id"] == "a-0"
    assert keyword_store.query("INC-4821")[0]["id"] == "a-0"


def test_organize_overview():
    from brain_mcp import organize, vault

    vault.write_file("nota.md", "---\nname: nota\ntags: [proyecto]\n---\nx")
    vault.write_file("notas.md", "---\nname: notas\ntags: [proyectos]\n---\ny")
    o = organize.overview()
    assert ["nota.md", "notas.md"] in o["similar_names"]
    assert "Panorama del vault" in organize.overview_text()


# ---------- MCP server ----------

def test_server_tools():
    import server

    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {"move_file", "vault_overview", "propose_connection", "read_url", "refresh_sources"} <= names
    assert server.move_file("profile.md", "x/profile.md").startswith("Error")


# ---------- chat ----------

def test_chat_commands_and_compact():
    from brain_mcp import chat

    assert chat.parse_command("/add-mcp https://x/mcp") == ("add-mcp", "https://x/mcp")
    assert chat.parse_command("/organize")[0] == "organize"
    assert chat.parse_command("/nope hola")[0] == ""
    h = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "RES", "compact": True}, {"role": "user", "content": "b"}]
    assert chat._split_compact(h) == ("RES", h[2:])


def test_context_settings():
    from brain_mcp import chat

    chat.save_ctx_settings({"mode": "cap", "cap": 8192, "models": {"m1": 4096}})
    assert chat.context_window("m1")["ctx"] == 4096  # sin Ollama no se conoce el máximo: se usa el propio
    assert chat.context_window("otro")["ctx"] == 4096  # DEFAULT_CTX (4096) < tope 8192
    with pytest.raises(chat.ChatError):
        chat.save_ctx_settings({"mode": "cap", "cap": 10})


# ---------- conexiones ----------

def test_connection_proposals():
    from brain_mcp import connectors

    p = connectors.propose({"name": "Fetch", "command": "uvx", "args": ["mcp-server-fetch"], "env": {"KEY": "secreto"}})
    assert "secreto" not in json.dumps(p) and connectors.load() == []
    c = connectors.resolve_proposal(p["id"], True)
    assert c["env_keys"] == {"KEY": True} and connectors.proposals() == []
    assert connectors.uses_oauth({"kind": "http", "headers": {}})


def test_oauth_storage():
    from brain_mcp import oauth
    from mcp.shared.auth import OAuthToken

    s = oauth._Storage("c1")
    asyncio.run(s.set_tokens(OAuthToken(access_token="t", token_type="Bearer", refresh_token="r")))
    assert asyncio.run(s.get_tokens()).access_token == "t"
    assert oauth.has_tokens("c1")
    oauth.forget("c1")
    assert not oauth.has_tokens("c1")
    assert oauth.callback("state=desconocido&code=x") == (False, "unknown_state")


# ---------- backup ----------

def test_backup_roundtrip_without_chroma(isolated):
    from brain_mcp import backup, vault

    (isolated / ".env").write_text("SECRETO=1\n")
    vault.write_file("projects/uno.md", "---\nname: uno\n---\nhola")
    b = backup.create()
    names = zipfile.ZipFile(backup.backups_dir() / b["name"]).namelist()
    assert "vault/projects/uno.md" in names and "data/history.sqlite3" in names
    assert not any(n.startswith("secrets/") for n in names)
    assert b["manifest"]["chroma"]["sources"] is None  # Chroma apagado: se reconstruye al restaurar
    vault.delete_file("projects/uno.md")
    rep = backup.restore(backup.backups_dir() / b["name"])
    assert (vault.VAULT / "projects/uno.md").exists()
    assert rep["safety_backup"].startswith("before-restore")
    assert "keyword" in rep["rebuild"]  # sin Chroma exportado: se reconstruye desde el vault
    evil = backup.backups_dir() / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("manifest.json", json.dumps({"app": "brain"}))
        z.writestr("../x", "x")
    with pytest.raises(backup.BackupError):
        backup.restore(evil)


# ---------- agentes ----------

def test_agents_paths():
    from brain_mcp import agents

    p = agents.CLIENTS["claude_desktop"]["path"]
    assert p.name == "claude_desktop_config.json" and p.parent.name == "Claude"
    assert agents.server_spec()["args"][-1] == "server.py"
