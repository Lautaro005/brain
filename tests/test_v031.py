"""v0.03.1: formato de las notas (type/created/updated), chunks por estructura, cambios en el grafo con
propuesta y aprobación, harness del chat y Fact check (sin red ni Ollama: todo lo externo se simula)."""
import json
import os
import time
import urllib.request
from datetime import date

import pytest

from tests.test_dashboard import _get, dash  # noqa: F401  (fixture)

TODAY = date.today().isoformat()


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Brain": "1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


# ---------- formato de las notas ----------

def test_notes_get_type_and_dates():
    from brain_mcp import vault

    vault.write_file("projects/demo.md", "---\nname: demo\ndescription: un proyecto\n---\nhola\n")
    meta = vault.parse(vault.read_file("projects/demo.md"))[0]
    assert meta["type"] == "Project" and str(meta["created"]) == TODAY and str(meta["updated"]) == TODAY
    # el orden y el resto del frontmatter no se tocan
    assert vault.read_file("projects/demo.md").startswith("---\ntype: Project\nname: demo\n")
    # created se conserva aunque el agente reescriba el archivo sin ese campo
    p = vault.VAULT / "projects/demo.md"
    p.write_text(p.read_text().replace(f"created: {TODAY}", "created: 2024-05-01").replace(f"updated: {TODAY}", "updated: 2024-05-02"))
    vault.write_file("projects/demo.md", "---\nname: demo\ndescription: otro\n---\nchau\n")
    meta = vault.parse(vault.read_file("projects/demo.md"))[0]
    assert str(meta["created"]) == "2024-05-01" and str(meta["updated"]) == TODAY
    # sin frontmatter: se agrega uno con los tres campos
    vault.write_file("memory/x.md", "- algo\n")
    assert vault.read_file("memory/x.md").startswith(f"---\ntype: Memory\ncreated: {TODAY}\nupdated: {TODAY}\n---\n")
    assert vault.note_type("knowledge/sources/a.md") == "Source" and vault.note_type("BRAIN.md") == "Index"
    assert vault.note_type("skills/s.md") == "Skill" and vault.note_type("suelta.md") == "Note"


def test_bookkeeping_fields_dont_touch_updated():
    from brain_mcp import vault

    p = vault.VAULT / "knowledge/sources/s.md"
    p.write_text("---\ntype: Source\nname: s\ndescription: d\ncreated: 2024-01-01\nupdated: 2024-01-02\n---\ntexto\n")
    vault.set_frontmatter("knowledge/sources/s.md", {"checked_at": "2026-01-01T00:00:00Z"})
    assert str(vault.parse(p.read_text())[0]["updated"]) == "2024-01-02"
    vault.set_frontmatter("knowledge/sources/s.md", {"description": "nueva"})
    assert str(vault.parse(p.read_text())[0]["updated"]) == TODAY


def test_backfill_metadata():
    from brain_mcp import history, vault

    (vault.VAULT / "projects").mkdir(exist_ok=True)
    old = vault.VAULT / "projects/viejo.md"
    old.write_text("---\nname: viejo\ndescription: sin fechas\n---\nx\n")
    t = time.mktime((2023, 3, 4, 12, 0, 0, 0, 0, -1))
    os.utime(old, (t, t))
    assert vault.missing_metadata() >= 1
    r = vault.backfill_metadata()
    assert "projects/viejo.md" in r["updated"]
    meta = vault.parse(old.read_text())[0]
    assert meta["type"] == "Project" and str(meta["created"]) == "2023-03-04" and str(meta["updated"]) == "2023-03-04"
    assert history.list_changes(path="projects/viejo.md")[0]["op"] == "format"
    assert vault.backfill_metadata()["count"] == 0  # idempotente
    assert vault.missing_metadata() == 0


def test_structural_chunks():
    from brain_mcp.chunking import chunk_sections, chunk_text

    text = "# Intro\n\nprimer párrafo corto\n\n```py\nprint(1)\n\nprint(2)\n```\n\n## Detalle\n\n" + "palabra " * 700
    parts = chunk_sections(text, max_words=300, overlap=20)
    assert parts[0]["section"] == "Intro" and parts[0]["text"].startswith("# Intro primer")
    assert all(p["section"] for p in parts)
    # un chunk que arranca a mitad de una sección lleva su título adelante
    assert any(p["text"].startswith("[Detalle]") for p in parts[1:])
    # el bloque de código no se corta por la línea en blanco de adentro
    assert any("print(1) print(2) ```" in p["text"] for p in parts)
    assert chunk_text("a b c") == ["a b c"]


