"""v0.03.1.1: Fact check para todos los agentes, buscador desde las Conexiones, "Sobre mí" automático, tipos
Project / SubProject / File en el grafo y FORMAT.md."""
import asyncio
import json

import pytest

from tests.test_dashboard import dash  # noqa: F401  (fixture)


# ---------- tipos de nota y grafo ----------

def test_project_subproject_file_types():
    from brain_mcp import vault

    cases = {"projects/app.md": "Project", "projects/index.md": "Index", "projects/app/index.md": "Project",
             "projects/app/app.md": "Project", "projects/app/notas.md": "File", "projects/app/web/README.md": "SubProject",
             "projects/app/web/spec.md": "File", "suelta.md": "Note", "FORMAT.md": "Reference"}
    for rel, want in cases.items():
        assert vault.note_type(rel) == want, rel
    # el `Project` que v0.03.1 puso en todo projects/ no tapa la ubicación; un type explícito distinto sí manda
    assert vault.effective_type("projects/app/notas.md", {"type": "Project"}) == "File"
    assert vault.effective_type("projects/app/notas.md", {"type": "Reference"}) == "Reference"
    vault.write_file("projects/app/notas.md", "---\nname: notas\ndescription: d\n---\nx\n")
    assert vault.parse(vault.read_file("projects/app/notas.md"))[0]["type"] == "File"


def test_backfill_fixes_legacy_project_type():
    from brain_mcp import vault

    (vault.VAULT / "projects/app").mkdir(parents=True)
    p = vault.VAULT / "projects/app/notas.md"
    p.write_text("---\ntype: Project\nname: notas\ndescription: d\ncreated: 2026-01-01\nupdated: 2026-01-02\n---\nx\n")
    assert vault.missing_metadata() >= 1
    assert "projects/app/notas.md" in vault.backfill_metadata()["updated"]
    meta = vault.parse(p.read_text())[0]
    assert meta["type"] == "File" and str(meta["updated"]) == "2026-01-02"


def test_graph_kinds_and_part_links():
    from brain_mcp import graph, vault

    vault.write_file("projects/app/index.md", "---\nname: app\ndescription: el proyecto\n---\nx\n")
    vault.write_file("projects/app/notas.md", "---\nname: notas\ndescription: apuntes\n---\nx\n")
    vault.write_file("projects/app/web/index.md", "---\nname: web\ndescription: el sitio\n---\nx\n")
    vault.write_file("projects/app/web/spec.md", "---\nname: spec\ndescription: spec\n---\nx\n")
    vault.write_file("projects/solo.md", "---\nname: solo\ndescription: otro\n---\nx\n")
    g = graph.build_graph()
    kind = {n["id"]: n["kind"] for n in g["nodes"]}
    assert kind["projects/app/index.md"] == "project" and kind["projects/solo.md"] == "project"
    assert kind["projects/app/web/index.md"] == "subproject"
    assert kind["projects/app/notas.md"] == "file" and kind["projects/app/web/spec.md"] == "file"
    parts = {(l["source"], l["target"]) for l in g["links"] if l["kind"] == "part"}
    assert ("projects/app/notas.md", "projects/app/index.md") in parts
    assert ("projects/app/web/index.md", "projects/app/index.md") in parts
    assert ("projects/app/web/spec.md", "projects/app/web/index.md") in parts


def test_format_note_for_agents():
    from brain_mcp import vault

    text = vault.read_file("FORMAT.md")  # lo crea ensure_vault (conftest)
    for t in ("Project", "SubProject", "File", "Memory", "Skill", "Source", "Reference", "Note"):
        assert f"`{t}`" in text
    meta = vault.parse(text)[0]
    assert meta["managed"] == "brain" and meta["type"] == "Reference"
    # si el usuario le saca `managed: brain`, brain no la vuelve a escribir
    vault.write_file("FORMAT.md", "---\nname: format\ndescription: mía\n---\nmi versión\n")
    vault.ensure_format_note()
    assert "mi versión" in vault.read_file("FORMAT.md")
    import server
    assert "SubProject" in server.mcp.instructions and "FORMAT.md" in server.mcp.instructions


# ---------- "Sobre mí" automático ----------

def test_auto_about(monkeypatch):
    from brain_mcp import memory, summarize

    monkeypatch.setattr(summarize, "generate", lambda prompt, fmt=None: None)  # sin modelo: viñetas
    memory.save_profile("Ana", "Diseñadora", "Me gusta lo simple.")
    assert memory.refresh_auto_about()["status"] == "empty"
    memory.add(["Trabaja en una tienda online"], "Trabajo")
    memory.add(["Prefiere respuestas cortas"], "Preferencias")
    assert memory.auto_about_stale()
    r = memory.refresh_auto_about()
    assert r["status"] == "updated" and "**Trabajo**" in r["text"]
    p = memory.get_profile()
    assert p["about"] == "Me gusta lo simple." and "tienda online" in p["auto_about"] and p["auto_updated"]
    assert memory.refresh_auto_about()["status"] == "unchanged" and not memory.auto_about_stale()
    # guardar el perfil a mano conserva el resumen
    memory.save_profile("Ana Ruiz", "Diseñadora", "Otro texto.")
    p = memory.get_profile()
    assert p["name"] == "Ana Ruiz" and p["about"] == "Otro texto." and "tienda online" in p["auto_about"]
    # con modelo, el párrafo del modelo
    monkeypatch.setattr(summarize, "generate", lambda prompt, fmt=None: "Trabajás en una tienda online.")
    memory.add(["Vive en Rosario"], "General")
    assert memory.refresh_auto_about()["text"] == "Trabajás en una tienda online."
    memory.set_auto_about(False)
    memory.add(["Toma mate"], "General")
    assert memory.refresh_auto_about()["status"] == "off"
    assert memory.refresh_auto_about(force=True)["status"] == "updated"
    # el chat lo recibe
    from brain_mcp import chat
    assert "Lo que brain sabe de vos" in chat._context("hola", "x", "es")


