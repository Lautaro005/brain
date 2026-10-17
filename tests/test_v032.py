"""v0.03.2: archivos centrales que las IAs no tocan (BRAIN.md, FORMAT.md, FACT-CHECK.md y las carpetas base), editor del
grafo, FACT-CHECK.md unido a la vista Fact check, estructura del grafo, títulos cortos de fuentes y Obsidian."""
import json

import pytest

from tests.test_dashboard import _get, dash  # noqa: F401  (fixture)
from tests.test_v031 import _post


def _note(name, extra=""):
    return f"---\nname: {name}\ndescription: x\n{extra}---\nhola\n"


# ---------- estructura base ----------

def test_core_files_and_brain_block():
    from brain_mcp import history, vault

    for f in ("BRAIN.md", "FORMAT.md", "FACT-CHECK.md"):
        assert (vault.VAULT / f).is_file()
    brain = vault.read_file("BRAIN.md")
    assert vault.CORE_START in brain and "[FACT-CHECK.md](FACT-CHECK.md)" in brain and "[FORMAT.md](FORMAT.md)" in brain
    fmt = vault.read_file("FORMAT.md")
    assert "## Estructura" in fmt and "FACT-CHECK.md" in fmt and "tag del proyecto" in fmt
    fc = vault.read_file("FACT-CHECK.md")
    assert vault.PREFS_START in fc and "Buscar en internet: permitido" in fc
    assert vault.parse(fc)[0]["type"] == "Reference" and vault.note_type("FACT-CHECK.md") == "Reference"
    # idempotente: abrir de nuevo no escribe nada
    n = len(history.list_changes(limit=500))
    vault.ensure_vault()
    assert len(history.list_changes(limit=500)) == n


def test_brain_migration_keeps_user_text():
    from brain_mcp import vault

    old = ("---\ntype: Index\nname: brain\ndescription: Índice\n---\n# Brain — índice\n\n"
           "- Perfil del usuario: [profile.md](profile.md) (leelo primero para saber con quién hablás)\n"
           "- Proyectos: `projects/`\n- Mi lista propia\n\n## Formato de las notas\n"
           "(Markdown estándar, más portable) o `[[nombre]]`.\n\nNota mía: [[algo]].\n")
    (vault.VAULT / "BRAIN.md").write_text(old, encoding="utf-8")
    vault.ensure_brain_note()
    text = vault.read_file("BRAIN.md")
    assert text.index(vault.CORE_START) > text.index("# Brain — índice")
    assert "- Proyectos: `projects/`" not in text and "## Formato de las notas" not in text
    assert "- Mi lista propia" in text and "Nota mía: [[algo]]." in text
    vault.ensure_brain_note()  # una segunda vez no duplica el bloque
    assert vault.read_file("BRAIN.md").count(vault.CORE_START) == 1


# ---------- lo que las IAs no pueden tocar ----------

def test_agents_cannot_touch_core_files():
    import server
    from brain_mcp import vault

    before = vault.read_file("FORMAT.md")
    for out in (server.write_file("BRAIN.md", "# hack"), server.append_file("FORMAT.md", "x"),
                server.str_replace_file("FACT-CHECK.md", "# Fact check", "# X"),
                server.set_frontmatter("FORMAT.md", {"description": "x"}), server.delete_file("FACT-CHECK.md"),
                server.move_file("FORMAT.md", "otra.md"), server.restore_file("BRAIN.md", 1)):
        assert out.startswith("Error") and ("archivo central" in out or "lo maneja brain" in out), out
    assert vault.read_file("FORMAT.md") == before and (vault.VAULT / "FACT-CHECK.md").is_file()
    # tampoco se puede pisar uno moviendo otra nota encima, ni tratar una carpeta como nota
    server.write_file("idea.md", _note("idea"))
    assert server.move_file("idea.md", "BRAIN.md").startswith("Error")
    assert server.write_file("projects", "x").startswith("Error")
    # adentro de las carpetas se escribe normal
    assert server.write_file("projects/brasa.md", _note("brasa")).startswith("OK")
    # mover la última nota de una carpeta base no borra la carpeta
    server.write_file("skills/unica.md", _note("unica"))
    assert server.move_file("skills/unica.md", "unica.md").startswith("OK")
    assert (vault.VAULT / "skills").is_dir()


def test_graph_proposal_cannot_change_core():
    from brain_mcp import graph_proposals, vault

    vault.write_file("projects/brasa.md", _note("brasa"))
    with pytest.raises(graph_proposals.ProposalError) as e:
        graph_proposals.propose([{"type": "add_link", "source": "FORMAT.md", "target": "brasa"}])
    assert "archivo central" in (e.value.detail or str(e.value))


# ---------- editor del usuario ----------