# ---------- grafo: propuesta → aprobación ----------

def _notes():
    from brain_mcp import vault

    vault.write_file("projects/brain-project.md", "---\nname: brain-project\ndescription: x\n---\nhola\n")
    vault.write_file("knowledge/mcp.md", "---\nname: MCP\ndescription: protocolo\n---\nmcp\n")
    vault.write_file("knowledge/otro.md", "---\nname: otro\ndescription: o\n---\nver [[brain-project]]\n")


def test_graph_proposal_lifecycle():
    from brain_mcp import graph_proposals as gp, vault

    _notes()
    before = vault.read_file("projects/brain-project.md")
    p = gp.propose([{"type": "add_link", "source": "brain-project", "target": "MCP", "label": "usa"}], "conectar")
    assert p["status"] == "pending" and p["summary"] == {"add": 1, "remove": 0, "nodes": 2}
    assert vault.read_file("projects/brain-project.md") == before  # proponer no cambia nada
    svg = gp.preview(p["id"])
    assert svg.startswith("<svg") and "stroke-dasharray" in svg and "#1a7f37" in svg and "<script" not in svg
    assert gp.approve(p["id"])["status"] == "applied"
    assert vault.parse(vault.read_file("projects/brain-project.md"))[0]["related"] == ["mcp"]
    with pytest.raises(gp.ProposalError) as e:
        gp.approve(p["id"])  # aprobar dos veces no duplica
    assert e.value.code == "not_pending"
    assert vault.parse(vault.read_file("projects/brain-project.md"))[0]["related"] == ["mcp"]
    # quitar la conexión, y deshacer
    p2 = gp.propose([{"type": "remove_link", "source": "projects/brain-project.md", "target": "mcp"}])
    before_rm = vault.read_file("projects/brain-project.md")
    gp.approve(p2["id"])
    assert "related" not in vault.parse(vault.read_file("projects/brain-project.md"))[0]
    assert gp.undo(p2["id"])["status"] == "undone"
    assert vault.read_file("projects/brain-project.md") == before_rm  # deshacer deja el archivo exacto


def test_graph_proposal_validation_and_stale():
    from brain_mcp import graph_proposals as gp, vault

    _notes()
    with pytest.raises(gp.ProposalError) as e:
        gp.propose([{"type": "add_link", "source": "no-existe", "target": "MCP"}])
    assert "no existe" in e.value.detail
    with pytest.raises(gp.ProposalError) as e:  # el link está en el texto: no se saca por acá
        gp.propose([{"type": "remove_link", "source": "otro", "target": "brain-project"}])
    assert "link en el texto" in e.value.detail
    with pytest.raises(gp.ProposalError) as e:  # ya conectadas
        gp.propose([{"type": "add_link", "source": "otro", "target": "brain-project"}])
    assert "ya está conectada" in e.value.detail
    with pytest.raises(gp.ProposalError):
        gp.propose([{"type": "add_link", "source": "a", "target": "b"}] * 11)
    vault.write_file("memory/general.md", "- algo\n")
    with pytest.raises(gp.ProposalError):
        gp.propose([{"type": "add_link", "source": "memory/general.md", "target": "MCP"}])
    p = gp.propose([{"type": "add_link", "source": "brain-project", "target": "MCP"}])
    vault.append_file("projects/brain-project.md", "\notra línea\n")  # cambió después de proponer
    with pytest.raises(gp.ProposalError) as e:
        gp.approve(p["id"])
    assert e.value.code == "stale" and gp.get(p["id"])["status"] == "stale"
    p3 = gp.propose([{"type": "add_link", "source": "brain-project", "target": "MCP"}])
    assert gp.reject(p3["id"], "no")["status"] == "rejected"
    region = gp.inspect(["otro", "nada"])
    assert region["missing"] == ["nada"] and any(n["id"] == "projects/brain-project.md" for n in region["nodes"][0]["neighbors"])


def test_graph_proposal_tool_and_api(dash):  # noqa: F811
    import asyncio

    import server

    _notes()
    r = asyncio.run(server.mcp.call_tool("propose_graph_change", {"changes": [{"type": "add_link", "source": "brain-project", "target": "MCP"}], "reason": "r"}))
    text = r.content[0].text
    pid = json.loads(text)["proposal"]["id"]
    listing = json.loads(_get(dash + "/api/graph/proposals?status=pending")[1])["proposals"]
    assert [x["id"] for x in listing] == [pid]
    st, svg = _get(dash + f"/api/graph/proposals/{pid}/preview.svg")
    assert st == 200 and svg.startswith(b"<svg")
    assert json.loads(_post(dash + f"/api/graph/proposals/{pid}/approve", {}))["proposal"]["status"] == "applied"
    again = json.loads(_post(dash + f"/api/graph/proposals/{pid}/approve", {}))
    assert again == {"ok": False, "code": "not_pending", "detail": "applied"}