def test_profile_auto_endpoint(dash, monkeypatch):  # noqa: F811
    from brain_mcp import memory, summarize
    from tests.test_dashboard import _get
    from tests.test_v031 import _post

    monkeypatch.setattr(summarize, "generate", lambda prompt, fmt=None: None)
    memory.add(["Trabaja en brain"], "Trabajo")
    r = json.loads(_get(dash + "/api/profile")[1])
    assert r["auto_about"] == {"enabled": True, "stale": True}
    r = json.loads(_post(dash + "/api/profile/auto", {"refresh": True}))
    assert r["status"] == "updated" and "brain" in r["profile"]["auto_about"]
    r = json.loads(_post(dash + "/api/profile/auto", {"enabled": False}))
    assert r["enabled"] is False and not memory.auto_about_enabled()


# ---------- Fact check para todos los agentes ----------

def test_search_knowledge_suggests_fact_check(monkeypatch):
    import server
    from brain_mcp import factcheck

    monkeypatch.setattr(server, "search_core", lambda q, k=5: ([], None, None))
    out = asyncio.run(server.mcp.call_tool("search_knowledge", {"query": "x"})).content[0].text
    assert "fact_check" not in out
    factcheck.save_prefs({"chat_auto": True})
    out = asyncio.run(server.mcp.call_tool("search_knowledge", {"query": "x"})).content[0].text
    assert "fact_check" in out
    hit = {"text": "t", "url": None, "source_md_path": "a.md", "chunk_index": 0, "score": 0.5, "rrf": 0.1, "match": "both"}
    monkeypatch.setattr(server, "search_core", lambda q, k=5: ([hit], None, None))
    out = asyncio.run(server.mcp.call_tool("search_knowledge", {"query": "x"}))
    assert "fact_check" in json.dumps(out.structured_content or [c.text for c in out.content], ensure_ascii=False)


def test_fact_check_tool_uses_prefs_without_blocking(monkeypatch):
    import server
    from brain_mcp import factcheck

    seen = {}

    def fake(text, lang="es", allow_web=None):
        seen["level"] = factcheck.prefs()["level"]
        return {"id": "fc1", "created_at": "2026-10-05T00:00:00Z", "input": text, "lang": "es", "claims": [], "notes": []}

    monkeypatch.setattr(factcheck, "run_sync", fake)
    factcheck.save_prefs({"level": "thorough"})
    out = asyncio.run(server.mcp.call_tool("fact_check", {"text": "algo"})).content[0].text
    assert "fc1" in out and seen["level"] == "thorough"


# ---------- buscador desde las Conexiones ----------

def test_factcheck_connection_provider(monkeypatch):
    from brain_mcp import connectors, factcheck as fc
    from mcp.types import TextContent

    tool = {"name": "openseo__get_serp_results", "description": "[OpenSEO] Fetch live Google organic search results",
            "conn_id": "openseo", "tool": "get_serp_results",
            "input_schema": {"properties": {"projectId": {"type": "string"},
                                            "queries": {"type": "array", "items": {"type": "object", "properties": {
                                                "keyword": {"type": "string"}, "languageCode": {"type": "string"}}}}}}}
    other = {"name": "gmail__send", "description": "[Gmail] Send", "conn_id": "g", "tool": "send",
             "input_schema": {"properties": {"to": {"type": "string"}}}}
    monkeypatch.setattr(connectors, "proxied_tools", lambda: [other, tool])
    calls = []

    async def fake_call(conn_id, name, args, capture_result=True):
        calls.append((conn_id, name, args, capture_result))
        return [TextContent(type="text", text=json.dumps({"results": [{"keyword": "x", "items": [
            {"url": "https://www.indec.gob.ar/a", "title": "INDEC", "description": "dato"},
            {"url": "https://diario.com/b", "title": "Diario"}]}]}))], False

    monkeypatch.setattr(connectors, "call", fake_call)
    assert [t["name"] for t in fc.search_tools()] == ["openseo__get_serp_results"]  # gmail no sirve de buscador
    with pytest.raises(fc.FactCheckError):
        fc.save_prefs({"provider": "connection"})
    with pytest.raises(fc.FactCheckError):
        fc.save_prefs({"provider": "connection", "connection_tool": tool["name"], "connection_args": "{no json"})
    p = fc.save_prefs({"provider": "connection", "connection_tool": tool["name"], "connection_args": '{"projectId": "p1"}'})
    assert p["connection_args"] == {"projectId": "p1"}
    res = fc.search_web("inflación marzo", fc.prefs(), 30)
    assert [r["url"] for r in res] == ["https://www.indec.gob.ar/a", "https://diario.com/b"] and res[0]["title"] == "INDEC"
    conn_id, name, args, capture = calls[0]
    assert (conn_id, name) == ("openseo", "get_serp_results") and capture is False  # no se guarda en el vault
    assert args == {"projectId": "p1", "queries": [{"keyword": "inflación marzo", "languageCode": "es"}]}
    # restaurar valores recomendados conserva el buscador elegido
    assert fc.save_prefs({"reset": True})["provider"] == "connection"
    # sin JSON: las URLs del texto
    assert [r["url"] for r in fc.parse_tool_results(["Ver https://a.org/x, y https://b.com/y."])] == ["https://a.org/x", "https://b.com/y"]
