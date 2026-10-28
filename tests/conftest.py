"""Cada test corre contra un vault y un data/ temporales: nunca toca los datos reales del usuario.
No necesitan red, Ollama ni Chroma (lo que depende de ellos se prueba en su camino de "caído")."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BRAIN_CHROMA_PORT", "59999")  # un puerto donde no hay nada: Chroma "apagado"
os.environ.setdefault("BRAIN_AUTO_ABOUT", "off")  # el resumen del perfil en segundo plano no corre en los tests


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from brain_mcp import appmode, backup, chat, connectors, family, graph, history, keyword_store, lmstudio, oauth, remote, vault

    data = tmp_path / "data"
    v = (tmp_path / "vault")
    v.mkdir()
    monkeypatch.setattr(vault, "VAULT", v.resolve())
    monkeypatch.setattr(graph, "VAULT", v.resolve())
    monkeypatch.setattr(history, "DATA", data)
    monkeypatch.setattr(history, "DB_PATH", data / "history.sqlite3")
    monkeypatch.setattr(keyword_store, "DATA", data)
    monkeypatch.setattr(keyword_store, "DB_PATH", data / "search.sqlite3")
    monkeypatch.setattr(connectors, "DATA", data)
    monkeypatch.setattr(connectors, "CONFIG", data / "connections.json")
    monkeypatch.setattr(connectors, "CACHE", data / "connections_tools.json")
    monkeypatch.setattr(connectors, "PROPOSALS", data / "connection_proposals.json")
    monkeypatch.setattr(connectors, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(oauth, "DATA", data)
    monkeypatch.setattr(remote, "DATA", data)
    monkeypatch.setattr(remote, "CONFIG", data / "remote.json")
    monkeypatch.setattr(remote, "BIN", data / "bin")
    monkeypatch.setattr(lmstudio, "DATA", data)
    monkeypatch.setattr(lmstudio, "CONFIG", data / "lmstudio.json")
    lmstudio._CACHE["key"] = None
    monkeypatch.setattr(family, "DATA", data)
    monkeypatch.setattr(family, "CONFIG", data / "family.json")
    monkeypatch.setenv("BRAIN_FAMILY_HOME", str(tmp_path / "home"))  # nada de las apps reales del usuario
    monkeypatch.setenv("OPENCUE_HOME", str(tmp_path / "opencue-none"))
    monkeypatch.delenv("BRAIN_LUMINA_URL", raising=False)
    monkeypatch.setattr(appmode, "DATA", data)
    monkeypatch.setattr(appmode, "SETTINGS", data / "app_settings.json")
    monkeypatch.setattr(appmode, "LOG", data / "logs" / "app.log")
    monkeypatch.delenv("BRAIN_APP", raising=False)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(chat, "OLLAMA", "http://127.0.0.1:59998")  # Ollama "apagado"
    chat._MAX.clear()
    vault.ensure_vault()
    yield tmp_path