# ---------- harness del chat ----------

def test_chat_repeated_tool_call_and_final_answer(monkeypatch):
    from brain_mcp import chat

    calls = []

    def fake_stream(model, messages, tools, num_ctx=0):
        calls.append(tools is not None)
        if tools is None:  # la vuelta final, sin tools
            yield {"message": {"content": "Listo: no encontré nada."}}
            yield {"done": True}
            return
        yield {"message": {"content": "", "tool_calls": [{"function": {"name": "list_vault", "arguments": {}}}]}}
        yield {"done": True}

    ran = []
    monkeypatch.setattr(chat, "_stream", fake_stream)
    monkeypatch.setattr(chat, "_run_tool", lambda n, a, v: (ran.append(n), ("[]", True))[1])
    monkeypatch.setattr(chat, "_save", lambda *a, **k: None)
    evs = list(chat.run("qué hay?", "fake"))
    assert ran == ["list_vault"]  # la misma llamada no se vuelve a ejecutar
    results = [e for e in evs if e["type"] == "tool_result"]
    assert len(results) > 1 and "no la repitas" in results[1]["result"].lower()
    assert calls[-1] is False and "Listo: no encontré nada." in "".join(e.get("text", "") for e in evs if e["type"] == "token")
    sysmsg = chat._context("hola", "x", "es")
    assert "Cómo trabajar" in sysmsg and "propose_graph_change" in sysmsg and "HTML" in sysmsg


def test_chat_factcheck_command():
    from brain_mcp import chat

    assert chat.parse_command("/factcheck la luna es de queso") == ("factcheck", "la luna es de queso")
    assert "fact_check" in chat._command_prompt("factcheck")


# ---------- Fact check ----------

def test_factcheck_helpers():
    from brain_mcp import factcheck as fc

    assert fc.source_type("https://www.indec.gob.ar/x") == "official"
    assert fc.source_type("https://pubmed.ncbi.nlm.nih.gov/123") == "academic"
    assert fc.source_type("https://chequeado.com/el-explicador/x") == "factchecker"
    assert fc.source_type("https://es.wikipedia.org/wiki/X") == "reference"
    assert fc.source_type("https://x.com/alguien/status/1") == "social"
    assert fc.source_type("https://www.lanacion.com.ar/x") == "news"
    assert fc.site_of("https://a.b.lanacion.com.ar/x") == "lanacion.com.ar" and fc.site_of("https://news.bbc.co.uk") == "bbc.co.uk"
    assert fc.quote_in("La inflación fue del 3,5 %", "texto… “la inflación fue del 3,5 %” en marzo")
    assert not fc.quote_in("fue del", "fue del 3")  # demasiado corta
    srcs = [{"url": "https://a.com/1", "text": "uno dos tres cuatro cinco seis siete ocho nueve diez " * 5},
            {"url": "https://a.com/2", "text": "otra cosa distinta"},
            {"url": "https://b.com/1", "text": "uno dos tres cuatro cinco seis siete ocho nueve diez " * 5},
            {"url": "https://c.com/1", "text": "nada que ver con lo anterior en absoluto ni un poco"}]
    fc.group_sources(srcs)
    assert srcs[0]["group"] == srcs[1]["group"] == srcs[2]["group"] and srcs[2].get("republished")
    assert srcs[3]["group"] != srcs[0]["group"]