def test_user_save_core_and_managed():
    from brain_mcp import vault

    raw = vault.raw_file("FORMAT.md")
    assert raw["core"] and raw["managed"]
    edited = raw["content"].rstrip() + "\n\nRegla mía.\n"
    r = vault.user_save("FORMAT.md", edited, raw["sha"])
    assert r["changed"] and r["released"]
    text = vault.read_file("FORMAT.md")
    assert "Regla mía." in text and vault.parse(text)[0]["managed"] == "user"
    vault.FORMAT_VERSION = "999"  # una versión nueva de la plantilla ya no lo pisa
    try:
        vault.ensure_format_note()
    finally:
        vault.FORMAT_VERSION = "3"
    assert "Regla mía." in vault.read_file("FORMAT.md")
    # versión vieja: stale
    with pytest.raises(vault.VaultError, match="stale"):
        vault.user_save("FORMAT.md", "otro", raw["sha"])
    b = vault.raw_file("BRAIN.md")
    assert vault.user_save("BRAIN.md", b["content"] + "\nMío.\n", b["sha"])["changed"]
    assert vault.user_save("BRAIN.md", vault.read_file("BRAIN.md"))["changed"] is False
    (vault.VAULT / "x.txt").write_text("a")
    with pytest.raises(vault.VaultError):
        vault.user_save("x.txt", "b")


def test_editor_api(dash):  # noqa: F811
    r = json.loads(_get(dash + "/api/vault/raw?path=BRAIN.md")[1])
    assert r["ok"] and r["core"] and r["content"].startswith("---")
    s = json.loads(_post(dash + "/api/vault/save", {"path": "BRAIN.md", "content": r["content"] + "\nDesde el editor.\n", "sha": r["sha"]}))
    assert s["ok"] and s["changed"]
    stale = json.loads(_post(dash + "/api/vault/save", {"path": "BRAIN.md", "content": "x", "sha": r["sha"]}))
    assert not stale["ok"] and stale["code"] == "stale"
    assert json.loads(_get(dash + "/api/file?path=FORMAT.md")[1])["core"] is True


# ---------- FACT-CHECK.md y la vista Fact check ----------

def test_factcheck_prefs_sync():
    from brain_mcp import factcheck, vault

    factcheck.save_prefs({"enabled": False, "level": "thorough", "blocked_domains": ["spam.example"]})
    text = vault.read_file("FACT-CHECK.md")
    assert "**no** (solo lo que hay en brain)" in text and "exhaustivo" in text and "spam.example" in text
    # si el usuario sacó las marcas, no se toca
    body = text.replace(vault.PREFS_START, "").replace(vault.PREFS_END, "")
    raw = vault.raw_file("FACT-CHECK.md")
    vault.user_save("FACT-CHECK.md", body, raw["sha"])
    factcheck.save_prefs({"enabled": True})
    assert "**no** (solo lo que hay en brain)" in vault.read_file("FACT-CHECK.md")


def test_chat_and_server_point_to_factcheck_md():
    import server
    from brain_mcp import chat

    assert "FACT-CHECK.md" in server.INSTRUCTIONS and "FORMAT.md" in server.INSTRUCTIONS
    short = chat._context("hola", "c1", "es")
    assert "# Cuando brain no alcanza" in short and "## 4. Qué fuentes cuentan" not in short
    full = chat._context("hola", "c1", "es", command="factcheck")
    assert "## 4. Qué fuentes cuentan" in full
    assert "BRAIN.md, FORMAT.md, FACT-CHECK.md" in chat.HARNESS


# ---------- grafo ----------

def test_graph_structure_and_short_sources():
    from brain_mcp import graph, vault

    vault.write_file("knowledge/sources/developers-cloudflare-com-cloudflare-one-connections-connect-networks-ab12cd.md",
                     _note("developers-cloudflare-com-cloudflare-one-connections-connect-networks-ab12cd",
                           "title: Cloudflare Tunnel · Cloudflare Zero Trust docs\nurl: https://developers.cloudflare.com/x\n"))
    vault.write_file("knowledge/sources/www-example-com-blog-a-very-long-slug.md",
                     _note("www-example-com-blog-a-very-long-slug", "url: https://www.example.com/blog/2026/a-very-long-slug-about-many-things\n"))
    g = graph.build_graph()
    nodes = {n["id"]: n for n in g["nodes"]}
    links = {(l["source"], l["target"]): l["kind"] for l in g["links"]}
    assert nodes["FORMAT.md"]["kind"] == nodes["FACT-CHECK.md"]["kind"] == "core" and nodes["FORMAT.md"]["label"] == "FORMAT.md"
    assert links[("BRAIN.md", "FORMAT.md")] == links[("BRAIN.md", "FACT-CHECK.md")] == "core"
    for d in ("projects", "skills", "memory", "knowledge"):  # aunque estén vacías
        assert nodes[f"dir:{d}"]["core"] and links[("BRAIN.md", f"dir:{d}")] == "folder"
    labels = sorted(n["label"] for n in g["nodes"] if n["kind"] == "source")
    assert labels == ["Cloudflare Tunnel", "a very long slug about many…"]
    assert all(len(x) <= graph.SHORT + 1 for x in labels)
    # FORMAT.md editado por el usuario (managed: user) sigue sin "mencionar" las carpetas que nombra
    raw = vault.raw_file("FORMAT.md")
    vault.user_save("FORMAT.md", raw["content"] + "\nMío.\n", raw["sha"])
    g2 = graph.build_graph()
    assert not [l for l in g2["links"] if l["source"] == "FORMAT.md" and l["kind"] == "mention"]
    full = next(n for n in g["nodes"] if n["label"] == "Cloudflare Tunnel")
    assert full["title"].startswith("Cloudflare Tunnel ·")


