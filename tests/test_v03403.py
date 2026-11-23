"""v0.03.4.3: una nota rara (un link larguísimo, un tag que no es lista, una carpeta llamada x.md) ya no deja en
blanco el grafo ni el Panel, y el grafo no depende de un CDN."""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LONG = "x" * 300  # un tramo de más de 255 bytes: Path.is_dir() levantaba OSError «File name too long»


@pytest.mark.parametrize("body", [f"[[{LONG}]]", f"[a]({LONG}.md)", f"`projects/{LONG}`", f"`{LONG}/{LONG}`"])
def test_long_link_does_not_break_graph(body):
    from brain_mcp import graph, stats, vault

    vault.write_file("projects/larga.md", f"---\nname: larga\ndescription: x\n---\n{body}\n[[otra]]\n")
    vault.write_file("projects/otra.md", "---\nname: otra\ndescription: y\n---\nhola\n")
    g = graph.build_graph()
    ids = {n["id"] for n in g["nodes"]}
    assert {"projects/larga.md", "projects/otra.md"} <= ids
    assert any(l["source"] == "projects/larga.md" and l["target"] == "projects/otra.md" for l in g["links"])
    assert g["skipped"] == []
    assert stats.vault_stats()["files"] >= 2


def test_odd_notes_are_skipped_not_fatal():
    from brain_mcp import graph, stats, vault

    v = vault.VAULT
    (v / "projects" / "carpeta.md").mkdir()  # una carpeta con nombre de nota
    (v / "projects" / "tags.md").write_text("---\nname: tags\ntags: 5\nrelated: {a: 1}\nentities: 3\nurl: 7\n---\nx\n")
    (v / "projects" / "bytes.md").write_bytes(b"---\nname: bytes\n---\n\xff\xfe texto")
    (v / "projects" / "lista.md").write_text("---\n- a\n- b\n---\nfrontmatter que es una lista\n")
    g = graph.build_graph()
    ids = {n["id"] for n in g["nodes"]}
    assert {"projects/tags.md", "projects/bytes.md", "projects/lista.md"} <= ids
    assert "projects/carpeta.md" not in ids
    assert "tag:5" in ids and next(n for n in g["nodes"] if n["id"] == "projects/tags.md")["url"] is None
    json.dumps(g)
    assert stats.vault_stats()["skipped"] == 0


def test_a_note_that_fails_is_reported(monkeypatch):
    from brain_mcp import graph, stats, vault

    vault.write_file("projects/ok.md", "---\nname: ok\n---\nbien\n")
    vault.write_file("projects/mala.md", "---\nname: mala\n---\nmal\n")
    real = graph._note_links

    def boom(rel, *a):
        if rel == "projects/mala.md":
            raise RuntimeError("nota rota")
        return real(rel, *a)
    monkeypatch.setattr(graph, "_note_links", boom)
    g = graph.build_graph()
    assert g["skipped"] == [{"path": "projects/mala.md", "error": "RuntimeError: nota rota"}]
    assert "projects/ok.md" in {n["id"] for n in g["nodes"]}
    assert stats.vault_stats()["skipped"] == 1


def test_graph_and_chat_libraries_are_bundled():
    import dashboard

    for page in ("graph.html", "chat.html"):
        html = (ROOT / "brain_mcp" / page).read_text("utf-8")
        assert "jsdelivr" not in html and "unpkg" not in html
        for src in re.findall(r'<script src="([^"]+)"', html):
            if src.startswith("/vendor/"):
                assert src in dashboard.VENDOR
                assert (ROOT / "brain_mcp" / "vendor" / dashboard.VENDOR[src]).stat().st_size > 10_000