def test_factcheck_verdict_rules():
    from brain_mcp import factcheck as fc

    p = fc.prefs()
    claim = {"text": "x", "checkable": True, "risk": "normal"}

    def S(st, g, primary=False, **kw):
        return {"stance": st, "group": g, "counted": True, "primary": primary, **kw}

    assert fc.verdict(claim, [S("supports", 1, True), S("supports", 2)], p)["verdict"] == "supported"
    assert fc.verdict(claim, [S("supports", 1), S("supports", 2)], p)["verdict"] == "likely_supported"  # sin primaria
    assert fc.verdict(claim, [S("supports", 1), S("supports", 1)], p)["verdict"] == "likely_supported"  # una sola línea
    assert fc.verdict(claim, [S("supports", 1, True), S("contradicts", 2)], p)["verdict"] == "inconclusive"
    assert fc.verdict(claim, [S("contradicts", 1, True), S("contradicts", 2)], p)["verdict"] == "false"
    assert fc.verdict(claim, [S("contradicts", 1)], p)["verdict"] == "likely_false"
    assert fc.verdict(claim, [S("supports_with_limits", 1)], p)["verdict"] == "mixed"
    assert fc.verdict(claim, [], p)["verdict"] == "inconclusive"
    assert fc.verdict({**claim, "checkable": False}, [], p)["verdict"] == "unverifiable"
    # lo viejo, las redes sociales como pista y las citas que no están en la página no cuentan
    assert fc.verdict(claim, [S("supports", 1, True, stale=True), S("supports", 2, pref="lead_only"),
                              {"stance": "unquoted", "group": 3, "counted": False}], p)["verdict"] == "inconclusive"
    # salud/derecho/finanzas: hace falta una línea más
    assert fc.verdict({**claim, "risk": "high"}, [S("supports", 1, True), S("supports", 2)], p)["verdict"] == "likely_supported"


def test_factcheck_prefs(dash):  # noqa: F811
    from brain_mcp import factcheck as fc

    p = fc.save_prefs({"level": "custom", "min_sources": 3, "max_sources": 2, "regions": "ar, uy", "languages": ["ES", "en"],
                       "blocked_domains": "https://www.spam.com/x\nbad", "brave_key": "secreta", "source_prefs": {"social": "exclude"}})
    assert p["min_sources"] == 3 and p["max_sources"] == 3 and p["regions"] == ["AR", "UY"] and p["languages"] == ["es", "en"]
    assert p["blocked_domains"] == ["spam.com"] and p["has_brave_key"] and "brave_key" not in p
    assert p["source_prefs"]["social"] == "exclude" and p["updated_at"]
    assert fc.save_prefs({"level": "quick"})["min_sources"] == 1  # un nivel fijo pisa sus parámetros
    with pytest.raises(fc.FactCheckError):
        fc.save_prefs({"provider": "searxng", "searxng_url": ""})
    r = fc.save_prefs({"reset": True})
    assert r["level"] == "standard" and r["has_brave_key"]  # restaurar no borra la clave
    api = json.loads(_get(dash + "/api/factcheck/prefs")[1])
    assert "secreta" not in json.dumps(api)
    assert (os.stat(fc._prefs_path()).st_mode & 0o777) == 0o600 or os.name == "nt"


DDG_HTML = """<html><body>
<div class="result results_links"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.indec.gob.ar%2Fdato&rut=x">INDEC dato</a>
<a class="result__snippet">La inflación de marzo fue 3,5 %</a></div>
<div class="result"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Anuncio</a></div>
<div class="result"><a class="result__a" href="https://www.lanacion.com.ar/nota">Nota</a><a class="result__snippet">s</a></div>
</body></html>"""


def test_factcheck_duckduckgo_parser(monkeypatch):
    from brain_mcp import factcheck as fc

    class R:
        text = DDG_HTML

        def raise_for_status(self):
            pass

    seen = {}
    monkeypatch.setattr(fc.requests, "post", lambda url, data, headers, timeout: (seen.update(data), R())[1])
    out = fc.search_web("inflación marzo", fc.prefs(), days=31)
    assert [r["url"] for r in out] == ["https://www.indec.gob.ar/dato", "https://www.lanacion.com.ar/nota"]
    assert seen["kl"] == "ar-es" and seen["df"] == "m"


