"""v0.03.3.5: la familia de apps (Lumina, OpenCue, AI WorkStudio) en el chat y como tool. Sin red ni apps reales."""
import asyncio
import json
import sqlite3
import sys
import textwrap

import pytest

from brain_mcp import chat, family


# ---------- Lumina ----------

def _fake_get(handler):
    class R:
        def __init__(self, status, body):
            self.status_code, self._b, self.text = status, body, json.dumps(body)
            self.ok = status < 400

        def json(self):
            return self._b

    def get(url, params=None, timeout=0, **kw):
        s, b = handler(url, params or {})
        return R(s, b)

    return get


def test_lumina_search_and_errors(monkeypatch):
    import requests

    seen = {}

    def h(url, params):
        seen.update(url=url, **params)
        return 200, [{"text": "x" * 3000, "title": "Reunión del lunes", "score": 0.7, "collection": "meetings", "date": "2026-10-26"}, "basura"]

    monkeypatch.setattr(requests, "get", _fake_get(h))
    hits = family.lumina_search("presupuesto", 50)
    assert seen["url"] == "http://127.0.0.1:47770/api/search" and seen["k"] == 20 and seen["scope"] == "all"
    assert len(hits) == 1 and len(hits[0]["text"]) == 1500 and hits[0]["kind"] == "meetings"
    assert "Reunión del lunes" in family.lumina_block(hits)
    for bad in ("", "  "):
        with pytest.raises(family.FamilyError):
            family.lumina_search(bad)
    with pytest.raises(family.FamilyError) as e:
        family.lumina_search("x", scope="todo")
    assert e.value.code == "bad_scope"
    monkeypatch.setattr(requests, "get", _fake_get(lambda u, p: (409, {"detail": "embeddings"})))
    with pytest.raises(family.FamilyError) as e:
        family.lumina_search("x")
    assert e.value.code == "lumina_not_ready"

    def down(*a, **k):
        raise requests.ConnectionError("no")

    monkeypatch.setattr(requests, "get", down)
    with pytest.raises(family.FamilyError) as e:
        family.lumina_search("x")
    assert e.value.code == "lumina_down"
    assert family.lumina_status()["running"] is False


def test_lumina_url_only_loopback():
    assert family.save({"lumina_url": "http://localhost:5000/"})["lumina_url"] == "http://localhost:5000"
    for bad in ("https://127.0.0.1:1", "http://example.com:47770", "http://127.0.0.1:1/x"):
        with pytest.raises(family.FamilyError):
            family.save({"lumina_url": bad})
    assert family.save({"lumina_url": ""})["lumina_url"] == family.LUMINA_URL


def test_lumina_search_mcp_tool(monkeypatch):
    import server

    monkeypatch.setattr(family, "lumina_search", lambda q, k=6, s="all": [{"title": "T", "text": "t"}])
    out = asyncio.run(server.mcp.call_tool("lumina_search", {"query": "hola"}))
    assert "T" in json.dumps(out.structured_content or [c.text for c in out.content])

    def down(*a, **k):
        raise family.FamilyError("lumina_down")

    monkeypatch.setattr(family, "lumina_search", down)
    txt = asyncio.run(server.mcp.call_tool("lumina_search", {"query": "hola"})).content[0].text
    assert "no está corriendo" in txt
    assert "lumina_search" not in server.READ_ONLY_TOOLS


# ---------- OpenCue ----------

@pytest.fixture
def oc_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCUE_HOME", str(tmp_path / "oc"))
    (tmp_path / "oc").mkdir()
    db = sqlite3.connect(tmp_path / "oc" / "opencue.db")
    db.execute("CREATE TABLE agents (id TEXT PRIMARY KEY, name TEXT, avatar_emoji TEXT, model TEXT)")
    db.executemany("INSERT INTO agents VALUES (?,?,?,?)", [("a1", "pacman", "🟡", "gpt-5"), ("a2", "Zeta", "", None)])
    db.commit()
    db.close()
    return tmp_path / "oc"


def test_opencue_agents_and_models(oc_home):
    ags = family.opencue_agents()
    assert [a["name"] for a in ags] == ["pacman", "Zeta"] and ags[1]["model"] == ""
    ms = family.chat_models()
    assert ms[0]["name"] == "opencue:pacman" and "OpenCue · 🟡 pacman" == ms[0]["label"] and ms[0]["agent"]
    assert family.is_agent_ref("opencue:pacman") and not family.is_agent_ref("ollama:llama3")
    family.save({"opencue_agents": False})
    assert family.chat_models() == []


