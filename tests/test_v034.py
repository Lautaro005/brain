"""v0.03.4: actualizar sin quedarse trabado, agentes fuera de projects/, OpenBlocks y la familia WorkStudio."""
import asyncio
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from brain_mcp import chat, family, graph, vault
from tests.test_dashboard import _get as _raw_get, dash  # noqa: F401  (fixture)
from tests.test_v031 import _post as _raw_post

ROOT = Path(__file__).resolve().parent.parent


def _get(base, path):
    return json.loads(_raw_get(base + path)[1])


def _post(base, path, body):
    return json.loads(_raw_post(base + path, body))


# ---------- brain.sh: pull_code ----------
# brain.sh es de macOS/Linux; en Windows el equivalente es Pull-Code de brain.ps1 (y `bash` ahí puede ser el de WSL):
# estas pruebas corren en los dos sistemas Unix del CI.
unix_only = pytest.mark.skipif(sys.platform == "win32", reason="brain.sh es de macOS/Linux")

def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True,
                          env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                               "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin"}).stdout


def _pull(d):
    """Corre pull_code de brain.sh sobre la carpeta d."""
    src = (ROOT / "brain.sh").read_text(encoding="utf-8")
    fn = src[src.index("pull_code() {"):src.index("\ncase \"${1:-}\" in")]
    return subprocess.run(["bash", "-c", f"{fn}\npull_code \"$1\"", "x", str(d)], capture_output=True, text=True)


