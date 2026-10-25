"""v0.03.3.4: vista Prompts (brain_mcp/prompts.json, /api/prompts) y su página de docs. Sin red."""
import json
import re
import sys
from pathlib import Path

from tests.test_v0331 import _get, dash  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent


def test_prompts_api_shape(dash):
    data = _get(dash + "/api/prompts")
    ids = [it["id"] for g in data["groups"] for it in g["items"]]
    assert len(ids) == len(set(ids)) >= 10 and {"project", "self-config", "central", "web"} <= set(ids)
    for g in data["groups"]:
        assert g["title"]["es"] and g["title"]["en"] and g["intro"]["es"] and g["intro"]["en"]
        for it in g["items"]:
            assert it["text"]["es"].strip() and it["text"]["en"].strip() and it["tools"]
            assert isinstance(it["tested"], list)


def test_prompts_in_docs_and_dashboard():
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_prompts_page

    assert build_prompts_page.main(check=True) == 0  # la página de docs sale de prompts.json
    html = (ROOT / "brain_mcp" / "dashboard.html").read_text("utf-8")
    assert 'data-view="prompts"' in html and 'id="view-prompts"' in html
    assert len(re.findall(r'"prompts"', html)) >= 3  # NAV_DEFAULT, VIEWS y go()
    assert "prompts:" in html.split("const I18N")[1]
    data = json.loads((ROOT / "brain_mcp" / "prompts.json").read_text("utf-8"))
    web = next(it for g in data["groups"] for it in g["items"] if it["id"] == "web")
    assert "web_search" in web["text"]["es"] and "web_search" in web["text"]["en"]
