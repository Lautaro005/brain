"""v0.03.3.6: los archivos centrales no se enlazan desde otras notas; botones y links del grafo; notch sin huecos."""
from pathlib import Path

import pytest


def _note(name, body="hola", extra=""):
    return f"---\nname: {name}\ndescription: x\n{extra}---\n{body}\n"


def test_graph_ignores_links_to_core_files():
    from brain_mcp import graph, vault

    vault.ensure_brain_note() if hasattr(vault, "ensure_brain_note") else None
    vault.write_file("projects/uno.md", _note("uno", "ver [[brain]] y [FORMAT](FORMAT.md) y `BRAIN.md`", "related: [FORMAT]\n"))
    g = graph.build_graph()
    pairs = {(l["source"], l["target"]) for l in g["links"]} | {(l["target"], l["source"]) for l in g["links"]}
    for core in vault.CORE_FILES:
        assert ("projects/uno.md", core) not in pairs
    # la estructura propia sigue: BRAIN.md → FORMAT.md
    assert any(l["kind"] == "core" for l in g["links"])


def test_proposal_cannot_target_core():
    from brain_mcp import graph_proposals, vault

    vault.write_file("projects/brasa.md", _note("brasa"))
    with pytest.raises(graph_proposals.ProposalError) as e:
        graph_proposals.propose([{"type": "add_link", "source": "brasa", "target": "FORMAT.md"}])
    assert "archivo central" in (e.value.detail or str(e.value))


def test_graph_page_has_back_button_and_safe_links():
    html = (Path(__file__).resolve().parent.parent / "brain_mcp" / "graph.html").read_text(encoding="utf-8")
    assert 'id="back"' in html and "function goBack" in html
    assert "function wireLinks" in html and "ev.preventDefault()" in html


def test_notch_lines_without_verb_have_no_empty_column():
    js = (Path(__file__).resolve().parent.parent / "apps/desktop/ui/notch.js").read_text(encoding="utf-8")
    assert 'li.className = "plain"' in js