@pytest.fixture()
def repos(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    (origin / "VERSION").write_text("v1\n")
    (origin / "uv.lock").write_text("a\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "uno")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    (clone / "data").mkdir()
    return origin, clone


def _release(origin, version="v2", lock="b\n"):
    (origin / "VERSION").write_text(version + "\n")
    (origin / "uv.lock").write_text(lock)
    _git(origin, "commit", "-qam", version)


@unix_only
def test_pull_code_plain_and_with_local_changes(repos):
    origin, clone = repos
    _release(origin)
    assert _pull(clone).returncode == 0 and (clone / "VERSION").read_text().strip() == "v2"
    # un archivo del repo cambiado en la instalación (p. ej. uv.lock) ya no frena la actualización
    (clone / "uv.lock").write_text("cambiado local\n")
    _release(origin, "v3", "c\n")
    r = _pull(clone)
    assert r.returncode == 0, r.stderr
    assert (clone / "VERSION").read_text().strip() == "v3" and (clone / "uv.lock").read_text() == "c\n"
    assert "stash" in _git(clone, "stash", "list").lower() or _git(clone, "stash", "list").strip()  # quedó guardado
    assert "pull falló" in (clone / "data/logs/update.log").read_text(encoding="utf-8")


@unix_only
def test_pull_code_diverged_install_is_aligned_but_dev_clone_is_not(repos):
    origin, clone = repos
    (clone / "local.txt").write_text("x")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "commit local")  # rama divergida: --ff-only no puede
    _release(origin, "v2")
    dev = _pull(clone)
    assert dev.returncode == 1 and "No se pudo actualizar" in dev.stderr  # clon de desarrollo: no se pisa nada
    assert (clone / "local.txt").exists()
    (clone / "data" / ".brain-install").write_text("")  # instalación: nadie edita ahí
    inst = _pull(clone)
    assert inst.returncode == 0, inst.stderr
    assert (clone / "VERSION").read_text().strip() == "v2" and not (clone / "local.txt").exists()


def test_scripts_use_the_resilient_pull():
    for name in ("brain.sh", "brain.ps1", "install.sh", "install.ps1"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "brain-install" in text, name
    assert "pull_code" in (ROOT / "brain.sh").read_text() and "Pull-Code" in (ROOT / "brain.ps1").read_text()


# ---------- la versión que corre de verdad ----------

def test_version_reports_stale_process_and_app_install(dash, monkeypatch, tmp_path):  # noqa: F811
    import dashboard

    from brain_mcp import history

    v = _get(dash, "/api/version")
    assert v["running"] == v["current"] and v["stale"] is False and v["app_install"] is None
    monkeypatch.setattr(dashboard, "RUNNING_VERSION", "v0.00.1")
    (history.DATA / "logs").mkdir(parents=True, exist_ok=True)
    (history.DATA / "logs" / "update.log").write_text("2026 [update] pull falló: Your local changes\n", encoding="utf-8")
    (history.DATA / "app_install.json").write_text(json.dumps({"ok": False, "tag": "v0.03.3.5", "reason": "viejo"}), encoding="utf-8")
    v = _get(dash, "/api/version")
    assert v["stale"] is True and v["app_install"]["tag"] == "v0.03.3.5" and "pull falló" in v["last_update"]["failed"]


# ---------- agents/ ----------

def _note(name, typ=None, body="x"):
    t = f"type: {typ}\n" if typ else ""
    return f"---\n{t}name: {name}\ndescription: d\n---\n{body}\n"


def test_agent_types_and_graph_kinds():
    assert vault.note_type("agents/pacman.md") == "Agent"
    assert vault.note_type("agents/pacman/index.md") == "Agent"
    assert vault.note_type("agents/pacman/memory.md") == "File"
    assert vault.note_type("agents/groups/dev/index.md") == "Agent"
    assert vault.note_type("agents/index.md") == "Index"
    assert vault.project_type("agents/pacman/index.md") is None  # un agente no es un proyecto
    vault.write_file("agents/pacman/index.md", _note("pacman"))
    vault.write_file("agents/pacman/memory.md", _note("pacman-memory"))
    g = graph.build_graph()
    kinds = {n["id"]: n["kind"] for n in g["nodes"]}
    assert kinds["agents/pacman/index.md"] == "agent" and kinds["agents/pacman/memory.md"] == "file"
    assert "agents" in vault.BASE_DIRS and any(n["id"] == "dir:agents" for n in g["nodes"])
    assert {"source": "agents/pacman/memory.md", "target": "agents/pacman/index.md", "kind": "part"} in g["links"]


def test_migrate_opencue_agents_out_of_projects():
    vault.write_file("projects/opencue-agents.md", _note("opencue-agents", "Project", "Mapa: [pacman](projects/opencue-agents/pacman/index.md)"))
    vault.write_file("projects/opencue-agents/pacman/index.md", _note("pacman", "SubProject"))
    vault.write_file("projects/opencue-agents/pacman/memory.md", _note("pacman-memory", "File"))
    vault.write_file("projects/otro.md", _note("otro", "Project", "ver [mapa](projects/opencue-agents.md) y `projects/opencue-agents` (texto)"))
    assert vault.legacy_agents()
    r = vault.migrate_agents()
    assert r["moved"] == 3 and not vault.legacy_agents()
    idx = vault.read_file("agents/index.md")
    assert "](agents/pacman/index.md)" in idx
    assert "type: Agent" in vault.read_file("agents/pacman/index.md") and "type: File" in vault.read_file("agents/pacman/memory.md")
    other = vault.read_file("projects/otro.md")
    assert "](agents/index.md)" in other and "`projects/opencue-agents`" in other  # solo se cambian los links por ruta
    assert vault.migrate_agents()["moved"] == 0
    assert graph.build_graph()  # el grafo sigue armándose


def test_family_api_shows_openblocks_and_migrates(dash, monkeypatch):  # noqa: F811
    monkeypatch.setattr(family, "openblocks_status", lambda timeout=1.5: {"installed": True, "running": False, "version": "", "url": "x", "enabled": True})
    r = _get(dash, "/api/family")
    assert r["openblocks"]["installed"] is True and r["agents"]["legacy"] is False
    vault.write_file("projects/opencue-agents/a/index.md", _note("a", "SubProject"))
    assert _get(dash, "/api/family")["agents"]["legacy"] is True
    m = _post(dash, "/api/family/migrate_agents", {})
    assert m["ok"] and m["moved"] == 1 and m["legacy"] is False
    saved = _post(dash, "/api/family", {"openblocks": False})
    assert saved["settings"]["openblocks"] is False


def test_family_logos_are_served(dash):  # noqa: F811
    import urllib.request

    for n in ("brain", "lumina", "workstudio", "opencue", "openblocks"):
        with urllib.request.urlopen(f"{dash}/assets/family/{n}.svg") as r:
            assert r.status == 200 and r.headers["Content-Type"] == "image/svg+xml" and b"<svg" in r.read(40)
    with pytest.raises(Exception):
        urllib.request.urlopen(f"{dash}/assets/family/../../server.py")


# ---------- OpenBlocks ----------

FAKE_OB = textwrap.dedent('''
    from mcp.server.mcpserver import MCPServer
    s = MCPServer("openblocks")

    @s.tool()
    def search_pages(query: str, limit: int = 5) -> str:
        return f"- Ideas/Plan.md · Plan de {query} (límite {limit})"

    s.run()
''')


def test_openblocks_search_over_stdio(tmp_path, monkeypatch):
    script = tmp_path / "fake_openblocks.py"
    script.write_text(FAKE_OB, encoding="utf-8")
    out = asyncio.run(family._openblocks_call(sys.executable, "viaje", 3, 60, prefix=(str(script),)))
    assert "Plan de viaje" in out and "límite 3" in out
    monkeypatch.setattr(family, "openblocks_cli", lambda: None)
    with pytest.raises(family.FamilyError) as e:
        family.openblocks_search("x")
    assert e.value.code == "openblocks_missing"
    family.save({"openblocks": False})
    with pytest.raises(family.FamilyError) as e:
        family.openblocks_search("x")
    assert e.value.code == "openblocks_off"


def _events(gen):
    return list(gen)


def test_chat_openblocks_toggle_adds_pages_for_an_agent(monkeypatch):
    monkeypatch.setattr(family, "openblocks_search", lambda q, k=6, timeout=30: "- Notas/Idea.md · Idea de la app")
    monkeypatch.setattr(family, "opencue_agents", lambda: [{"id": "a1", "name": "pacman"}, {"id": "a2", "name": "otro"}])
    sent = []
    monkeypatch.setattr(family, "opencue_chat", lambda agent, msg, timeout=0, start_app=True: sent.append(msg) or "ok")
    monkeypatch.setattr(chat, "_save", lambda *a, **k: None)
    monkeypatch.setattr(chat, "get_chat", lambda cid: (_ for _ in ()).throw(chat.ChatError("not_found", cid)))
    evs = _events(chat.run("¿Qué idea anoté?", "opencue:pacman", None, "es", openblocks=True))
    assert "Idea de la app" in sent[0] and sent[0].rstrip().endswith("¿Qué idea anoté?")
    assert [e["name"] for e in evs if e["type"] == "tool"][:2] == ["openblocks_search", "OpenCue → pacman"]
    sent.clear()
    monkeypatch.setattr(family, "openblocks_search", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debería buscar")))
    _events(chat.run("hola", "opencue:pacman", None, "es"))
    assert "OpenBlocks" not in sent[0]


def test_openblocks_missing_is_a_notice(monkeypatch):
    def missing(*a, **k):
        raise family.FamilyError("openblocks_missing")

    monkeypatch.setattr(family, "openblocks_search", missing)
    monkeypatch.setattr(family, "opencue_chat", lambda *a, **k: "ok")
    monkeypatch.setattr(chat, "_save", lambda *a, **k: None)
    monkeypatch.setattr(chat, "get_chat", lambda cid: (_ for _ in ()).throw(chat.ChatError("not_found", cid)))
    evs = _events(chat.run("hola", "opencue:pacman", None, "es", openblocks=True))
    assert any(e["type"] == "notice" and e["code"] == "openblocks_missing" for e in evs)
    assert any(e["type"] == "token" for e in evs)


def test_openblocks_tool_is_registered_and_not_read_only():
    import server

    assert "openblocks_search" in {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert "openblocks_search" not in server.READ_ONLY_TOOLS