def test_factcheck_full_run(monkeypatch):
    from brain_mcp import factcheck as fc, scrape
    import server

    pages = {
        "https://www.indec.gob.ar/dato": ("INDEC: la inflación de marzo de 2026 fue de 3,5 % según el índice de precios.", "INDEC"),
        "https://www.lanacion.com.ar/nota": ("Según el INDEC, la inflación de marzo de 2026 fue de 3,5 % y bajó.", "La Nación"),
        "https://www.spam.com/x": ("la inflación de marzo de 2026 fue de 3,5 %", "spam"),
    }

    def gen(prompt, fmt=None):
        if "Separá el texto" in prompt:
            return json.dumps({"claims": [
                {"text": "La inflación de marzo de 2026 fue 3,5 %", "type": "numeric", "checkable": True, "risk": "normal",
                 "topic": "other", "query": "inflación marzo 2026", "entities": ["INDEC"]},
                {"text": "El mejor helado es el de chocolate", "type": "other", "checkable": False, "why_not": "es una opinión"}]})
        if "Compará la AFIRMACIÓN" in prompt:
            primary = "INDEC:" in prompt
            return json.dumps({"stance": "supports", "quote": "la inflación de marzo de 2026 fue de 3,5 %",
                               "explanation": "da el mismo número", "primary": primary, "same_scope": True},
                              ensure_ascii=False)
        return "Dos fuentes independientes, una oficial, dicen 3,5 %."

    monkeypatch.setattr(fc, "generate", gen)
    monkeypatch.setattr(server, "search_core", lambda q, k=5: ([], None, None))
    monkeypatch.setattr(fc, "search_web", lambda q, p, days=None: [{"url": u, "title": t, "snippet": ""} for u, (_, t) in pages.items()])
    monkeypatch.setattr(scrape, "scrape_public", lambda url: {"text": pages[url][0], "title": pages[url][1],
                                                              "meta": {"date": "2026-04-14"}})
    fc.save_prefs({"blocked_domains": ["spam.com"]})
    evs = list(fc.run("La inflación de marzo de 2026 fue 3,5 %. El mejor helado es el de chocolate.", "es"))
    rep = evs[-1]["report"]
    c1, c2 = rep["claims"]
    assert c1["verdict"] == "supported" and c1["decision"] == "web" and "no_local" in c1["reason"] and rep["went_web"]
    assert {s["url"] for s in c1["sources"]} == {"https://www.indec.gob.ar/dato", "https://www.lanacion.com.ar/nota"}  # spam bloqueado
    assert all("text" not in s for s in c1["sources"]) and c1["explanation"]
    assert c2["verdict"] == "unverifiable" and c2["explanation"] == "es una opinión"
    assert fc.get_report(rep["id"])["id"] == rep["id"] and fc.listing()[0]["verdicts"] == ["supported", "unverifiable"]
    md = fc.to_markdown(rep)
    assert "respaldada" in md and "indec.gob.ar" in md and "> la inflación de marzo de 2026 fue de 3,5 %" in md
    # una cita inventada no cuenta
    monkeypatch.setattr(fc, "generate", lambda prompt, fmt=None: gen(prompt).replace("la inflación de marzo de 2026 fue de 3,5 %", "esto no está en la página para nada") if "Compará" in prompt else gen(prompt))
    rep2 = fc.run_sync("La inflación de marzo de 2026 fue 3,5 %.")
    assert rep2["claims"][0]["verdict"] == "inconclusive"
    assert all(s["stance"] == "unquoted" for s in rep2["claims"][0]["sources"])
    # sin internet: solo brain, y lo avisa
    rep3 = fc.run_sync("La inflación de marzo de 2026 fue 3,5 %.", allow_web=False)
    assert rep3["claims"][0]["decision"] == "local_only" and "web_off" in rep3["notes"] and not rep3["went_web"]


def test_factcheck_api_stream(dash, monkeypatch):  # noqa: F811
    from brain_mcp import factcheck as fc

    monkeypatch.setattr(fc, "generate", lambda prompt, fmt=None: None)  # sin modelo
    import server
    monkeypatch.setattr(server, "search_core", lambda q, k=5: ([], None, None))
    out = _post(dash + "/api/factcheck/run", {"text": "El agua hierve a 100 grados al nivel del mar.", "web": False})
    evs = [json.loads(x) for x in out.decode().splitlines()]
    assert evs[0]["type"] == "start" and evs[-1]["type"] == "done"
    rid = evs[-1]["report"]["id"]
    assert "no_model" in evs[-1]["report"]["notes"]
    got = json.loads(_get(dash + f"/api/factcheck/report?id={rid}")[1])
    assert got["ok"] and got["markdown"].startswith("# Fact check")
    assert json.loads(_post(dash + "/api/factcheck/block", {"domain": "malo.com"}))["prefs"]["blocked_domains"] == ["malo.com"]
    assert json.loads(_post(dash + "/api/factcheck/save_source", {"url": "file:///etc/passwd"}))["code"] == "bad_url"
    assert json.loads(_post(dash + "/api/factcheck/delete", {"id": rid}))["ok"]


def test_factcheck_never_opens_local_addresses():
    from brain_mcp import factcheck as fc

    s = fc.fetch({"url": "http://127.0.0.1:8765/api/status", "title": "x"})
    assert not s["inspected"] and "públicas" in s["error"]