def test_no_opencue_means_no_agents(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCUE_HOME", str(tmp_path / "nada"))
    assert family.opencue_agents() == [] and family.chat_models() == []


FAKE_SERVER = textwrap.dedent('''
    import sys
    from mcp.server.mcpserver import MCPServer
    s = MCPServer("opencue")
    AGENT = sys.argv[sys.argv.index("--agent") + 1]

    @s.tool()
    def send_to_agent(agent: str, message: str) -> str:
        if agent == "boom":
            raise ValueError("fallo")
        return f"[{agent}]: desde {AGENT}: {message[-40:]}"

    s.run()
''')


def test_send_to_agent_over_stdio(tmp_path):
    script = tmp_path / "fake_opencue.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    out = asyncio.run(family._send_to_agent(sys.executable, "a2", "pacman", "hola mundo", 60, prefix=(str(script),)))
    assert out == "desde a2: hola mundo"  # sin el prefijo "[agente]:"
    with pytest.raises(family.FamilyError) as e:
        asyncio.run(family._send_to_agent(sys.executable, "a2", "boom", "x", 60, prefix=(str(script),)))
    assert e.value.code == "agent_error"


def test_opencue_chat_picks_another_agent_as_sender(oc_home, monkeypatch):
    calls = {}

    async def fake(exe, sender, target, message, timeout, prefix=()):
        calls.update(sender=sender, target=target, message=message)
        return "listo"

    monkeypatch.setattr(family, "_send_to_agent", fake)
    monkeypatch.setattr(family, "opencue_cli", lambda: "/x/opencue")
    monkeypatch.setattr(family, "_start_opencue", lambda: None)
    assert family.opencue_chat("pacman", "hola") == "listo"
    assert calls["sender"] == "a2" and calls["target"] == "pacman"  # el remitente es otro agente: nunca el destino
    with pytest.raises(family.FamilyError) as e:
        family.opencue_chat("no-existe", "hola")
    assert e.value.code == "agent_not_found"


# ---------- el chat ----------

def _events(gen):
    return list(gen)


def test_chat_with_opencue_agent_carries_brain_context(oc_home, monkeypatch):
    from brain_mcp import memory

    memory.save_profile("Lauti", "estudiante", "Estudio Derecho")
    memory.add(["Prefiere respuestas cortas"], "Preferencias")
    sent = []
    monkeypatch.setattr(family, "opencue_chat", lambda agent, msg, timeout=0, start_app=True: sent.append((agent, msg)) or "Hola, soy pacman")
    saved = []
    monkeypatch.setattr(chat, "_save", lambda *a, **k: saved.append(a))
    monkeypatch.setattr(chat, "get_chat", lambda cid: (_ for _ in ()).throw(chat.ChatError("not_found", cid)))
    evs = _events(chat.run("¿Quién soy?", "opencue:pacman", None, "es"))
    kinds = [e["type"] for e in evs]
    assert kinds[0] == "start" and kinds[-1] == "done" and "token" in kinds and "error" not in kinds
    msg = sent[0][1]
    assert sent[0][0] == "pacman" and "Estudio Derecho" in msg and "Prefiere respuestas cortas" in msg and msg.rstrip().endswith("¿Quién soy?")
    assert any(e["type"] == "tool" and e["name"] == "OpenCue → pacman" for e in evs)
    assert saved and saved[0][2] == "opencue:pacman"


def test_second_message_does_not_repeat_the_context(oc_home, monkeypatch):
    sent = []
    monkeypatch.setattr(family, "opencue_chat", lambda agent, msg, timeout=0, start_app=True: sent.append(msg) or "ok")
    monkeypatch.setattr(chat, "_save", lambda *a, **k: None)
    prev = {"title": "t", "created_at": "x", "messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]}
    monkeypatch.setattr(chat, "get_chat", lambda cid: prev)
    _events(chat.run("otra cosa", "opencue:pacman", "c1", "es"))
    assert sent == ["otra cosa"]


def test_agent_errors_become_error_events(oc_home, monkeypatch):
    def boom(*a, **k):
        raise family.FamilyError("opencue_start", "no abrió")

    monkeypatch.setattr(family, "opencue_chat", boom)
    monkeypatch.setattr(chat, "_save", lambda *a, **k: None)
    monkeypatch.setattr(chat, "get_chat", lambda cid: (_ for _ in ()).throw(chat.ChatError("not_found", cid)))
    evs = _events(chat.run("hola", "opencue:pacman", None, "es"))
    err = next(e for e in evs if e["type"] == "error")
    assert err["code"] == "opencue_start" and evs[-1]["type"] == "done"


def test_lumina_toggle_adds_results_for_an_agent(oc_home, monkeypatch):
    monkeypatch.setattr(family, "lumina_search", lambda q, k=6, s="all": [{"text": "Se acordó el presupuesto", "title": "Reunión", "kind": "meetings", "date": "2026-10-26"}])
    sent = []
    monkeypatch.setattr(family, "opencue_chat", lambda agent, msg, timeout=0, start_app=True: sent.append(msg) or "ok")
    monkeypatch.setattr(chat, "_save", lambda *a, **k: None)
    monkeypatch.setattr(chat, "get_chat", lambda cid: (_ for _ in ()).throw(chat.ChatError("not_found", cid)))
    evs = _events(chat.run("¿Qué se acordó?", "opencue:pacman", None, "es", lumina=True))
    assert "Se acordó el presupuesto" in sent[0] and sent[0].rstrip().endswith("¿Qué se acordó?")
    assert [e["name"] for e in evs if e["type"] == "tool"][:2] == ["lumina_search", "OpenCue → pacman"]
    # sin el botón no se consulta Lumina
    sent.clear()
    monkeypatch.setattr(family, "lumina_search", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debería buscar")))
    _events(chat.run("hola", "opencue:pacman", None, "es", lumina=False))
    assert "Lumina" not in sent[0]


def test_lumina_down_is_a_notice_not_a_failure(oc_home, monkeypatch):
    def down(*a, **k):
        raise family.FamilyError("lumina_down")

    monkeypatch.setattr(family, "lumina_search", down)
    monkeypatch.setattr(family, "opencue_chat", lambda *a, **k: "ok")
    monkeypatch.setattr(chat, "_save", lambda *a, **k: None)
    monkeypatch.setattr(chat, "get_chat", lambda cid: (_ for _ in ()).throw(chat.ChatError("not_found", cid)))
    evs = _events(chat.run("hola", "opencue:pacman", None, "es", lumina=True))
    assert any(e["type"] == "notice" and e["code"] == "lumina_down" for e in evs)
    assert not any(e["type"] == "error" for e in evs)


def test_lumina_tool_only_in_chat_when_enabled(monkeypatch):
    """Con un modelo normal: lumina_search aparece entre las tools del chat solo si el usuario habilitó Lumina."""
    seen = []

    def fake_stream(model, messages, tools, num_ctx=0, options=None):
        seen.append([t["function"]["name"] for t in (tools or [])])
        yield {"message": {"content": "listo"}, "done": True}

    monkeypatch.setattr(chat, "_stream", fake_stream)
    monkeypatch.setattr(chat, "_save", lambda *a, **k: None)
    monkeypatch.setattr(chat, "get_chat", lambda cid: (_ for _ in ()).throw(chat.ChatError("not_found", cid)))
    monkeypatch.setattr(chat, "_past_block", lambda *a, **k: "")
    monkeypatch.setattr(chat, "context_window", lambda m, *a, **k: {"ctx": 8192, "max": 8192, "source": "x"})
    monkeypatch.setattr(family, "lumina_search", lambda q, k=6, s="all": [])
    _events(chat.run("hola", "ollama:x", None, "es", lumina=False))
    evs = _events(chat.run("hola", "ollama:x", None, "es", lumina=True))
    assert "lumina_search" not in seen[0] and "lumina_search" in seen[1]
    assert any(e["type"] == "tool" and e["name"] == "lumina_search" for e in evs)


def test_models_include_agents_even_without_ollama(oc_home, monkeypatch):
    from brain_mcp import llm_providers

    monkeypatch.setattr(llm_providers, "list_chat_models", lambda: [])
    monkeypatch.setattr(llm_providers, "ollama_healthy", lambda: False)
    assert [m["name"] for m in chat.models()] == ["opencue:pacman", "opencue:Zeta"]


def test_family_api(oc_home, monkeypatch):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    import dashboard

    srv = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    port = srv.server_address[1]
    dashboard.ALLOWED_HOSTS.update({f"127.0.0.1:{port}", f"localhost:{port}"})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        monkeypatch.setattr(family, "lumina_status", lambda timeout=1.5: {"installed": True, "running": False, "version": "", "url": family.LUMINA_URL})
        monkeypatch.setattr(family, "_process_running", lambda n: False)
        monkeypatch.setattr(family, "opencue_cli", lambda: "/x/opencue")

        def post(path, body):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                         headers={"X-Brain": "1", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())

        with urllib.request.urlopen(base + "/api/family", timeout=10) as r:
            st = json.loads(r.read())
        assert st["ok"] and st["opencue"]["agents"] == 2 and st["opencue"]["installed"] and st["lumina"]["running"] is False
        assert post("/api/family", {"lumina_url": "http://evil.example:1"})["code"] == "bad_url"
        assert post("/api/family", {"opencue_agents": False})["settings"]["opencue_agents"] is False
        assert post("/api/family/open", {"app": "nada"})["code"] == "not_installed"
    finally:
        srv.shutdown()