def test_short_label():
    from brain_mcp.graph import short_label

    assert short_label({"title": "Guía — Sitio"}, "x") == "Guía — Sitio"
    assert short_label({"url": "https://github.com/"}, "x") == "github.com"
    assert short_label({"url": "https://es.wikipedia.org/wiki/Zettelkasten"}, "x") == "Zettelkasten"
    assert short_label({"url": "https://cnn.com/a-b"}, "x") == "cnn.com · a b"
    assert short_label({}, "nota-larga") == "nota-larga"


# ---------- Obsidian ----------

@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    from brain_mcp import agents, obsidian

    home = tmp_path / "home"
    monkeypatch.setattr(agents, "HOME", home)
    monkeypatch.setattr(agents, "APPS", tmp_path / "Applications")
    monkeypatch.setattr(agents, "_app_data", lambda: home / "appdata")
    monkeypatch.setattr(obsidian, "_running", lambda: False)
    monkeypatch.setattr(obsidian.shutil, "which", lambda name: None)
    return home


def test_obsidian_open_and_register(fake_home, monkeypatch):
    from brain_mcp import obsidian, vault

    st = obsidian.status()
    assert not st["installed"] and st["uri"].startswith("obsidian://open?path=")
    with pytest.raises(obsidian.ObsidianError) as e:
        obsidian.open_vault()
    assert e.value.code == "not_installed"

    cfg = fake_home / "appdata/obsidian/obsidian.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"vaults": {"aaaa": {"path": "/otro/vault", "ts": 1, "open": True}}, "frame": "hidden"}))
    r = obsidian.open_vault()
    assert r["added"] and r["registered"] and r["configured_now"]
    data = json.loads(cfg.read_text())
    assert data["frame"] == "hidden" and data["vaults"]["aaaa"]["path"] == "/otro/vault"  # no toca lo ajeno
    assert any(v["path"] == str(vault.VAULT) for v in data["vaults"].values())
    assert (cfg.parent / "obsidian.json.bak-brain").is_file()
    app = json.loads((vault.VAULT / ".obsidian/app.json").read_text())
    assert app["useMarkdownLinks"] is True
    # la segunda vez no escribe nada, y no pisa la config de .obsidian
    (vault.VAULT / ".obsidian/app.json").write_text("{}")
    r2 = obsidian.open_vault()
    assert not r2["added"] and not r2["configured_now"] and (vault.VAULT / ".obsidian/app.json").read_text() == "{}"
    # .obsidian no aparece en el vault de los agentes ni en el grafo
    assert not any(f["path"].startswith(".obsidian") for f in vault.list_files())


def test_obsidian_running_and_bad_config(fake_home, monkeypatch):
    from brain_mcp import obsidian

    cfg = fake_home / "appdata/obsidian/obsidian.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"vaults": {}}))
    monkeypatch.setattr(obsidian, "_running", lambda: True)
    with pytest.raises(obsidian.ObsidianError) as e:
        obsidian.open_vault()
    assert e.value.code == "running" and json.loads(cfg.read_text()) == {"vaults": {}}
    monkeypatch.setattr(obsidian, "_running", lambda: False)
    cfg.write_text("{no es json")
    with pytest.raises(obsidian.ObsidianError) as e:
        obsidian.open_vault()
    assert e.value.code == "bad_config" and cfg.read_text() == "{no es json"


def test_obsidian_api(dash, fake_home):  # noqa: F811
    st = json.loads(_get(dash + "/api/obsidian")[1])
    assert st["ok"] and st["installed"] is False
    r = json.loads(_post(dash + "/api/obsidian/open", {}))
    assert not r["ok"] and r["code"] == "not_installed" and r["download"].startswith("https://obsidian.md")
